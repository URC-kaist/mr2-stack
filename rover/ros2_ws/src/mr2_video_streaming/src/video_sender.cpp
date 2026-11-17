#include "frame_timing.hpp"
#include "rtp_send_time.hpp"
#include <gst/video/video-event.h>
#include <sys/socket.h>
#include <sys/ioctl.h>
#include <linux/videodev2.h>
#include <cerrno>
#include <cstdlib>
#include <csignal>
#include <cstdio>
#include <cstring>

static volatile std::sig_atomic_t stopped = 0;
static void stop(int) {stopped = 1;}

static int camera_ioctl(int fd, unsigned long request, void * argument)
{
  int result;
  do {
    result = ioctl(fd, request, argument);
  } while (result < 0 && errno == EINTR);
  return result;
}

// Configure the actual capture FD before format negotiation on every open,
// including capture checks and restarts after USB re-enumeration. Do not change
// automatic exposure itself: only disallow exposure-driven frame-rate reduction.
static void fixed_capture_rate(GstElement *, gint fd, GstCaps *, gpointer data)
{
  const auto * stream_id = static_cast<const char *>(data);
  v4l2_queryctrl query{};
  query.id = V4L2_CID_EXPOSURE_AUTO_PRIORITY;
  if (camera_ioctl(fd, VIDIOC_QUERYCTRL, &query) < 0) {
    if (errno != EINVAL && errno != ENOTTY) {
      std::fprintf(
        stderr, "%s: cannot query exposure frame-rate control: %s\n",
        stream_id, std::strerror(errno));
    }
    return;  // Optional control; unsupported cameras must still capture.
  }
  if (query.flags & V4L2_CTRL_FLAG_DISABLED) {return;}
  v4l2_control control{};
  control.id = V4L2_CID_EXPOSURE_AUTO_PRIORITY;
  control.value = 0;
  if (camera_ioctl(fd, VIDIOC_S_CTRL, &control) < 0) {
    std::fprintf(
      stderr, "%s: cannot disable exposure-driven frame-rate reduction: %s\n",
      stream_id, std::strerror(errno));
    return;
  }
  std::fprintf(
    stderr, "%s: exposure_dynamic_framerate=0 (exposure mode unchanged)\n",
    stream_id);
}

static void configure_capture_source(const GValue * item, gpointer stream_id)
{
  auto * element = GST_ELEMENT(g_value_get_object(item));
  auto * factory = gst_element_get_factory(element);
  if (factory && g_str_equal(gst_plugin_feature_get_name(factory), "v4l2src")) {
    g_signal_connect(element, "prepare-format", G_CALLBACK(fixed_capture_rate), stream_id);
  }
}

#include "capture_sender.hpp"

