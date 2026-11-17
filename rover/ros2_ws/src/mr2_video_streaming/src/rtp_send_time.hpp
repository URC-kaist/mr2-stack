#pragma once
#include <gst/gst.h>
#include <gst/rtp/gstrtpbuffer.h>
#include <gst/video/video-event.h>
#include <chrono>
#include <atomic>
#include <thread>
#include <algorithm>
#include <cstdint>
#include "shared_wire_clock.hpp"

// Private RFC 8285 two-byte extension: ID 7, monotonic and epoch microseconds.
// RTP sequence + SSRC identify the packet; monotonic clocks drive delay trends.
namespace rtp_send_time
{
constexpr guint8 kId = 7;
inline void force_keyframe(GstElement * encoder)
{
  if (g_signal_lookup("force-IDR", G_OBJECT_TYPE(encoder))) {
    g_signal_emit_by_name(encoder, "force-IDR");
  } else {
    gst_element_send_event(
      encoder,
      gst_video_event_new_upstream_force_key_unit(GST_CLOCK_TIME_NONE, TRUE, 0));
  }
}
inline uint64_t mono_us()
{
  return std::chrono::duration_cast<std::chrono::microseconds>(
    std::chrono::steady_clock::now().time_since_epoch()).count();
}
inline uint64_t epoch_us()
{
  return std::chrono::duration_cast<std::chrono::microseconds>(
    std::chrono::system_clock::now().time_since_epoch()).count();
}
struct Pacer
{
  SharedWireClock wire;
  std::atomic<int> kbps{0};
  std::atomic<bool> stopping{false}, request_keyframe{false};
  std::atomic<uint32_t> encoded_frames{0}, admitted_frames{0};
  std::atomic<uint64_t> frame_period_us{33333};
  // Admission and packet pacing run on separate bounded-queue tasks.
  std::atomic<uint64_t> next_us{0};
  bool recovering{false};
  void set_framerate(int numerator, int denominator)
  {
    if (numerator > 0 && denominator > 0) {
      frame_period_us = uint64_t(1000000) * denominator / numerator;
    }
  }
  uint64_t frame_deadline_us(bool delta) const
  {
    // At low FPS each frame contains more bits at the same average bitrate.
    // Delta frames have a 150 ms floor and 20% source-period margin. IDRs may
    // use 300 ms so an otherwise healthy low-rate stream can start/recover.
    const auto source_window = delta ? frame_period_us * 6 / 5 : 2 * frame_period_us;
    return std::clamp<uint64_t>(source_window, delta ? 150000 : 300000, 300000);
  }
  bool admit_frame(uint64_t size, bool delta, uint64_t now, uint64_t age_us = 0)
  {
    ++encoded_frames;
    const int rate = kbps.load();
    if (rate <= 0) {return true;}
    if (wire.available() && wire.rate() <= 0) {return false;}
    if (recovering && delta) {return false;}
    const uint64_t wire_size = size + ((size + 1099) / 1100) * 52 + 128;
    // Drop entire access units before RTP sequence assignment. Never send a
    // partial H.264 frame or misclassify intentional skips as network loss.
    const auto stream_next = next_us.load();
    uint64_t finish = std::max(stream_next, now) + wire_size * 8000 / rate;
    if (wire.available() && wire.rate() > 0) {
      const auto burst = std::clamp<uint64_t>(2 * frame_period_us, 30000, 100000);
      const auto local_debt = stream_next > now ? stream_next - now : 0;
      const auto local_time = local_debt + wire_size * 8000 / rate;
      finish = now + std::max(
        local_time > burst ? local_time - burst : 0,
        wire.backlog(now) + wire_size * 8000 / wire.rate());
    }
    const auto deadline = frame_deadline_us(delta);
    if (age_us > deadline || finish - now > deadline - age_us) {
      recovering = true; request_keyframe = true; return false;
    }
    recovering = false;
    ++admitted_frames;
    return true;
  }
  void reset()
  {
    next_us = 0; recovering = false; stopping = false; request_keyframe = false;
    encoded_frames = 0; admitted_frames = 0;
  }
  bool admit(GstBuffer * buffer)
  {
    return admit_frame(
      gst_buffer_get_size(buffer),
      GST_BUFFER_FLAG_IS_SET(buffer, GST_BUFFER_FLAG_DELTA_UNIT), mono_us());
  }
  bool wait(GstBuffer * buffer)
  {
    const int rate = kbps.load();
    if (rate <= 0) {return true;}
    if (wire.available() && wire.rate() <= 0) {return false;}
    auto now = mono_us();
    const auto stream_next = next_us.load();
    uint64_t ready = stream_next;
    if (wire.available() && wire.rate() > 0) {
      const auto burst = std::clamp<uint64_t>(2 * frame_period_us, 30000, 100000);
      ready = stream_next > burst ? stream_next - burst : 0;
      while (!stopping && ready > now) {
        std::this_thread::sleep_for(
          std::chrono::microseconds(
            std::min<uint64_t>(
              1000,
              ready - now)));
        now = mono_us();
      }
      if (stopping) {return false;}
      // Do not reserve the global link while waiting for this stream's tokens.
      ready = wire.reserve(gst_buffer_get_size(buffer) + 52, now);
      if (ready == UINT64_MAX) {return false;}
    }
    while (!stopping && ready > now) {
      std::this_thread::sleep_for(std::chrono::microseconds(std::min<uint64_t>(1000, ready - now)));
      now = mono_us();
    }
    if (stopping) {return false;}
    next_us =
      std::max(stream_next, now) + (gst_buffer_get_size(buffer) + 52) * 8000 / std::max(1, rate);
    return true;
  }
};
inline uint64_t frame_age_us(GstPad * pad, GstBuffer * buffer)
{
  if (!GST_BUFFER_PTS_IS_VALID(buffer)) {return 0;}
  auto * event = gst_pad_get_sticky_event(pad, GST_EVENT_SEGMENT, 0);
  if (!event) {return 0;}
  const GstSegment * segment = nullptr;
  gst_event_parse_segment(event, &segment);
  const auto pts = segment && segment->format == GST_FORMAT_TIME ?
    gst_segment_to_running_time(
    segment, GST_FORMAT_TIME,
    GST_BUFFER_PTS(buffer)) : GST_CLOCK_TIME_NONE;
  gst_event_unref(event);
  auto * element = gst_pad_get_parent_element(pad);
  if (!element) {return 0;}
  auto * clock = gst_element_get_clock(element);
  const auto base = gst_element_get_base_time(element);
  const auto now = clock ? gst_clock_get_time(clock) : 0;
  if (clock) {gst_object_unref(clock);}
  gst_object_unref(element);
  return GST_CLOCK_TIME_IS_VALID(pts) && now >= base &&
         now - base >= pts ? (now - base - pts) / 1000 : 0;
}
inline GstPadProbeReturn raw_gate(GstPad * pad, GstPadProbeInfo * info, gpointer data)
{
  auto * pacer = static_cast<Pacer *>(data);
  // Before encoding, skipped source images cannot break H.264 references.
  return frame_age_us(pad, GST_PAD_PROBE_INFO_BUFFER(info)) >
         std::max<uint64_t>(
    100000,
    2 * pacer->frame_period_us) ? GST_PAD_PROBE_DROP : GST_PAD_PROBE_OK;
}
inline GstPadProbeReturn gate(GstPad * pad, GstPadProbeInfo * info, gpointer data)
{
  auto * pacer = static_cast<Pacer *>(data);
  if (GST_PAD_PROBE_INFO_TYPE(info) & GST_PAD_PROBE_TYPE_EVENT_DOWNSTREAM) {
    auto * event = GST_PAD_PROBE_INFO_EVENT(info);
    if (GST_EVENT_TYPE(event) == GST_EVENT_CAPS) {
      GstCaps * caps = nullptr;
      gst_event_parse_caps(event, &caps);
      int numerator = 0, denominator = 0;
      if (caps && !gst_caps_is_empty(caps) &&
        gst_structure_get_fraction(
          gst_caps_get_structure(caps, 0), "framerate", &numerator,
          &denominator))
      {
        pacer->set_framerate(numerator, denominator);
      }
    }
    return GST_PAD_PROBE_OK;
  }
  auto * buffer = GST_PAD_PROBE_INFO_BUFFER(info);
  const bool delta = GST_BUFFER_FLAG_IS_SET(buffer, GST_BUFFER_FLAG_DELTA_UNIT);
  if (frame_age_us(pad, buffer) > pacer->frame_deadline_us(delta)) {
    pacer->recovering = true; pacer->request_keyframe = true; return GST_PAD_PROBE_DROP;
  }
  return pacer->admit_frame(
    gst_buffer_get_size(buffer), delta, mono_us(),
    frame_age_us(pad, buffer)) ? GST_PAD_PROBE_OK : GST_PAD_PROBE_DROP;
}
inline GstBuffer * stamp(GstBuffer * buffer)
{
  buffer = gst_buffer_make_writable(buffer);
  GstRTPBuffer rtp = GST_RTP_BUFFER_INIT;
  if (!gst_rtp_buffer_map(buffer, GST_MAP_READWRITE, &rtp)) {return buffer;}
  guint8 data[16];
  GST_WRITE_UINT64_BE(data, mono_us());
  GST_WRITE_UINT64_BE(data + 8, epoch_us());
  gst_rtp_buffer_add_extension_twobytes_header(&rtp, 0, kId, data, sizeof(data));
  gst_rtp_buffer_unmap(&rtp);
  return buffer;
}
inline GstPadProbeReturn probe(GstPad * pad, GstPadProbeInfo * info, gpointer data)
{
  auto * pacer = static_cast<Pacer *>(data);
  if (GST_PAD_PROBE_INFO_TYPE(info) & GST_PAD_PROBE_TYPE_BUFFER) {
    if (pacer && !pacer->wait(GST_PAD_PROBE_INFO_BUFFER(info))) {return GST_PAD_PROBE_DROP;}
    GST_PAD_PROBE_INFO_DATA(info) = stamp(GST_PAD_PROBE_INFO_BUFFER(info));
  } else if (GST_PAD_PROBE_INFO_TYPE(info) & GST_PAD_PROBE_TYPE_BUFFER_LIST) {
    auto * list = GST_PAD_PROBE_INFO_BUFFER_LIST(info);
    // udpsink accepts lists. Waiting inside a list probe would still send the
    // whole list in a burst after the final wait. Chain individual buffers on
    // this sink pad instead; its BUFFER probe paces/stamps each actual send.
    for (guint i = 0; i < gst_buffer_list_length(list); ++i) {
      if (gst_pad_chain(pad, gst_buffer_ref(gst_buffer_list_get(list, i))) != GST_FLOW_OK) {break;}
    }
    return GST_PAD_PROBE_DROP;
  }
  return GST_PAD_PROBE_OK;
}
inline GstPad * attach(GstElement * pipeline, Pacer * pacer = nullptr)
{
  if (pacer) {
    auto * encoder = gst_bin_get_by_name(GST_BIN(pipeline), "latency_encoder");
    if (encoder) {
      auto * pad = gst_element_get_static_pad(encoder, "sink");
      gst_pad_add_probe(pad, GST_PAD_PROBE_TYPE_BUFFER, raw_gate, pacer, nullptr);
      gst_object_unref(pad); gst_object_unref(encoder);
    }
    auto * pay = gst_bin_get_by_name(GST_BIN(pipeline), "latency_pay");
    if (pay) {
      auto * pad = gst_element_get_static_pad(pay, "sink");
      gst_pad_add_probe(
        pad,
        GstPadProbeType(
          GST_PAD_PROBE_TYPE_BUFFER | GST_PAD_PROBE_TYPE_EVENT_DOWNSTREAM), gate, pacer, nullptr);
      gst_object_unref(pad); gst_object_unref(pay);
    }
  }
  auto * sink = gst_bin_get_by_name(GST_BIN(pipeline), "video_udp");
  if (!sink) {return nullptr;}
  auto * pad = gst_element_get_static_pad(sink, "sink");
  gst_object_unref(sink);
  gst_pad_add_probe(
    pad, GstPadProbeType(
      GST_PAD_PROBE_TYPE_BUFFER | GST_PAD_PROBE_TYPE_BUFFER_LIST), probe, pacer, nullptr);
  return pad;
}
}  // namespace rtp_send_time
