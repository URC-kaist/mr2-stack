#pragma once
#include <gst/app/gstappsink.h>
#include <gst/app/gstappsrc.h>
#include <mutex>
#include <sstream>
#include <vector>
#include <stdexcept>
#include "sender_control.hpp"

// One persistent device/FD; only the encoder generation changes. Raw capture
// never waits for the encoder or socket. Both pipelines share one clock origin.
class CaptureSender
{
  std::string id_, source_caps_, host_, preset_{"ultrafast"}, tune_{"zerolatency"};
  int port_{0}, control_{-1};
  GstElement * capture_{nullptr}, *encoded_{nullptr}, *input_{nullptr}, *encoder_{nullptr};
  GstBus * capture_bus_{nullptr}, *encoded_bus_{nullptr};
  GstPad * stamp_{nullptr};
  GstClock * clock_{gst_system_clock_obtain()};
  GstClockTime origin_{gst_clock_get_time(clock_)};
  std::mutex mutex_;
  GstCaps * capture_caps_{nullptr};
  FrameTiming timing_;
  rtp_send_time::Pacer pacer_;
  SenderControl current_;
  static GstElement * parse(std::vector<std::string> args)
  {
    std::vector<const gchar *> v;
    for (const auto & a : args) {v.push_back(a.c_str());}
    v.push_back(nullptr);
    GError * error = nullptr;
    auto * p = gst_parse_launchv(v.data(), &error);
    if (error || !p) {
      const std::string message = error ? error->message : "invalid camera pipeline";
      if (error) {g_error_free(error);}
      if (p) {gst_object_unref(p);}
      throw std::runtime_error(message);
    }
    return p;
  }
  void clock_pipeline(GstElement * p)
  {
    gst_pipeline_use_clock(GST_PIPELINE(p), clock_);
    gst_element_set_start_time(p, GST_CLOCK_TIME_NONE);
    gst_element_set_base_time(p, origin_);
  }
  static GstFlowReturn sample(GstAppSink * sink, gpointer data)
  {
    auto * self = static_cast<CaptureSender *>(data);
    auto * sample = gst_app_sink_pull_sample(sink);
    if (!sample) {return GST_FLOW_EOS;}
    {
      std::lock_guard<std::mutex> guard(self->mutex_);
      auto * caps = gst_sample_get_caps(sample);
      if (caps && (!self->capture_caps_ || !gst_caps_is_equal(caps, self->capture_caps_))) {
        if (self->capture_caps_) {gst_caps_unref(self->capture_caps_);}
        self->capture_caps_ = gst_caps_ref(caps);
        if (self->input_) {gst_app_src_set_caps(GST_APP_SRC(self->input_), caps);}
      }
      if (self->input_) {
        // Appsrc has a one-buffer downstream-leaky queue; push cannot accumulate
        // device buffers during encoding or a reconfiguration.
        gst_app_src_push_buffer(
          GST_APP_SRC(self->input_),
          gst_buffer_ref(gst_sample_get_buffer(sample)));
      }
    }
    gst_sample_unref(sample);
    return GST_FLOW_OK;
  }
  void close_encoder()
  {
    {std::lock_guard<std::mutex> guard(mutex_); if (input_) {
        gst_object_unref(input_);
      }
      input_ = nullptr;}
    pacer_.stopping = true;
    if (encoded_) {gst_element_set_state(encoded_, GST_STATE_NULL);}
    if (encoded_) {timing_.detach(encoded_);}
    if (stamp_) {gst_object_unref(stamp_);}
    stamp_ = nullptr;
    if (encoder_) {gst_object_unref(encoder_);}
    encoder_ = nullptr;
    if (encoded_bus_) {gst_object_unref(encoded_bus_);}
    encoded_bus_ = nullptr;
    if (encoded_) {gst_object_unref(encoded_);}
    encoded_ = nullptr;
  }
  void build_encoder(const SenderControl & c)
  {
    const auto changed_at = rtp_send_time::mono_us();
    close_encoder();
    const bool hw = c.flags & 2, jpeg = source_caps_.find("image/jpeg") == 0;
    const auto raw = std::string(
      hw ? "video/x-raw(memory:NVMM),format=NV12" : "video/x-raw,format=I420") +
      ",width=" + std::to_string(c.width) + ",height=" + std::to_string(c.height) + ",framerate=" +
      std::to_string(c.fps) + "/1";
    std::vector<std::string> args{"appsrc", "name=video_capture_input", "is-live=true",
      "format=time",
      "block=false", "do-timestamp=false", "max-buffers=1", "leaky-type=downstream", "!",
      "videorate", "drop-only=true", "skip-to-first=true", "max-rate=" + std::to_string(c.fps), "!",
      std::string(jpeg ? "image/jpeg" : "video/x-raw") + ",framerate=" + std::to_string(c.fps) +
      "/1"};
    if (jpeg) {args.insert(args.end(), {"!", hw ? "nvv4l2decoder" : "jpegdec"});}
    if (jpeg && hw) {args.push_back("mjpeg=true");}
    if (hw) {
      args.insert(
        args.end(), {"!", "nvvidconv", "!", raw, "!", "nvv4l2h264enc",
          "name=latency_encoder", "copy-timestamp=true", "maxperf-enable=true", "control-rate=1",
          "bitrate=" + std::to_string(c.encoder_kbps * 1000), "vbv-size=" + std::to_string(c.vbv),
          "iframeinterval=" + std::to_string(c.key_interval),
          "idrinterval=" + std::to_string(c.key_interval), "insert-sps-pps=true",
          "!", "video/x-h264,stream-format=byte-stream,alignment=au,level=(string)3.1"});
    } else {
      args.insert(
        args.end(), {"!", "videoconvert", "!", "videoscale", "!", raw, "!", "x264enc", "name=latency_encoder",
          "bitrate=" + std::to_string(c.encoder_kbps), "speed-preset=" + preset_, "tune=" + tune_,
          "key-int-max=" + std::to_string(
            c.key_interval), "bframes=0", "byte-stream=true", "threads=1",
          "vbv-buf-capacity=" + std::to_string(std::max(100u, 1000 / std::max(1u, c.fps) + 1))});
    }
    args.insert(
      args.end(), {"!", "h264parse", "config-interval=1", "!", "queue", "max-size-buffers=1", "max-size-bytes=0", "max-size-time=0",
        "!", "rtph264pay", "name=latency_pay", "pt=96", "mtu=1176", "config-interval=1", "!", "udpsink", "name=video_udp",
        "host=" + host_, "port=" + std::to_string(
          port_), "buffer-size=65536", "sync=false", "async=false"});
    encoded_ = parse(args); clock_pipeline(encoded_);
    pacer_.reset(); pacer_.kbps = c.wire_kbps;
    timing_.attach(encoded_); stamp_ = rtp_send_time::attach(encoded_, &pacer_);
    encoder_ = gst_bin_get_by_name(GST_BIN(encoded_), "latency_encoder");
    encoded_bus_ = gst_element_get_bus(encoded_);
    {
      std::lock_guard<std::mutex> guard(mutex_);
      input_ = gst_bin_get_by_name(GST_BIN(encoded_), "video_capture_input");
      // The configured source caps may omit format/colorimetry. Appsrc must
      // receive the fixed caps negotiated by the real capture source before
      // its first buffer, rather than advertise an incomplete initial format.
      if (capture_caps_) {gst_app_src_set_caps(GST_APP_SRC(input_), capture_caps_);}
    }
    current_ = c;
    if (gst_element_set_state(encoded_, GST_STATE_PLAYING) == GST_STATE_CHANGE_FAILURE) {
      throw std::runtime_error("Cannot start camera encoder");
    }
    std::fprintf(
      stderr, "%s: encoder profile %ux%u %u FPS, %u kbps (reconfigured in %llu ms)\n",
      id_.c_str(), c.width, c.height, c.fps, c.wire_kbps,
      static_cast<unsigned long long>((rtp_send_time::mono_us() - changed_at) / 1000));
  }

public:
  CaptureSender(int argc, char ** argv, SenderControl initial)
  : id_(argv[1]), timing_(id_), current_(initial)
  {
    std::vector<std::string> source;
    for (int i = 2; i < argc && std::string(argv[i]) != "videorate"; ++i) {
      source.emplace_back(argv[i]);
    }
    if (!source.empty() && source.back() == "!") {source.pop_back();}
    for (int i = 2; i < argc; ++i) {
      const std::string a(argv[i]);
      if (source_caps_.empty() && (a.find("video/x-raw,") == 0 || a.find("image/jpeg,") == 0)) {
        source_caps_ = a;
      }
      if (a.find("host=") == 0) {host_ = a.substr(5);}
      if (a.find("port=") == 0) {port_ = std::stoi(a.substr(5));}
      if (a.find("speed-preset=") == 0) {preset_ = a.substr(13);}
      if (a.find("tune=") == 0) {tune_ = a.substr(5);}
    }
    source.insert(
      source.end(),
      {"!", "appsink", "name=video_capture", "sync=false", "async=false", "max-buffers=1",
        "drop=true"});
    capture_ = parse(source); clock_pipeline(capture_);
    auto * sources = gst_bin_iterate_recurse(GST_BIN(capture_));
    gst_iterator_foreach(sources, configure_capture_source, const_cast<char *>(id_.c_str()));
    gst_iterator_free(sources);
    timing_.attach(capture_); capture_bus_ = gst_element_get_bus(capture_);
    auto * sink = gst_bin_get_by_name(GST_BIN(capture_), "video_capture");
    GstAppSinkCallbacks callbacks{}; callbacks.new_sample = sample;
    gst_app_sink_set_callbacks(GST_APP_SINK(sink), &callbacks, this, nullptr);
    gst_object_unref(sink);
    if (const auto * fd = std::getenv("MR2_VIDEO_WIRE_FD")) {pacer_.wire.attach(std::atoi(fd));}
    if (const auto * fd = std::getenv("MR2_VIDEO_CONTROL_FD")) {control_ = std::atoi(fd);}
    if (initial.wire_kbps > 0) {build_encoder(initial);}
    if (gst_element_set_state(capture_, GST_STATE_PLAYING) == GST_STATE_CHANGE_FAILURE) {
      throw std::runtime_error("Cannot start camera capture");
    }
  }
  ~CaptureSender()
  {
    if (capture_) {gst_element_set_state(capture_, GST_STATE_NULL);}
    close_encoder();
    if (capture_) {timing_.detach(capture_); gst_object_unref(capture_);}
    if (capture_bus_) {gst_object_unref(capture_bus_);}
    if (capture_caps_) {gst_caps_unref(capture_caps_);}
    gst_object_unref(clock_);
  }
  int run()
  {
    auto report_at = rtp_send_time::mono_us(); uint64_t key_at = 0;
    while (!stopped) {
      SenderControl next = current_; bool have = false, force = false;
      SenderControl msg;
      while (control_ >= 0 && recv(control_, &msg, sizeof(msg), MSG_DONTWAIT) == sizeof(msg)) {
        if (msg.magic != current_.magic || !msg.width || !msg.height || !msg.fps ||
          (!(msg.flags & 4) && (!msg.encoder_kbps || !msg.wire_kbps))) {continue;}
        next = msg; have = true; force |= bool(msg.flags & 1);
      }
      if (have) {
        if (next.flags & 4) {
          if (!(current_.flags & 4)) {close_encoder();}
          pacer_.kbps = 0; current_ = next;
        } else {
          const bool rebuild = !encoded_ || next.width != current_.width ||
            next.height != current_.height ||
            next.fps != current_.fps ||
            ((next.flags ^ current_.flags) & 2) ||
            next.encoder_kbps * 1000 / next.fps > current_.vbv ||
            next.wire_kbps < current_.wire_kbps * .7;
          if (rebuild) {build_encoder(next);} else {
            if (next.encoder_kbps != current_.encoder_kbps) {
              g_object_set(
                encoder_, "bitrate",
                guint((next.flags & 2) ? next.encoder_kbps * 1000 : next.encoder_kbps), nullptr);
            }
            pacer_.kbps = next.wire_kbps;
            // The driver keeps the startup VBV; preserve its actual value.
            next.vbv = current_.vbv; current_ = next;
          }
          if (force) {pacer_.request_keyframe = true;}
        }
      }
      const auto now = rtp_send_time::mono_us();
      if (encoder_ && pacer_.request_keyframe && now - key_at >= 300000) {
        pacer_.request_keyframe = false; key_at = now; rtp_send_time::force_keyframe(encoder_);
      }
      if (control_ >= 0 && now - report_at >= 500000) {
        uint32_t report[3] =
        {0x4d523253, pacer_.encoded_frames.load(), pacer_.admitted_frames.load()};
        const auto ignored = send(control_, report, sizeof(report), MSG_DONTWAIT | MSG_NOSIGNAL);
        (void)ignored; report_at = now;
      }
      for (auto * bus : {capture_bus_, encoded_bus_}) {
        if (!bus) {continue;}
        auto * message = gst_bus_timed_pop_filtered(
          bus, (bus == encoded_bus_ || !encoded_bus_) ? 20 * GST_MSECOND : 0,
          static_cast<GstMessageType>(GST_MESSAGE_ERROR | GST_MESSAGE_EOS));
        if (!message) {continue;}
        if (GST_MESSAGE_TYPE(message) == GST_MESSAGE_ERROR) {
          GError * error = nullptr; gchar * debug = nullptr; gst_message_parse_error(
            message,
            &error,
            &debug);
          std::fprintf(stderr, "%s: %s (%s)\n", id_.c_str(), error->message, debug ? debug : "");
          g_error_free(error); g_free(debug); gst_message_unref(message); return 1;
        }
        gst_message_unref(message); return 0;
      }
    }
    return 0;
  }
};