// Keep one process per USB camera, retaining the existing crash isolation.
int main(int argc, char ** argv)
{
  if (argc < 3) {return 2;}
  const std::string stream_id(argv[1]);
  gst_init(nullptr, nullptr);
  std::signal(SIGINT, stop); std::signal(SIGTERM, stop);
  if (const auto * profile = std::getenv("MR2_VIDEO_PROFILE")) {
    SenderControl config;
    std::istringstream p(profile);
    if (p >> config.encoder_kbps >> config.wire_kbps >> config.width >> config.height >>
      config.fps >> config.vbv >> config.key_interval >> config.flags)
    {
      try {
        CaptureSender sender(argc, argv, config); return sender.run();
      } catch (const std::exception & error) {
        std::fprintf(stderr, "%s: %s\n", stream_id.c_str(), error.what()); return 1;
      }
    }
    return 2;
  }
  GError * error = nullptr;
  GstElement * pipeline = gst_parse_launchv(const_cast<const gchar **>(argv + 2), &error);
  if (!pipeline || error) {
    std::fprintf(stderr, "video sender: %s\n", error ? error->message : "invalid pipeline");
    if (error) {g_error_free(error);}
    if (pipeline) {gst_object_unref(pipeline);}
    return 1;
  }
  auto * sources = gst_bin_iterate_recurse(GST_BIN(pipeline));
  gst_iterator_foreach(sources, configure_capture_source, const_cast<char *>(stream_id.c_str()));
  gst_iterator_free(sources);
  auto timing = std::make_unique<FrameTiming>(stream_id);
  timing->attach(pipeline);
  rtp_send_time::Pacer pacer;
  if (const auto * fd = std::getenv("MR2_VIDEO_WIRE_FD")) {pacer.wire.attach(std::atoi(fd));}
  GstPad * stamp_pad = rtp_send_time::attach(pipeline, &pacer);
  GstElement * encoder = gst_bin_get_by_name(GST_BIN(pipeline), "latency_encoder");
  if (encoder) {
    guint rate = 0; g_object_get(encoder, "bitrate", &rate, nullptr);
    const bool software = g_str_has_prefix(G_OBJECT_TYPE_NAME(encoder), "GstX264");
    pacer.kbps = software ? int(rate * 1.2) : int(rate / 1000 * 1.2);
  }
  const char * fd_env = std::getenv("MR2_VIDEO_CONTROL_FD");
  const int control_fd = fd_env ? std::atoi(fd_env) : -1;
  uint64_t last_keyframe = 0;
  auto last_report = rtp_send_time::mono_us();
  std::signal(SIGINT, stop);
  std::signal(SIGTERM, stop);
  int result =
    gst_element_set_state(pipeline, GST_STATE_PLAYING) == GST_STATE_CHANGE_FAILURE ? 1 : 0;
  GstBus * bus = gst_element_get_bus(pipeline);
  while (!stopped && result == 0) {
    uint32_t update[3]{};
    const auto update_size = control_fd >=
      0 ? recv(control_fd, update, sizeof(update), MSG_DONTWAIT) : -1;
    if ((update_size == 8 || update_size == 12) && encoder) {
      const bool software = g_str_has_prefix(G_OBJECT_TYPE_NAME(encoder), "GstX264");
      g_object_set(encoder, "bitrate", guint(software ? update[0] : update[0] * 1000), nullptr);
      pacer.kbps = update[1];
      if (update_size == 12 && (update[2] & 1)) {pacer.request_keyframe = true;}
    }
    if (pacer.request_keyframe && encoder && rtp_send_time::mono_us() - last_keyframe > 300000) {
      pacer.request_keyframe = false; last_keyframe = rtp_send_time::mono_us();
      rtp_send_time::force_keyframe(encoder);
    }
    if (control_fd >= 0 && rtp_send_time::mono_us() - last_report >= 500000) {
      uint32_t report[3] = {0x4d523253, pacer.encoded_frames.load(), pacer.admitted_frames.load()};
      const auto ignored = send(control_fd, report, sizeof(report), MSG_DONTWAIT | MSG_NOSIGNAL);
      (void)ignored;
      last_report = rtp_send_time::mono_us();
    }
    GstMessage * message = gst_bus_timed_pop_filtered(
      bus, 20 * GST_MSECOND,
      static_cast<GstMessageType>(GST_MESSAGE_ERROR | GST_MESSAGE_EOS));
    if (!message) {continue;}
    if (GST_MESSAGE_TYPE(message) == GST_MESSAGE_ERROR) {
      gchar * debug = nullptr;
      gst_message_parse_error(message, &error, &debug);
      std::fprintf(stderr, "%s: %s (%s)\n", stream_id.c_str(), error->message, debug ? debug : "");
      g_error_free(error);
      g_free(debug);
      result = 1;
    }
    gst_message_unref(message);
    break;
  }
  pacer.stopping = true;
  gst_element_set_state(pipeline, GST_STATE_NULL);
  if (stamp_pad) {gst_object_unref(stamp_pad);}
  if (encoder) {gst_object_unref(encoder);}
  timing.reset();
  gst_object_unref(bus);
  gst_object_unref(pipeline);
  return result;
}
