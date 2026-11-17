#pragma once

#include <gst/gst.h>
#include <gst/rtp/gstrtpbuffer.h>
#include <nlohmann/json.hpp>
#include <chrono>
#include <cstdlib>
#include <map>
#include <mutex>
#include <string>
#include <vector>
#include <sys/socket.h>
#include <sys/un.h>
#include <unistd.h>

// Passive, bounded probes. Video never waits for a diagnostics consumer.
// Match by running-time PTS, not frame order: queues may drop frames and
// software encoders may shift PTS/segment origins by a fixed offset.
class FrameTiming
{
public:
  explicit FrameTiming(std::string stream_id)
  : stream_id_(std::move(stream_id))
  {
    socket_ = socket(AF_UNIX, SOCK_DGRAM | SOCK_NONBLOCK | SOCK_CLOEXEC, 0);
    destination_.sun_family = AF_UNIX;
    const auto * custom = std::getenv("MR2_VIDEO_TIMING_SOCKET");
    const auto name = custom && *custom ? std::string(custom) :
      std::string("mr2-video-timing-") + std::to_string(getuid());
    name.copy(destination_.sun_path + 1, sizeof(destination_.sun_path) - 2);
    destination_size_ = offsetof(sockaddr_un, sun_path) + 1 + name.size();
  }
  ~FrameTiming()
  {
    for (auto & probe : probes_) {
      gst_pad_remove_probe(probe.pad, probe.id);
      gst_object_unref(probe.pad);
    }
    if (socket_ >= 0) {close(socket_);}
  }
  void attach(GstElement * pipeline)
  {
    add(pipeline, "src", "src", ingest_probe);
    add(pipeline, "latency_encoder", "sink", encode_in_probe);
    add(pipeline, "latency_encoder", "src", encode_out_probe);
    add(pipeline, "latency_pay", "src", rtp_probe);
  }
  void detach(GstElement * pipeline)
  {
    // The encoder pipeline is NULL before detach; the capture probe remains
    // active on the same collector while the encoder generation is replaced.
    for (auto it = probes_.begin(); it != probes_.end(); ) {
      if (it->owner == pipeline) {
        gst_pad_remove_probe(it->pad, it->id); gst_object_unref(it->pad);
        it = probes_.erase(it);
      } else {++it;}
    }
  }

private:
  struct Stamp { int64_t mono_us{0}; int64_t epoch_us{0}; };
  struct Frame { Stamp ingest; Stamp encode_in; Stamp encode_out; };
  struct Probe { GstPad * pad; gulong id; GstElement * owner; };
  std::string stream_id_;
  int socket_{-1};
  sockaddr_un destination_{};
  socklen_t destination_size_{0};
  std::mutex mutex_;
  std::map<GstClockTime, Frame> frames_;
  std::vector<Probe> probes_;
  static Stamp now()
  {
    return {std::chrono::duration_cast<std::chrono::microseconds>(
        std::chrono::steady_clock::now().time_since_epoch()).count(),
      std::chrono::duration_cast<std::chrono::microseconds>(
        std::chrono::system_clock::now().time_since_epoch()).count()};
  }
  static GstClockTime running_pts(GstPad * pad, GstBuffer * buffer)
  {
    if (!GST_BUFFER_PTS_IS_VALID(buffer)) {return GST_CLOCK_TIME_NONE;}
    GstEvent * event = gst_pad_get_sticky_event(pad, GST_EVENT_SEGMENT, 0);
    if (!event) {return GST_CLOCK_TIME_NONE;}
    const GstSegment * segment = nullptr;
    gst_event_parse_segment(event, &segment);
    const auto result = segment && segment->format == GST_FORMAT_TIME ?
      gst_segment_to_running_time(segment, GST_FORMAT_TIME, GST_BUFFER_PTS(buffer)) :
      GST_CLOCK_TIME_NONE;
    gst_event_unref(event);
    return result;
  }
  void add(
    GstElement * pipeline, const char * element_name, const char * pad_name,
    GstPadProbeCallback callback)
  {
    GstElement * element = gst_bin_get_by_name(GST_BIN(pipeline), element_name);
    if (!element) {return;}
    GstPad * pad = gst_element_get_static_pad(element, pad_name);
    gst_object_unref(element);
    if (!pad) {return;}
    auto flags =
      static_cast<GstPadProbeType>(GST_PAD_PROBE_TYPE_BUFFER | GST_PAD_PROBE_TYPE_BUFFER_LIST);
    probes_.push_back({pad, gst_pad_add_probe(pad, flags, callback, this, nullptr), pipeline});
  }
  void record(GstPad * pad, GstBuffer * buffer, int stage)
  {
    const Stamp stamp = now();
    const auto pts = running_pts(pad, buffer);
    if (!GST_CLOCK_TIME_IS_VALID(pts)) {return;}
    std::lock_guard<std::mutex> guard(mutex_);
    if (stage == 0) {
      frames_[pts] = Frame{stamp, {}, {}};
      while (frames_.size() > 256) {frames_.erase(frames_.begin());}
      return;
    }
    auto found = frames_.find(pts);
    if (found == frames_.end()) {return;}
    if (stage == 1) {found->second.encode_in = stamp;}
    if (stage == 2 && !found->second.encode_out.mono_us) {found->second.encode_out = stamp;}
    if (stage != 3) {return;}
    GstRTPBuffer rtp = GST_RTP_BUFFER_INIT;
    if (!gst_rtp_buffer_map(buffer, GST_MAP_READ, &rtp)) {return;}
    if (gst_rtp_buffer_get_marker(&rtp)) {
      const Frame & frame = found->second;
      if (frame.ingest.mono_us && frame.encode_in.mono_us >= frame.ingest.mono_us &&
        frame.encode_out.mono_us >= frame.encode_in.mono_us &&
        stamp.mono_us >= frame.encode_out.mono_us)
      {
        const auto sample = nlohmann::json{
          {"schema_version", 1}, {"stream_id", stream_id_},
          {"ssrc", gst_rtp_buffer_get_ssrc(&rtp)},
          {"rtp_timestamp", gst_rtp_buffer_get_timestamp(&rtp)},
          {"marker_sequence", gst_rtp_buffer_get_seq(&rtp)},
          {"ingest_epoch_us", frame.ingest.epoch_us},
          {"pay_epoch_us", stamp.epoch_us},
          {"ingest_to_pay_us", stamp.mono_us - frame.ingest.mono_us},
          {"encode_us", frame.encode_out.mono_us - frame.encode_in.mono_us}
        }.dump();
        if (socket_ >= 0) {
          sendto(
            socket_, sample.data(), sample.size(), MSG_DONTWAIT | MSG_NOSIGNAL,
            reinterpret_cast<const sockaddr *>(&destination_), destination_size_);
        }
      }
      frames_.erase(found);
    }
    gst_rtp_buffer_unmap(&rtp);
  }
  static GstPadProbeReturn dispatch(GstPad * pad, GstPadProbeInfo * info, gpointer data, int stage)
  {
    auto * self = static_cast<FrameTiming *>(data);
    if (GST_PAD_PROBE_INFO_TYPE(info) & GST_PAD_PROBE_TYPE_BUFFER) {
      self->record(pad, GST_PAD_PROBE_INFO_BUFFER(info), stage);
    } else if (GST_PAD_PROBE_INFO_TYPE(info) & GST_PAD_PROBE_TYPE_BUFFER_LIST) {
      GstBufferList * list = GST_PAD_PROBE_INFO_BUFFER_LIST(info);
      for (guint i = 0; i < gst_buffer_list_length(list); ++i) {
        self->record(pad, gst_buffer_list_get(list, i), stage);
      }
    }
    return GST_PAD_PROBE_OK;
  }
  static GstPadProbeReturn ingest_probe(GstPad * p, GstPadProbeInfo * i, gpointer d)
  {
    return dispatch(p, i, d, 0);
  }
  static GstPadProbeReturn encode_in_probe(GstPad * p, GstPadProbeInfo * i, gpointer d)
  {
    return dispatch(p, i, d, 1);
  }
  static GstPadProbeReturn encode_out_probe(GstPad * p, GstPadProbeInfo * i, gpointer d)
  {
    return dispatch(p, i, d, 2);
  }
  static GstPadProbeReturn rtp_probe(GstPad * p, GstPadProbeInfo * i, gpointer d)
  {
    return dispatch(p, i, d, 3);
  }
};
