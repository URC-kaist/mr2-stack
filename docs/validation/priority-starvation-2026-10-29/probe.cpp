#include "/home/mr2/mr2-stack/rover/ros2_ws/src/mr2_video_streaming/src/rtp_send_time.hpp"
#include "/home/mr2/mr2-stack/rover/ros2_ws/src/mr2_video_streaming/src/video_control.hpp"
#include <nlohmann/json.hpp>
#include <iostream>
#include <sstream>
#include <vector>

struct Stats {
  rtp_send_time::Pacer pacer;
  int frames = 0, admitted = 0, keys = 0, keys_admitted = 0, blocked_deltas = 0;
  int packets = 0;
  uint64_t wire_bytes = 0;
  std::vector<uint64_t> key_sizes;
};
static GstPadProbeReturn gate(GstPad * pad, GstPadProbeInfo * info, gpointer data) {
  auto & s = *static_cast<Stats *>(data);
  if (!(GST_PAD_PROBE_INFO_TYPE(info) & GST_PAD_PROBE_TYPE_BUFFER))
    return rtp_send_time::gate(pad, info, &s.pacer);
  GstBuffer * b = GST_PAD_PROBE_INFO_BUFFER(info);
  const bool delta = GST_BUFFER_FLAG_IS_SET(b, GST_BUFFER_FLAG_DELTA_UNIT);
  ++s.frames;
  if (!delta) { ++s.keys; s.key_sizes.push_back(gst_buffer_get_size(b)); }
  if (delta && s.pacer.recovering) ++s.blocked_deltas;
  const auto result = rtp_send_time::gate(pad, info, &s.pacer);
  if (result == GST_PAD_PROBE_OK) { ++s.admitted; if (!delta) ++s.keys_admitted; }
  return result;
}
static GstPadProbeReturn send(GstPad * pad, GstPadProbeInfo * info, gpointer data) {
  auto & s = *static_cast<Stats *>(data);
  if (GST_PAD_PROBE_INFO_TYPE(info) & GST_PAD_PROBE_TYPE_BUFFER) {
    ++s.packets; s.wire_bytes += gst_buffer_get_size(GST_PAD_PROBE_INFO_BUFFER(info)) + 52;
  }
  return rtp_send_time::probe(pad, info, &s.pacer);
}
int main(int argc, char ** argv) {
  if (argc < 5) return 2;
  const int wire = std::stoi(argv[1]), bitrate = int(wire * .85), vbv = std::stoi(argv[2]);
  const int seconds = std::stoi(argv[4]);
  const int update_ms = argc > 5 ? std::stoi(argv[5]) : 0;
  const auto profile = video_control::profile(bitrate, 640, 360, 30);
  gst_init(nullptr, nullptr);
  std::ostringstream pipeline;
  pipeline << "videotestsrc name=src is-live=true pattern=" << argv[3]
    << " ! video/x-raw,width=" << profile.width << ",height=" << profile.height << ",framerate=" << profile.fps << "/1"
    << " ! nvvidconv ! video/x-raw(memory:NVMM),format=NV12"
    << " ! nvv4l2h264enc name=latency_encoder copy-timestamp=true bitrate=" << bitrate * 1000
    << " vbv-size=" << vbv << " idrinterval=30 iframeinterval=30 insert-sps-pps=true maxperf-enable=true control-rate=1"
    << " ! video/x-h264,stream-format=byte-stream,alignment=au,level=(string)3.1"
    << " ! h264parse config-interval=1 ! rtph264pay name=latency_pay pt=96 mtu=1176 config-interval=1"
    << " ! udpsink name=video_udp host=127.0.0.1 port=53982 sync=false async=false";
  GError * error = nullptr;
  auto * p = gst_parse_launch(pipeline.str().c_str(), &error);
  if (!p || error) return 3;
  Stats s; s.pacer.kbps = wire;
  auto * pay = gst_bin_get_by_name(GST_BIN(p), "latency_pay");
  auto * paypad = gst_element_get_static_pad(pay, "sink");
  gst_pad_add_probe(paypad, GstPadProbeType(GST_PAD_PROBE_TYPE_BUFFER | GST_PAD_PROBE_TYPE_EVENT_DOWNSTREAM), gate, &s, nullptr);
  auto * sink = gst_bin_get_by_name(GST_BIN(p), "video_udp");
  auto * sinkpad = gst_element_get_static_pad(sink, "sink");
  gst_pad_add_probe(sinkpad, GstPadProbeType(GST_PAD_PROBE_TYPE_BUFFER | GST_PAD_PROBE_TYPE_BUFFER_LIST), send, &s, nullptr);
  auto * encoder = gst_bin_get_by_name(GST_BIN(p), "latency_encoder");
  auto * bus = gst_element_get_bus(p);
  gst_element_set_state(p, GST_STATE_PLAYING);
  auto started = rtp_send_time::mono_us(), last_keyframe = started, last_update = started;
  int updates = 0;
  bool failed = false;
  while (rtp_send_time::mono_us() - started < uint64_t(seconds) * 1000000) {
    if (update_ms && rtp_send_time::mono_us() - last_update >= uint64_t(update_ms) * 1000) {
      g_object_set(encoder, "bitrate", guint(bitrate * 1000), nullptr);
      last_update = rtp_send_time::mono_us(); ++updates;
    }
    if (s.pacer.request_keyframe && rtp_send_time::mono_us() - last_keyframe > 1000000) {
      s.pacer.request_keyframe = false; last_keyframe = rtp_send_time::mono_us();
      rtp_send_time::force_keyframe(encoder);
    }
    auto * msg = gst_bus_timed_pop_filtered(bus, 20 * GST_MSECOND, GstMessageType(GST_MESSAGE_ERROR | GST_MESSAGE_EOS));
    if (msg) {
      if (GST_MESSAGE_TYPE(msg) == GST_MESSAGE_ERROR) {
        gchar * debug = nullptr; gst_message_parse_error(msg, &error, &debug);
        std::cerr << error->message << ' ' << (debug ? debug : "") << '\n';
        g_error_free(error); g_free(debug); failed = true;
      }
      gst_message_unref(msg); break;
    }
  }
  s.pacer.stopping = true; gst_element_set_state(p, GST_STATE_NULL);
  const auto json = nlohmann::json{{"wire_kbps", wire}, {"encoder_kbps", bitrate}, {"vbv_bits", vbv},
    {"width", profile.width}, {"height", profile.height}, {"fps", profile.fps}, {"pattern", argv[3]},
    {"frames_encoded", s.frames}, {"frames_admitted", s.admitted}, {"keys_encoded", s.keys},
    {"keys_admitted", s.keys_admitted}, {"deltas_blocked_recovering", s.blocked_deltas},
    {"key_sizes_bytes", s.key_sizes}, {"rtp_packets", s.packets},
    {"sent_kbps", s.wire_bytes * 8.0 / (seconds * 1000)},
    {"pacer_frame_window_us", s.pacer.frame_deadline_us(false)}, {"bitrate_updates", updates}, {"update_interval_ms", update_ms}, {"failed", failed}};
  std::cout << json.dump() << '\n';
  gst_object_unref(bus); gst_object_unref(paypad); gst_object_unref(pay); gst_object_unref(sinkpad);
  gst_object_unref(sink); gst_object_unref(encoder); gst_object_unref(p);
  return failed ? 1 : 0;
}
