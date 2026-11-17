#include <algorithm>
#include <chrono>
#include <cerrno>
#include <cctype>
#include <cstdlib>
#include <csignal>
#include <cstring>
#include <cstdio>
#include <cstdint>
#include <filesystem>
#include <fstream>
#include <map>
#include <memory>
#include <mutex>
#include <set>
#include <sstream>
#include <stdexcept>
#include <string>
#include <thread>
#include <utility>
#include <vector>
#include <sys/wait.h>
#include <unistd.h>

#include <gst/app/gstappsrc.h>
#include <gst/gst.h>
#include "frame_timing.hpp"
#include "video_control.hpp"
#include "rtp_send_time.hpp"
#include "link_probe.hpp"
#include "sender_control.hpp"
#include <gst/video/video-event.h>
#include "std_msgs/msg/u_int8_multi_array.hpp"
#include <sys/socket.h>
#include <fcntl.h>

#include <nlohmann/json.hpp>

#include "mr2_latency_msgs/srv/acquire_video_stream_lease.hpp"
#include "mr2_latency_msgs/srv/release_video_stream_lease.hpp"
#include "rclcpp/rclcpp.hpp"
#include "sensor_msgs/msg/image.hpp"
#include "std_srvs/srv/set_bool.hpp"

namespace mr2_video_streaming
{

using json = nlohmann::json;

constexpr int kUdpSendBufferBytes = 65536;
constexpr int kMinV4L2RestartDelaySeconds = 1;
constexpr int kMaxV4L2RestartDelaySeconds = 30;
constexpr int kV4L2StableRuntimeSeconds = 5;

enum class StreamSourceType { RosTopic, V4L2 };

struct EncoderConfig
{
  std::string type{"x264"};
  int bitrate_kbps{1500};
  int keyframe_interval{30};
  std::string speed_preset{"ultrafast"};
  std::string tune{"zerolatency"};
};

struct StreamConfig
{
  bool high_priority{false};
  std::string stream_id;
  StreamSourceType source_type{StreamSourceType::RosTopic};
  std::string ros_topic;
  std::string ros_encoding;
  std::string v4l2_device;
  std::string v4l2_pixel_format;
  int v4l2_capture_width{0};
  int v4l2_capture_height{0};
  int udp_port{0};
  int width{0};
  int height{0};
  int framerate{15};
  EncoderConfig encoder;
};

std::string uppercase(std::string value)
{
  std::transform(
    value.begin(), value.end(), value.begin(), [](unsigned char ch) {
      return static_cast<char>(std::toupper(ch));
    });
  return value;
}

bool is_supported_ros_encoding(const std::string & encoding)
{
  return encoding == "rgb8" || encoding == "bgr8";
}

bool is_jetson_hardware_encoder(const std::string & encoder_type)
{
  return encoder_type == "nvv4l2h264enc" || encoder_type == "jetson_h264" ||
         encoder_type == "jetson";
}

bool is_supported_encoder_type(const std::string & encoder_type)
{
  return encoder_type == "x264" || encoder_type == "x264enc" ||
         is_jetson_hardware_encoder(encoder_type);
}

bool is_valid_lease_owner(const std::string & value)
{
  if (value.empty() || value.size() > 128) {
    return false;
  }
  return std::all_of(
    value.begin(), value.end(), [](unsigned char ch) {
      return std::isalnum(ch) || ch == '-' || ch == '_' || ch == '.';
    });
}

std::string trim_copy(const std::string & value)
{
  const auto first = value.find_first_not_of(" \t\r\n");
  if (first == std::string::npos) {
    return "";
  }
  const auto last = value.find_last_not_of(" \t\r\n");
  return value.substr(first, last - first + 1);
}

std::set<std::string> parse_disabled_stream_ids(const std::string & value)
{
  std::set<std::string> stream_ids;
  std::stringstream input(value);
  std::string item;
  while (std::getline(input, item, ',')) {
    item = trim_copy(item);
    if (!item.empty()) {
      stream_ids.insert(item);
    }
  }
  return stream_ids;
}

std::string source_type_to_string(StreamSourceType source_type)
{
  switch (source_type) {
    case StreamSourceType::RosTopic:
      return "ros_topic";
    case StreamSourceType::V4L2:
      return "v4l2";
  }
  return "unknown";
}

std::string quote_gstreamer_string(const std::string & value)
{
  std::string quoted = "\"";
  for (const char ch : value) {
    if (ch == '\\' || ch == '"') {
      quoted.push_back('\\');
    }
    quoted.push_back(ch);
  }
  quoted.push_back('"');
  return quoted;
}

class StreamPipeline
{
public:
  StreamPipeline(StreamConfig config, std::string base_host, rclcpp::Logger logger)
  : config_(std::move(config)),
    base_host_(std::move(base_host)),
    logger_(std::move(logger)),
    frame_duration_ns_(1000000000LL / std::max(1, config_.framerate)),
    maximum_kbps_(config_.encoder.bitrate_kbps), output_fps_(config_.framerate)
  {
    if (config_.source_type == StreamSourceType::V4L2) {
      if (config_.v4l2_capture_width <= 0) {config_.v4l2_capture_width = config_.width;}
      if (config_.v4l2_capture_height <= 0) {config_.v4l2_capture_height = config_.height;}
      last_device_present_ = std::filesystem::exists(config_.v4l2_device);
      v4l2_restart_failures_ = 1;
      next_v4l2_restart_at_ = std::chrono::steady_clock::time_point::min();
    }
  }

  ~StreamPipeline() {shutdown();}

  const std::string & stream_id() const {return config_.stream_id;}

  bool is_enabled() const {return enabled_;}
  bool device_present() const
  {
    if (config_.source_type == StreamSourceType::RosTopic) {
      return std::chrono::steady_clock::now() - last_ros_frame_at_ < std::chrono::seconds(3);
    }
    return std::filesystem::exists(config_.v4l2_device);
  }
  int port() const {return config_.udp_port;}
  int maximum_kbps() const {return maximum_kbps_;}
  void refresh_device_presence()
  {
    if (config_.source_type != StreamSourceType::V4L2) {return;}
    const bool present = device_present();
    if (present && !last_device_present_) {
      v4l2_restart_failures_ = 1;
      next_v4l2_restart_at_ = std::chrono::steady_clock::time_point::min();
    }
    last_device_present_ = present;
  }
  int allocation_capacity() const
  {
    if (!enabled_ || !device_present()) {return 0;}
    if (config_.source_type == StreamSourceType::V4L2 &&
      (v4l2_restart_failures_ > 0 || !retired_pids_.empty())) {return 0;}
    return maximum_kbps_;
  }
  bool high_priority() const {return config_.high_priority;}
  void set_wire_clock(int fd) {wire_fd_ = fd; if (fd >= 0) {pacer_.wire.attach(fd);}}
  void request_keyframe()
  {
    if (!enabled_ || pacer_.kbps <= 0) {return;}
    if (control_fd_ >= 0) {send_sender_control(true);} else {pacer_.request_keyframe = true;}
  }
  void set_priority(bool high) {config_.high_priority = high;}
  uint8_t state() const
  {
    return !device_present() ? video_control::NO_DEVICE : enabled_ ? video_control::ON :
           video_control::OFF;
  }
  void append_status(std::vector<uint8_t> & b) const
  {
    video_control::append(b, port(), 2);
    b.push_back(state()); b.push_back((high_priority() ? 1 : 0) | (enabled_ ? 2 : 0));
    video_control::append(b, enabled_ ? config_.encoder.bitrate_kbps : 0);
    video_control::append(b, std::max(0, config_.width), 2);
    video_control::append(b, std::max(0, config_.height), 2);
    b.push_back(output_fps_);
  }
  void apply_budget(int wire_kbps)
  {
    const int previous_wire = pacer_.kbps;
    const bool live_capture = config_.source_type == StreamSourceType::V4L2 && gst_child_pid_ > 0;
    if (wire_kbps <= 0) {
      pacer_.kbps = 0; config_.encoder.bitrate_kbps = 0;
      if (live_capture) {send_sender_control();} else if (previous_wire > 0) {shutdown();}
      return;
    }
    if (recovery_level_ && recovery_wire_kbps_ > 0 && wire_kbps >= recovery_wire_kbps_ * 1.5) {
      recovery_level_ = 0; adapted_ = false;
    }
    // The allocation includes RTP/UDP overhead and leaves encoder burst margin.
    const int bitrate = std::max(1, int(wire_kbps * .85));
    const bool rate_changed = bitrate != config_.encoder.bitrate_kbps || wire_kbps != previous_wire;
    if (!live_capture && ((previous_wire > 0 && wire_kbps < previous_wire * .7) ||
      (encoder_vbv_bits_ > 0 && bitrate * 1000 / std::max(1, output_fps_) > encoder_vbv_bits_)))
    {
      shutdown(); stopping_ = false; pacer_.stopping = false; adapted_ = false;
      next_v4l2_restart_at_ = std::chrono::steady_clock::time_point::min();
    }
    config_.encoder.bitrate_kbps = bitrate;
    pacer_.kbps = wire_kbps;
    if (pipeline_ && rate_changed) {
      auto * encoder = gst_bin_get_by_name(GST_BIN(pipeline_), "latency_encoder");
      if (encoder) {
        const bool software = g_str_has_prefix(G_OBJECT_TYPE_NAME(encoder), "GstX264");
        g_object_set(encoder, "bitrate", guint(software ? bitrate : bitrate * 1000), nullptr);
        gst_object_unref(encoder);
      }
    }
    if (config_.source_type == StreamSourceType::RosTopic && input_width_ <= 0) {return;}
    const int width = config_.v4l2_capture_width > 0 ? config_.v4l2_capture_width :
      (input_width_ > 0 ? input_width_ : config_.width);
    const int height = config_.v4l2_capture_height > 0 ? config_.v4l2_capture_height :
      (input_height_ > 0 ? input_height_ : config_.height);
    if (width <= 0 || height <= 0) {return;}
    auto target = video_control::profile(bitrate, width, height, config_.framerate);
    if (recovery_level_ > 0) {
      const int divisor = 1 << recovery_level_;
      target.width = std::max(32, target.width / divisor / 2 * 2);
      target.height = std::max(24, target.height / divisor / 2 * 2);
      target.fps = std::min(target.fps, recovery_level_ == 1 ? 10 : 5);
    }
    const auto now = std::chrono::steady_clock::now();
    if (target.width != candidate_.width || target.height != candidate_.height ||
      target.fps != candidate_.fps)
    {
      candidate_ = target; candidate_since_ = now;
    }
    const bool initial = !adapted_ || previous_wire <= 0;
    // A deliberately paused capture may have produced no encoded frames for
    // minutes. Give its resumed encoder the normal watchdog startup window;
    // that silence was not a camera/encoder failure.
    if (previous_wire <= 0 && live_capture) {reset_sender_progress();}
    const bool down = target.width < config_.width || target.fps < output_fps_;
    bool profile_changed = false;
    if ((target.width != config_.width || target.height != config_.height ||
      target.fps != output_fps_) &&
      (initial || (down && wire_kbps < profile_wire_kbps_ * .7) ||
      (now - candidate_since_ >= std::chrono::seconds(down ? 1 : 5) &&
      now - last_profile_change_ >= std::chrono::seconds(3))))
    {
      if (!live_capture) {shutdown();}
      config_.width = target.width; config_.height = target.height; output_fps_ = target.fps;
      profile_changed = true;
      profile_wire_kbps_ = wire_kbps;
      last_profile_change_ = now;
      stopping_ = false; pacer_.stopping = false;
      next_v4l2_restart_at_ = std::chrono::steady_clock::time_point::min();
    }
    if (initial) {stopping_ = false; pacer_.stopping = false; profile_wire_kbps_ = wire_kbps;}
    adapted_ = true;
    if (control_fd_ >= 0 && (rate_changed || profile_changed)) {send_sender_control();}
  }

  bool set_enabled(bool enabled)
  {
    if (enabled_ == enabled) {
      return false;
    }

    enabled_ = enabled;
    if (!enabled_) {
      shutdown();
      RCLCPP_INFO(logger_, "Disabled video stream %s", config_.stream_id.c_str());
      return true;
    }

    stopping_ = false;
    pacer_.stopping = false;
    v4l2_restart_failures_ = config_.source_type == StreamSourceType::V4L2 ? 1 : 0;
    next_v4l2_restart_at_ = std::chrono::steady_clock::time_point::min();
    start_if_needed();
    RCLCPP_INFO(logger_, "Enabled video stream %s", config_.stream_id.c_str());
    return true;
  }

  void start_if_needed()
  {
    if (!enabled_ || pacer_.kbps <= 0 || v4l2_restart_failures_ > 0 ||
      config_.source_type != StreamSourceType::V4L2 || pipeline_ != nullptr ||
      gst_child_pid_ > 0)
    {
      return;
    }
    if (next_v4l2_restart_at_ != std::chrono::steady_clock::time_point::max() &&
      std::chrono::steady_clock::now() < next_v4l2_restart_at_)
    {
      return;
    }
    start_v4l2_pipeline();
  }

  void poll_runtime()
  {
    retired_pids_.erase(
      std::remove_if(
        retired_pids_.begin(), retired_pids_.end(), [this](pid_t pid) {
          if (waitpid(pid, nullptr, WNOHANG) != 0) {retired_deadlines_.erase(pid); return true;}
          const auto found = retired_deadlines_.find(pid);
          if (found != retired_deadlines_.end() &&
          std::chrono::steady_clock::now() >= found->second) {kill(pid, SIGKILL);}
          return false;
        }), retired_pids_.end());
    if (config_.source_type == StreamSourceType::V4L2 && enabled_ && v4l2_restart_failures_ > 0) {
      poll_capture_check();
      return;
    }
    if (control_fd_ >= 0) {
      uint32_t report[3];
      while (recv(control_fd_, report, sizeof(report), MSG_DONTWAIT) == sizeof(report)) {
        if (report[0] == 0x4d523253) {sender_encoded_ = report[1]; sender_admitted_ = report[2];}
      }
    } else if (pipeline_) {
      sender_encoded_ = pacer_.encoded_frames; sender_admitted_ = pacer_.admitted_frames;
    }
    if (enabled_ && pacer_.kbps > 0 && (pipeline_ || gst_child_pid_ > 0)) {
      const auto now = std::chrono::steady_clock::now();
      if (sender_encoded_ != last_sender_encoded_) {
        last_encoding_progress_ = now; last_sender_encoded_ = sender_encoded_;
      }
      if (config_.source_type == StreamSourceType::V4L2 &&
        now - last_encoding_progress_ > std::chrono::seconds(5))
      {
        shutdown(); stopping_ = false; pacer_.stopping = false;
        schedule_v4l2_restart("Stream " + config_.stream_id + " stopped producing encoded frames");
        return;
      }
      if (sender_admitted_ != last_sender_admitted_) {
        last_sender_progress_ = now; last_sender_admitted_ = sender_admitted_;
      }
      if (sender_encoded_ > 0 && now - last_encoding_progress_ < std::chrono::seconds(1) &&
        now - last_sender_progress_ > std::chrono::seconds(2) &&
        now - last_recovery_change_ > std::chrono::seconds(3))
      {
        recovery_level_ = std::min(3, recovery_level_ + 1);
        recovery_wire_kbps_ = pacer_.kbps;
        last_recovery_change_ = now; next_recovery_upgrade_ = now + std::chrono::seconds(30);
        RCLCPP_WARN(
          logger_, "Stream %s has no admitted frames; retrying at recovery level %d",
          config_.stream_id.c_str(), recovery_level_);
        const int wire = pacer_.kbps;
        shutdown(); adapted_ = false; apply_budget(wire); start_if_needed(); return;
      }
      if (recovery_level_ > 0 && now >= next_recovery_upgrade_ &&
        now - last_sender_progress_ < std::chrono::seconds(1))
      {
        --recovery_level_; next_recovery_upgrade_ = now + std::chrono::seconds(30);
        last_recovery_change_ = now; adapted_ = false;
        apply_budget(pacer_.kbps); start_if_needed(); return;
      }
    }
    if (pipeline_failed_.exchange(false)) {
      shutdown();
      stopping_ = false;
      pacer_.stopping = false;
    }
    if (pipeline_ && pacer_.request_keyframe &&
      rtp_send_time::mono_us() - last_keyframe_us_ > 1000000)
    {
      pacer_.request_keyframe = false; last_keyframe_us_ = rtp_send_time::mono_us();
      auto * encoder = gst_bin_get_by_name(GST_BIN(pipeline_), "latency_encoder");
      if (encoder) {
        rtp_send_time::force_keyframe(encoder);
        gst_object_unref(encoder);
      }
    }
    if (!enabled_ || config_.source_type != StreamSourceType::V4L2 ||
      stopping_)
    {
      return;
    }

    if (gst_child_pid_ > 0) {
      int status = 0;
      const pid_t result = waitpid(gst_child_pid_, &status, WNOHANG);
      if (result == 0) {
        const auto now = std::chrono::steady_clock::now();
        if (!v4l2_child_reported_stable_ &&
          now - gst_child_started_at_ >= std::chrono::seconds(kV4L2StableRuntimeSeconds))
        {
          v4l2_restart_failures_ = 0;
          v4l2_child_reported_stable_ = true;
        }
        return;
      }

      if (result == gst_child_pid_) {
        std::string reason;
        if (WIFEXITED(status)) {
          reason = "exited with code " + std::to_string(WEXITSTATUS(status));
        } else if (WIFSIGNALED(status)) {
          reason = "exited on signal " + std::to_string(WTERMSIG(status));
        } else {
          reason = "exited unexpectedly";
        }
        gst_child_pid_ = -1;
        schedule_v4l2_restart("V4L2 sender child for stream " + config_.stream_id + " " + reason);
      } else if (result < 0) {
        if (errno != ECHILD) {
          RCLCPP_ERROR(
            logger_,
            "waitpid failed for V4L2 sender child on stream %s: %s",
            config_.stream_id.c_str(),
            std::strerror(errno));
          return;
        }

        gst_child_pid_ = -1;
        schedule_v4l2_restart("V4L2 sender child for stream " + config_.stream_id + " disappeared");
      }
    }

    if (gst_child_pid_ <= 0 && std::chrono::steady_clock::now() >= next_v4l2_restart_at_) {
      try {
        start_v4l2_pipeline();
      } catch (const std::exception & error) {
        schedule_v4l2_restart(
          "failed to restart V4L2 sender for stream " + config_.stream_id + ": " + error.what());
      }
    }
  }

  void push_frame(const sensor_msgs::msg::Image & msg)
  {
    last_ros_frame_at_ = std::chrono::steady_clock::now();
    if (!enabled_ || pacer_.kbps <= 0 || config_.source_type != StreamSourceType::RosTopic) {
      return;
    }

    if (!validate_encoding(msg.encoding)) {
      return;
    }

    if (input_width_ <= 0) {
      input_width_ = static_cast<int>(msg.width); input_height_ = static_cast<int>(msg.height);
      apply_budget(pacer_.kbps);
    }

    if (!ensure_ros_pipeline(static_cast<int>(msg.width), static_cast<int>(msg.height))) {
      return;
    }

    std::vector<uint8_t> normalized;
    if (!normalize_to_rgb(msg, normalized)) {
      return;
    }

    GstBuffer * buffer = gst_buffer_new_allocate(nullptr, normalized.size(), nullptr);
    if (buffer == nullptr) {
      RCLCPP_ERROR(
        logger_, "Failed to allocate GstBuffer for stream %s",
        config_.stream_id.c_str());
      return;
    }

    gst_buffer_fill(buffer, 0, normalized.data(), normalized.size());
    // ROS callbacks can skip/delay frames under the full autonomy workload.
    // Timestamp their actual ingestion; a synthetic frame counter makes RTP
    // time drift behind wall time and the receiver discard otherwise good data.
    GST_BUFFER_PTS(buffer) = GST_CLOCK_TIME_NONE;
    GST_BUFFER_DTS(buffer) = GST_CLOCK_TIME_NONE;
    GST_BUFFER_DURATION(buffer) = frame_duration_ns_;
    frame_index_ += 1;

    const GstFlowReturn result = gst_app_src_push_buffer(GST_APP_SRC(appsrc_), buffer);
    if (result != GST_FLOW_OK) {
      RCLCPP_WARN(
        logger_,
        "Dropped frame for stream %s because appsrc returned flow status %d",
        config_.stream_id.c_str(),
        static_cast<int>(result));
    }
  }

private:
  SenderControl sender_control(bool force = false) const
  {
    SenderControl c;
    c.encoder_kbps = config_.encoder.bitrate_kbps; c.wire_kbps = pacer_.kbps;
    c.width = config_.width; c.height = config_.height; c.fps = output_fps_;
    c.vbv = video_control::vbv_bits(c.encoder_kbps, c.fps);
    c.key_interval = video_control::keyframe_interval(config_.encoder.keyframe_interval, c.fps);
    c.flags = (force ? 1 : 0) | (hardware_encoder() ? 2 : 0) | (c.wire_kbps == 0 ? 4 : 0);
    return c;
  }
  void send_sender_control(bool force = false)
  {
    const auto c = sender_control(force);
    const auto ignored = send(control_fd_, &c, sizeof(c), MSG_DONTWAIT | MSG_NOSIGNAL);
    (void)ignored;
  }
  static void append_live_leaky_queue(std::vector<std::string> & args)
  {
    args.insert(
      args.end(),
      {
        "!",
        "queue",
        "leaky=downstream",
        "max-size-buffers=1",
        "max-size-bytes=0",
        "max-size-time=0",
      });
  }

  bool validate_encoding(const std::string & encoding)
  {
    if (!is_supported_ros_encoding(encoding)) {
      RCLCPP_WARN(
        logger_,
        "Stream %s received unsupported encoding %s",
        config_.stream_id.c_str(),
        encoding.c_str());
      return false;
    }

    return true;
  }

  std::string build_encoded_sink_branch() const
  {
    std::ostringstream branch;
    if (hardware_encoder()) {
      branch << "! nvvidconv "
             << "! video/x-raw(memory:NVMM),format=NV12 "
             << "! nvv4l2h264enc name=latency_encoder copy-timestamp=true bitrate=" <<
        (config_.encoder.bitrate_kbps * 1000)
             << " vbv-size=" << video_control::vbv_bits(config_.encoder.bitrate_kbps, output_fps_)
             << " idrinterval=" << video_control::keyframe_interval(
        config_.encoder.keyframe_interval, output_fps_)
             << " iframeinterval=" << video_control::keyframe_interval(
        config_.encoder.keyframe_interval, output_fps_)
             << " insert-sps-pps=true maxperf-enable=true control-rate=1 "
             << "! video/x-h264,stream-format=byte-stream,alignment=au,level=(string)3.1 ";
    } else {
      branch << "! x264enc name=latency_encoder bitrate=" << config_.encoder.bitrate_kbps
             << " speed-preset=" << config_.encoder.speed_preset
             << " tune=" << config_.encoder.tune
             << " key-int-max=" << video_control::keyframe_interval(
        config_.encoder.keyframe_interval, output_fps_)
             << " bframes=0 byte-stream=true threads=1 ";
      branch << " vbv-buf-capacity=" << std::max(100, 1000 / std::max(1, output_fps_) + 1) << " ";
    }
    branch << "! h264parse config-interval=1 "
           << "! queue max-size-buffers=1 max-size-bytes=0 max-size-time=0 "
           << "! rtph264pay name=latency_pay pt=96 mtu=1176 config-interval=1 "
           << "! udpsink name=video_udp host=" << quote_gstreamer_string(base_host_)
           << " port=" << config_.udp_port
           << " buffer-size=" << kUdpSendBufferBytes
           << " sync=false async=false";
    return branch.str();
  }

  std::string build_raw_output_caps() const
  {
    std::ostringstream caps;
    caps << "video/x-raw,format=I420";
    if (output_width_ > 0) {
      caps << ",width=" << output_width_;
    }
    if (output_height_ > 0) {
      caps << ",height=" << output_height_;
    }
    if (output_fps_ > 0) {
      caps << ",framerate=" << std::max(1, output_fps_) << "/1";
    }
    return caps.str();
  }

  std::string build_nvmm_output_caps() const
  {
    std::ostringstream caps;
    caps << "video/x-raw(memory:NVMM),format=NV12";
    if (output_width_ > 0) {
      caps << ",width=" << output_width_;
    }
    if (output_height_ > 0) {
      caps << ",height=" << output_height_;
    }
    if (output_fps_ > 0) {
      caps << ",framerate=" << std::max(1, output_fps_) << "/1";
    }
    return caps.str();
  }

  std::string build_v4l2_source_caps() const
  {
    std::ostringstream caps;
    const std::string pixel_format = uppercase(config_.v4l2_pixel_format);
    const bool is_jpeg = pixel_format == "MJPG" || pixel_format == "JPEG";
    const int capture_width =
      config_.v4l2_capture_width > 0 ? config_.v4l2_capture_width : config_.width;
    const int capture_height =
      config_.v4l2_capture_height > 0 ? config_.v4l2_capture_height : config_.height;

    caps << (is_jpeg ? "image/jpeg" : "video/x-raw");
    if (!is_jpeg && !pixel_format.empty()) {
      caps << ",format=" << pixel_format;
    }
    if (capture_width > 0) {
      caps << ",width=" << capture_width;
    }
    if (capture_height > 0) {
      caps << ",height=" << capture_height;
    }
    if (config_.framerate > 0) {
      caps << ",framerate=" << std::max(1, config_.framerate) << "/1";
    }
    return caps.str();
  }

  bool ensure_ros_pipeline(int frame_width, int frame_height)
  {
    if (pipeline_ != nullptr) {
      if (frame_width != input_width_ || frame_height != input_height_) {
        RCLCPP_WARN(
          logger_,
          "Stream %s received unexpected frame dimensions %dx%d (expected %dx%d)",
          config_.stream_id.c_str(),
          frame_width,
          frame_height,
          input_width_,
          input_height_);
        return false;
      }
      return true;
    }

    input_width_ = frame_width;
    input_height_ = frame_height;
    output_width_ = config_.width > 0 ? config_.width : frame_width;
    output_height_ = config_.height > 0 ? config_.height : frame_height;

    if (output_width_ != frame_width || output_height_ != frame_height) {
      RCLCPP_INFO(
        logger_,
        "Stream %s scaling ROS frames from %dx%d to configured size %dx%d",
        config_.stream_id.c_str(),
        frame_width,
        frame_height,
        output_width_,
        output_height_);
    }

    std::ostringstream pipeline_description;
    pipeline_description
      << "appsrc name=src is-live=true format=time block=false do-timestamp=true max-buffers=1 "
      << "! queue leaky=downstream max-size-buffers=1 max-size-bytes=0 max-size-time=0 "
      << "! videoconvert "
      << "! videoscale ! videorate drop-only=true "
      << "! " << build_raw_output_caps() << " "
      << build_encoded_sink_branch();

    start_pipeline(pipeline_description.str(), true);

    RCLCPP_INFO(
      logger_,
      "Started ROS video stream %s topic=%s port=%d size=%dx%d fps=%d",
      config_.stream_id.c_str(),
      config_.ros_topic.c_str(),
      config_.udp_port,
      output_width_,
      output_height_,
      config_.framerate);
    return true;
  }

  void start_v4l2_pipeline()
  {
    if (gst_child_pid_ > 0) {
      return;
    }
    if (!std::filesystem::exists(config_.v4l2_device)) {
      schedule_v4l2_restart(
        "V4L2 device " + config_.v4l2_device + " for stream " + config_.stream_id +
        " is not present");
      return;
    }

    const int capture_width =
      config_.v4l2_capture_width > 0 ? config_.v4l2_capture_width : config_.width;
    const int capture_height =
      config_.v4l2_capture_height > 0 ? config_.v4l2_capture_height : config_.height;
    output_width_ = config_.width > 0 ? config_.width : capture_width;
    output_height_ = config_.height > 0 ? config_.height : capture_height;

    const std::string pixel_format = uppercase(config_.v4l2_pixel_format);
    const bool is_jpeg = pixel_format == "MJPG" || pixel_format == "JPEG";

    std::vector<std::string> args{
      (std::filesystem::read_symlink("/proc/self/exe").parent_path() / "video_sender").string(),
      config_.stream_id,
      "v4l2src",
      "name=src",
      "device=" + config_.v4l2_device,
      "do-timestamp=true",
    };
    const std::string source_caps = build_v4l2_source_caps();
    if (!source_caps.empty()) {
      args.push_back("!");
      args.push_back(source_caps);
    }
    append_live_leaky_queue(args);
    // Decimate in the camera's native memory domain. Placing videorate after
    // nvvidconv lets NVMM negotiation constrain a raw UVC camera's capture FPS.
    args.insert(
      args.end(), {"!", "videorate", "drop-only=true", "!",
        std::string(is_jpeg ? "image/jpeg" : "video/x-raw") +
        ",framerate=" + std::to_string(output_fps_) + "/1"});
    if (hardware_encoder()) {
      if (is_jpeg) {
        args.insert(args.end(), {"!", "nvv4l2decoder", "mjpeg=true"});
      }
      args.insert(
        args.end(),
        {
          "!",
          "nvvidconv",
          "!",
          build_nvmm_output_caps(),
          "!",
          "nvv4l2h264enc",
          "name=latency_encoder",
          "copy-timestamp=true",
          "bitrate=" + std::to_string(config_.encoder.bitrate_kbps * 1000),
          "vbv-size=" +
          std::to_string(video_control::vbv_bits(config_.encoder.bitrate_kbps, output_fps_)),
          "idrinterval=" +
          std::to_string(
            video_control::keyframe_interval(
              config_.encoder.keyframe_interval,
              output_fps_)),
          "iframeinterval=" +
          std::to_string(
            video_control::keyframe_interval(
              config_.encoder.keyframe_interval,
              output_fps_)),
          "insert-sps-pps=true",
          "maxperf-enable=true",
          "control-rate=1",
        });
    } else {
      if (is_jpeg) {
        args.push_back("!");
        args.push_back("jpegdec");
      }
      args.insert(
        args.end(),
        {"!", "videoconvert", "!", "videoscale", "!", build_raw_output_caps()});
      args.insert(
        args.end(),
        {
          "!",
          "x264enc",
          "name=latency_encoder",
          "bitrate=" + std::to_string(config_.encoder.bitrate_kbps),
          "speed-preset=" + config_.encoder.speed_preset,
          "tune=" + config_.encoder.tune,
          "key-int-max=" +
          std::to_string(
            video_control::keyframe_interval(
              config_.encoder.keyframe_interval,
              output_fps_)),
          "bframes=0",
          "byte-stream=true",
          "threads=1",
          "vbv-buf-capacity=" + std::to_string(std::max(100, 1000 / std::max(1, output_fps_) + 1)),
        });
    }
    if (hardware_encoder()) {
      args.insert(
        args.end(),
        {"!", "video/x-h264,stream-format=byte-stream,alignment=au,level=(string)3.1"});
    }
    args.insert(
      args.end(),
      {
        "!",
        "h264parse",
        "config-interval=1",
        "!", "queue", "max-size-buffers=1", "max-size-bytes=0", "max-size-time=0",
        "!",
        "rtph264pay",
        "name=latency_pay",
        "pt=96",
        "mtu=1176",
        "config-interval=1",
        "!",
        "udpsink",
        "name=video_udp",
        "host=" + base_host_,
        "port=" + std::to_string(config_.udp_port),
        "buffer-size=" + std::to_string(kUdpSendBufferBytes),
        "sync=false",
        "async=false",
      });

    start_gst_launch_child(args);
    gst_child_started_at_ = std::chrono::steady_clock::now();
    v4l2_child_reported_stable_ = false;
    next_v4l2_restart_at_ = std::chrono::steady_clock::time_point::max();

    std::ostringstream details;
    details << "Started V4L2 video stream " << config_.stream_id
            << " device=" << config_.v4l2_device
            << " port=" << config_.udp_port;
    if (capture_width > 0 && capture_height > 0) {
      details << " capture=" << capture_width << "x" << capture_height;
    }
    if (output_width_ > 0 && output_height_ > 0) {
      details << " output=" << output_width_ << "x" << output_height_;
    }
    if (config_.framerate > 0) {
      details << " fps=" << output_fps_;
    }
    if (!config_.v4l2_pixel_format.empty()) {
      details << " pixel_format=" << config_.v4l2_pixel_format;
    }
    RCLCPP_INFO(logger_, "%s", details.str().c_str());
  }

  void start_pipeline(const std::string & description, bool needs_appsrc)
  {
    pacer_.reset();
    reset_sender_progress();
    encoder_vbv_bits_ = hardware_encoder() ? video_control::vbv_bits(
      config_.encoder.bitrate_kbps,
      output_fps_) : 0;
    GError * error = nullptr;
    pipeline_ = gst_parse_launch(description.c_str(), &error);
    if (pipeline_ == nullptr) {
      const std::string message = error != nullptr ? error->message : "unknown error";
      if (error != nullptr) {
        g_error_free(error);
      }
      throw std::runtime_error(
              "failed to create pipeline for " + config_.stream_id + ": " +
              message);
    }

    if (needs_appsrc) {
      appsrc_ = gst_bin_get_by_name(GST_BIN(pipeline_), "src");
      if (appsrc_ == nullptr) {
        throw std::runtime_error("failed to resolve appsrc for stream " + config_.stream_id);
      }

      GstCaps * caps = gst_caps_new_simple(
        "video/x-raw",
        "format",
        G_TYPE_STRING,
        "RGB",
        "width",
        G_TYPE_INT,
        input_width_,
        "height",
        G_TYPE_INT,
        input_height_,
        "framerate",
        GST_TYPE_FRACTION,
        std::max(1, config_.framerate),
        1,
        nullptr);
      g_object_set(
        G_OBJECT(appsrc_),
        "caps",
        caps,
        "is-live",
        TRUE,
        "format",
        GST_FORMAT_TIME,
        "block",
        FALSE,
        "do-timestamp",
        TRUE,
        "max-buffers",
        1,
        nullptr);
      gst_caps_unref(caps);
    }

    stamp_pad_ = rtp_send_time::attach(pipeline_, &pacer_);
    timing_ = std::make_unique<FrameTiming>(config_.stream_id);
    timing_->attach(pipeline_);
    bus_ = gst_element_get_bus(pipeline_);
    if (bus_ != nullptr) {
      bus_watch_id_ = gst_bus_add_watch(bus_, &StreamPipeline::handle_bus_message, this);
    }

    const GstStateChangeReturn state_change = gst_element_set_state(pipeline_, GST_STATE_PLAYING);
    if (state_change == GST_STATE_CHANGE_FAILURE) {
      throw std::runtime_error("failed to start pipeline for stream " + config_.stream_id);
    }
    if (state_change == GST_STATE_CHANGE_ASYNC) {
      GstState current_state = GST_STATE_NULL;
      const GstStateChangeReturn settled =
        gst_element_get_state(pipeline_, &current_state, nullptr, 5 * GST_SECOND);
      if (settled == GST_STATE_CHANGE_FAILURE) {
        throw std::runtime_error(
                "pipeline failed while settling to PLAYING for stream " + config_.stream_id);
      }
    }
  }

  bool normalize_to_rgb(const sensor_msgs::msg::Image & msg, std::vector<uint8_t> & output)
  {
    const std::size_t expected_row_bytes = static_cast<std::size_t>(msg.width) * 3U;
    if (msg.step < expected_row_bytes) {
      RCLCPP_WARN(
        logger_,
        "Stream %s received invalid image step %u for width %u",
        config_.stream_id.c_str(),
        msg.step,
        msg.width);
      return false;
    }

    output.resize(static_cast<std::size_t>(msg.width) * static_cast<std::size_t>(msg.height) * 3U);
    for (std::size_t row = 0; row < msg.height; ++row) {
      const uint8_t * src = msg.data.data() + row * msg.step;
      uint8_t * dst = output.data() + row * expected_row_bytes;
      if (msg.encoding == "rgb8") {
        std::copy(src, src + expected_row_bytes, dst);
        continue;
      }

      for (std::size_t column = 0; column < msg.width; ++column) {
        const std::size_t src_offset = column * 3U;
        const std::size_t dst_offset = column * 3U;
        dst[dst_offset + 0] = src[src_offset + 2];
        dst[dst_offset + 1] = src[src_offset + 1];
        dst[dst_offset + 2] = src[src_offset + 0];
      }
    }

    return true;
  }

  void shutdown()
  {
    stopping_ = true;
    encoder_vbv_bits_ = 0;
    pacer_.stopping = true;
    if (control_fd_ >= 0) {close(control_fd_); control_fd_ = -1;}
    if (capture_check_pid_ > 0) {
      kill(capture_check_pid_, SIGKILL); retired_pids_.push_back(capture_check_pid_);
      capture_check_pid_ = -1;
    }
    if (gst_child_pid_ > 0) {
      kill(gst_child_pid_, SIGTERM);
      retired_pids_.push_back(gst_child_pid_);
      retired_deadlines_[gst_child_pid_] = std::chrono::steady_clock::now() +
        std::chrono::milliseconds(500);
      gst_child_pid_ = -1;
    }
    if (pipeline_ != nullptr) {
      gst_element_set_state(pipeline_, GST_STATE_NULL);
    }
    timing_.reset();
    if (stamp_pad_) {gst_object_unref(stamp_pad_); stamp_pad_ = nullptr;}
    if (bus_watch_id_ != 0) {
      g_source_remove(bus_watch_id_);
      bus_watch_id_ = 0;
    }
    if (bus_ != nullptr) {
      gst_object_unref(bus_);
      bus_ = nullptr;
    }
    if (appsrc_ != nullptr) {
      gst_object_unref(appsrc_);
      appsrc_ = nullptr;
    }
    if (pipeline_ != nullptr) {
      gst_object_unref(pipeline_);
      pipeline_ = nullptr;
    }
  }

  void schedule_v4l2_restart(const std::string & reason)
  {
    v4l2_restart_failures_ = std::min(v4l2_restart_failures_ + 1, 6);
    const int exponential_delay = kMinV4L2RestartDelaySeconds << (v4l2_restart_failures_ - 1);
    const int delay_seconds = std::min(exponential_delay, kMaxV4L2RestartDelaySeconds);
    next_v4l2_restart_at_ =
      std::chrono::steady_clock::now() + std::chrono::seconds(delay_seconds);
    RCLCPP_WARN(
      logger_,
      "%s; retrying in %d s",
      reason.c_str(),
      delay_seconds);
  }

  rtp_send_time::Pacer pacer_;
  int wire_fd_{-1};
  int profile_wire_kbps_{0};
  std::map<pid_t, std::chrono::steady_clock::time_point> retired_deadlines_;
  GstPad * stamp_pad_{nullptr};
  int control_fd_{-1};
  uint64_t last_keyframe_us_{0};
  std::unique_ptr<FrameTiming> timing_;
  StreamConfig config_;
  std::string base_host_;
  rclcpp::Logger logger_;
  GstElement * pipeline_{nullptr};
  GstElement * appsrc_{nullptr};
  GstBus * bus_{nullptr};
  guint bus_watch_id_{0};
  pid_t gst_child_pid_{-1};
  int input_width_{0};
  int input_height_{0};
  int output_width_{0};
  int output_height_{0};
  GstClockTime frame_duration_ns_{0};
  std::chrono::steady_clock::time_point last_ros_frame_at_{};
  std::atomic<bool> pipeline_failed_{false};
  GstClockTime frame_index_{0};
  bool stopping_{false};
  bool enabled_{true};
  int maximum_kbps_;
  int output_fps_;
  bool adapted_{false};
  int recovery_level_{0};
  int recovery_wire_kbps_{0}, encoder_vbv_bits_{0};
  uint32_t sender_encoded_{0}, sender_admitted_{0}, last_sender_admitted_{0},
  last_sender_encoded_{0};
  std::chrono::steady_clock::time_point last_encoding_progress_{};
  std::chrono::steady_clock::time_point last_sender_progress_{}, last_recovery_change_{},
    next_recovery_upgrade_{};
  video_control::Profile candidate_{0, 0, 0};
  std::chrono::steady_clock::time_point candidate_since_{}, last_profile_change_{};
  int v4l2_restart_failures_{0};
  bool v4l2_child_reported_stable_{false};
  bool last_device_present_{false};
  std::vector<pid_t> retired_pids_;
  pid_t capture_check_pid_{-1};
  std::chrono::steady_clock::time_point capture_check_started_{};
  std::chrono::steady_clock::time_point gst_child_started_at_{};
  std::chrono::steady_clock::time_point next_v4l2_restart_at_{
    std::chrono::steady_clock::time_point::max()};

  static gboolean handle_bus_message(GstBus * /* bus */, GstMessage * message, gpointer user_data)
  {
    auto * self = static_cast<StreamPipeline *>(user_data);
    return self->on_bus_message(message);
  }

  gboolean on_bus_message(GstMessage * message)
  {
    switch (GST_MESSAGE_TYPE(message)) {
      case GST_MESSAGE_ERROR: {
          pipeline_failed_ = true;
          GError * error = nullptr;
          gchar * debug = nullptr;
          gst_message_parse_error(message, &error, &debug);
          RCLCPP_ERROR(
            logger_,
            "Stream %s GStreamer error from %s: %s%s%s",
            config_.stream_id.c_str(),
            GST_OBJECT_NAME(message->src),
            error != nullptr ? error->message : "unknown error",
            debug != nullptr ? " | " : "",
            debug != nullptr ? debug : "");
          if (error != nullptr) {
            g_error_free(error);
          }
          if (debug != nullptr) {
            g_free(debug);
          }
          break;
        }
      case GST_MESSAGE_WARNING: {
          GError * warning = nullptr;
          gchar * debug = nullptr;
          gst_message_parse_warning(message, &warning, &debug);
          RCLCPP_WARN(
            logger_,
            "Stream %s GStreamer warning from %s: %s%s%s",
            config_.stream_id.c_str(),
            GST_OBJECT_NAME(message->src),
            warning != nullptr ? warning->message : "unknown warning",
            debug != nullptr ? " | " : "",
            debug != nullptr ? debug : "");
          if (warning != nullptr) {
            g_error_free(warning);
          }
          if (debug != nullptr) {
            g_free(debug);
          }
          break;
        }
      case GST_MESSAGE_STATE_CHANGED: {
          if (GST_MESSAGE_SRC(message) == GST_OBJECT(pipeline_)) {
            GstState old_state = GST_STATE_NULL;
            GstState new_state = GST_STATE_NULL;
            GstState pending_state = GST_STATE_NULL;
            gst_message_parse_state_changed(message, &old_state, &new_state, &pending_state);
            RCLCPP_INFO(
              logger_,
              "Stream %s pipeline state %s -> %s (pending %s)",
              config_.stream_id.c_str(),
              gst_element_state_get_name(old_state),
              gst_element_state_get_name(new_state),
              gst_element_state_get_name(pending_state));
          }
          break;
        }
      default:
        break;
    }
    return G_SOURCE_CONTINUE;
  }

  void start_gst_launch_child(const std::vector<std::string> & args)
  {
    reset_sender_progress();
    encoder_vbv_bits_ = hardware_encoder() ? video_control::vbv_bits(
      config_.encoder.bitrate_kbps,
      output_fps_) : 0;
    std::vector<char *> argv;
    argv.reserve(args.size() + 1);
    for (const auto & arg : args) {
      argv.push_back(const_cast<char *>(arg.c_str()));
    }
    argv.push_back(nullptr);

    if (control_fd_ >= 0) {close(control_fd_); control_fd_ = -1;}
    int sockets[2];
    if (socketpair(AF_UNIX, SOCK_DGRAM | SOCK_NONBLOCK | SOCK_CLOEXEC, 0, sockets) != 0) {
      throw std::runtime_error("video control socketpair failed");
    }
    gst_child_pid_ = fork();
    if (gst_child_pid_ < 0) {
      close(sockets[0]); close(sockets[1]);
      throw std::runtime_error(
              "failed to fork gst-launch child for stream " + config_.stream_id + ": " +
              std::strerror(errno));
    }

    if (gst_child_pid_ == 0) {
      close(sockets[0]);
      if (wire_fd_ >= 0) {
        fcntl(wire_fd_, F_SETFD, 0);
        const auto wire_fd_string = std::to_string(wire_fd_);
        setenv("MR2_VIDEO_WIRE_FD", wire_fd_string.c_str(), 1);
      }
      fcntl(sockets[1], F_SETFD, 0);
      const auto fd_string = std::to_string(sockets[1]);
      setenv("MR2_VIDEO_CONTROL_FD", fd_string.c_str(), 1);
      const auto c = sender_control();
      std::ostringstream profile;
      profile << c.encoder_kbps << ' ' << c.wire_kbps << ' ' << c.width << ' ' << c.height << ' '
              << c.fps << ' ' << c.vbv << ' ' << c.key_interval << ' ' << c.flags;
      setenv("MR2_VIDEO_PROFILE", profile.str().c_str(), 1);
      execvp(argv[0], argv.data());
      std::fprintf(
        stderr,
        "video_streaming_node failed to exec %s for stream %s: %s\n",
        argv[0],
        config_.stream_id.c_str(),
        std::strerror(errno));
      _exit(127);
    }
    close(sockets[1]); control_fd_ = sockets[0];
    send_sender_control();
  }

  void reset_sender_progress()
  {
    sender_encoded_ = 0; sender_admitted_ = 0; last_sender_admitted_ = 0; last_sender_encoded_ = 0;
    last_sender_progress_ = std::chrono::steady_clock::now();
    last_encoding_progress_ = last_sender_progress_;
  }
  void poll_capture_check()
  {
    const auto now = std::chrono::steady_clock::now();
    if (capture_check_pid_ > 0) {
      int status = 0;
      const auto done = waitpid(capture_check_pid_, &status, WNOHANG);
      if (done == capture_check_pid_) {
        capture_check_pid_ = -1;
        if (WIFEXITED(status) && WEXITSTATUS(status) == 0) {
          v4l2_restart_failures_ = 0; next_v4l2_restart_at_ = now;
          return;
        }
        schedule_v4l2_restart("Capture check failed for " + config_.stream_id);
      } else if (now - capture_check_started_ > std::chrono::seconds(2)) {
        kill(capture_check_pid_, SIGKILL); retired_pids_.push_back(capture_check_pid_);
        capture_check_pid_ = -1;
        schedule_v4l2_restart("Capture check timed out for " + config_.stream_id);
      }
      return;
    }
    if (!device_present() || now < next_v4l2_restart_at_ || !retired_pids_.empty()) {return;}
    // Validate one raw camera frame without using any video bandwidth. A
    // failed camera must not repeatedly take the JSON prefix's entire budget
    // away from healthy cameras while its retry timer runs.
    std::vector<std::string> args{
      (std::filesystem::read_symlink("/proc/self/exe").parent_path() / "video_sender").string(),
      config_.stream_id + "/capture-check", "v4l2src", "device=" + config_.v4l2_device,
      "num-buffers=1", "!", build_v4l2_source_caps(), "!", "fakesink", "sync=false", "async=false"};
    std::vector<char *> argv;
    for (auto & arg : args) {argv.push_back(arg.data());}
    argv.push_back(nullptr);
    capture_check_pid_ = fork();
    if (capture_check_pid_ == 0) {execvp(argv[0], argv.data()); _exit(127);}
    if (capture_check_pid_ < 0) {
      schedule_v4l2_restart("Capture check fork failed for " + config_.stream_id);
    } else {capture_check_started_ = now;}
  }
  bool hardware_encoder() const
  {
    return video_control::use_hardware_encoder(
      is_jetson_hardware_encoder(config_.encoder.type),
      output_width_ > 0 ? output_width_ : config_.width);
  }
};

StreamConfig parse_stream_config(const json & item)
{
  StreamConfig config;
  config.stream_id = item.at("stream_id").get<std::string>();
  config.high_priority = item.value("priority", std::string("low")) == "high";
  config.udp_port = item.at("udp_port").get<int>();
  config.width = item.value("width", 0);
  config.height = item.value("height", 0);
  config.framerate = std::max(1, item.value("framerate", 15));

  const json encoder = item.value("encoder", json::object());
  config.encoder.type = encoder.value("type", std::string("x264"));
  if (!is_supported_encoder_type(config.encoder.type)) {
    throw std::runtime_error(
            "stream " + config.stream_id + " has unsupported encoder.type " + config.encoder.type);
  }
  config.encoder.bitrate_kbps = std::max(100, encoder.value("bitrate_kbps", 1500));
  config.encoder.keyframe_interval = std::max(1, encoder.value("keyframe_interval", 30));
  config.encoder.speed_preset = encoder.value("speed_preset", std::string("ultrafast"));
  config.encoder.tune = encoder.value("tune", std::string("zerolatency"));

  if (!item.contains("source")) {
    throw std::runtime_error("stream " + config.stream_id + " is missing source");
  }

  const json source = item.at("source");
  const std::string source_type = source.at("type").get<std::string>();
  if (source_type == "ros_topic") {
    config.source_type = StreamSourceType::RosTopic;
    config.ros_topic = source.at("ros_topic").get<std::string>();
    config.ros_encoding = source.at("ros_encoding").get<std::string>();
  } else if (source_type == "v4l2") {
    config.source_type = StreamSourceType::V4L2;
    config.v4l2_device = source.at("device").get<std::string>();
    config.v4l2_pixel_format = source.value("pixel_format", std::string());
    config.v4l2_capture_width =
      source.value("capture_width", item.value("capture_width", config.width));
    config.v4l2_capture_height =
      source.value("capture_height", item.value("capture_height", config.height));
  } else {
    throw std::runtime_error(
            "stream " + config.stream_id + " has unsupported source.type " + source_type);
  }

  if (config.source_type == StreamSourceType::RosTopic) {
    if (!is_supported_ros_encoding(config.ros_encoding)) {
      throw std::runtime_error(
              "stream " + config.stream_id + " has unsupported ros_encoding " +
              config.ros_encoding);
    }
  } else if (config.v4l2_device.empty()) {
    throw std::runtime_error("stream " + config.stream_id + " is missing source.device");
  }

  return config;
}

std::vector<StreamConfig> load_streams_from_file(const std::string & config_path)
{
  std::ifstream input(config_path);
  if (!input.is_open()) {
    throw std::runtime_error("failed to open video config: " + config_path);
  }

  const json parsed = json::parse(input);
  const auto streams_json = parsed.at("streams");
  if (!streams_json.is_array() || streams_json.empty()) {
    throw std::runtime_error("video config must contain a non-empty streams array");
  }

  std::vector<StreamConfig> streams;
  streams.reserve(streams_json.size());
  std::set<std::string> stream_ids;
  std::set<std::string> ros_topics;
  std::set<std::string> v4l2_devices;
  std::set<int> udp_ports;

  for (const auto & item : streams_json) {
    StreamConfig stream = parse_stream_config(item);
    if (!stream_ids.insert(stream.stream_id).second) {
      throw std::runtime_error("duplicate stream_id " + stream.stream_id);
    }
    if (!udp_ports.insert(stream.udp_port).second) {
      throw std::runtime_error("duplicate udp_port " + std::to_string(stream.udp_port));
    }
    if (stream.source_type == StreamSourceType::RosTopic) {
      if (!ros_topics.insert(stream.ros_topic).second) {
        throw std::runtime_error("duplicate ros_topic " + stream.ros_topic);
      }
    } else if (!v4l2_devices.insert(stream.v4l2_device).second) {
      throw std::runtime_error("duplicate v4l2 device " + stream.v4l2_device);
    }
    streams.push_back(std::move(stream));
  }
  return streams;
}

class VideoStreamingNode : public rclcpp::Node
{
public:
  VideoStreamingNode()
  : rclcpp::Node("video_streaming"),
    video_config_path_(declare_parameter<std::string>("video_config_path", "")),
    base_host_(declare_parameter<std::string>("base_host", default_base_host())),
    stream_lease_timeout_s_(declare_parameter<double>("stream_lease_timeout_s", 60.0)),
    disabled_stream_ids_(parse_disabled_stream_ids(
        declare_parameter<std::string>("disabled_stream_ids", "")))
  {
    video_budget_kbps_ =
      int(std::clamp<int64_t>(declare_parameter<int>("initial_budget_kbps", 0), 0, 100000));
    if (video_config_path_.empty()) {
      throw std::runtime_error("video_config_path parameter is required");
    }

    std::unique_ptr<GMainLoop, decltype(& g_main_loop_unref)> gst_main_loop(
      g_main_loop_new(nullptr, FALSE), &g_main_loop_unref);
    if (!gst_main_loop) {
      throw std::runtime_error("failed to allocate GMainLoop");
    }

    const auto streams = load_streams_from_file(video_config_path_);
    if (!wire_clock_.create()) {
      RCLCPP_WARN(get_logger(), "Shared video pacer unavailable; using independent pacing");
    }
    probe_ = std::make_unique<link_probe::Sender>(base_host_, streams.front().udp_port);
    for (const auto & stream : streams) {
      auto pipeline = std::make_shared<StreamPipeline>(stream, base_host_, get_logger());
      pipeline->set_wire_clock(wire_clock_.fd());
      if (disabled_stream_ids_.count(stream.stream_id) > 0) {
        pipeline->set_enabled(false);
      }

      if (stream.source_type == StreamSourceType::RosTopic) {
        auto subscription = create_subscription<sensor_msgs::msg::Image>(
          stream.ros_topic,
          rclcpp::SensorDataQoS(),
          [pipeline](const sensor_msgs::msg::Image::SharedPtr msg) {
            pipeline->push_frame(*msg);
          });
        subscriptions_.push_back(subscription);
      }

      pipelines_.push_back(pipeline);
      const std::string service_name =
        std::string(get_fully_qualified_name()) + "/streams/" + stream.stream_id + "/set_enabled";
      auto service = create_service<std_srvs::srv::SetBool>(
        service_name,
        [this, pipeline, stream_id = stream.stream_id](
          const std::shared_ptr<std_srvs::srv::SetBool::Request> request,
          std::shared_ptr<std_srvs::srv::SetBool::Response> response) {
          expire_stream_lease_if_needed();
          if (stream_lease_active()) {
            response->success = false;
            response->message = "stream configuration is locked by latency trial " +
            stream_lease_owner_;
            return;
          }
          const bool changed = pipeline->set_enabled(request->data);
          response->success = true;
          response->message =
          "stream " + stream_id + (pipeline->is_enabled() ? " enabled" : " disabled");
          if (!changed) {
            response->message += " (already in requested state)";
          }
        });
      stream_enable_services_.push_back(service);

      std::ostringstream details;
      details << "Configured video stream " << stream.stream_id
              << " source=" << source_type_to_string(stream.source_type)
              << " port=" << stream.udp_port
              << " service=" << service_name;
      if (stream.source_type == StreamSourceType::RosTopic) {
        details << " topic=" << stream.ros_topic
                << " encoding=" << stream.ros_encoding;
      } else {
        details << " device=" << stream.v4l2_device;
        if (!stream.v4l2_pixel_format.empty()) {
          details << " pixel_format=" << stream.v4l2_pixel_format;
        }
        if (stream.v4l2_capture_width > 0 && stream.v4l2_capture_height > 0) {
          details << " capture=" << stream.v4l2_capture_width << "x"
                  << stream.v4l2_capture_height;
        }
      }
      if (stream.width > 0 && stream.height > 0) {
        details << " output=" << stream.width << "x" << stream.height;
      }
      RCLCPP_INFO(get_logger(), "%s", details.str().c_str());
    }

    acquire_stream_lease_service_ =
      create_service<mr2_latency_msgs::srv::AcquireVideoStreamLease>(
      std::string(get_fully_qualified_name()) + "/acquire_stream_lease",
      [this](
        const std::shared_ptr<mr2_latency_msgs::srv::AcquireVideoStreamLease::Request> request,
        std::shared_ptr<mr2_latency_msgs::srv::AcquireVideoStreamLease::Response> response) {
        acquire_stream_lease(*request, *response);
      });
    release_stream_lease_service_ =
      create_service<mr2_latency_msgs::srv::ReleaseVideoStreamLease>(
      std::string(get_fully_qualified_name()) + "/release_stream_lease",
      [this](
        const std::shared_ptr<mr2_latency_msgs::srv::ReleaseVideoStreamLease::Request> request,
        std::shared_ptr<mr2_latency_msgs::srv::ReleaseVideoStreamLease::Response> response) {
        release_stream_lease(*request, *response);
      });

    state_pub_ = create_publisher<std_msgs::msg::UInt8MultiArray>("/video/state", 20);
    control_sub_ = create_subscription<std_msgs::msg::UInt8MultiArray>(
      "/video/control", 10,
      [this](std_msgs::msg::UInt8MultiArray::ConstSharedPtr msg) {
        handle_video_control(msg->data);
      });
    rebalance_video();
    monitor_timer_ = create_wall_timer(
      std::chrono::milliseconds(500), [this]() {
        expire_stream_lease_if_needed();
        for (const auto & pipeline : pipelines_) {pipeline->refresh_device_presence();}
        if (!stream_lease_active()) {
          const auto age = std::chrono::steady_clock::now() - last_feedback_at_;
          if (have_feedback_ && age > std::chrono::seconds(2)) {
            video_budget_kbps_ = 0;
          }
          rebalance_video();
        }
        for (const auto & pipeline : pipelines_) {
          pipeline->start_if_needed();
          pipeline->poll_runtime();
        }
        if (++state_tick_ % 2 == 0) {publish_video_states();}
      });

    gst_main_loop_ = gst_main_loop.get();
    gst_main_loop_thread_ = std::thread(
      [this]() {
        g_main_loop_run(gst_main_loop_);
      });
    gst_main_loop.release();
  }

  ~VideoStreamingNode() override
  {
    if (gst_main_loop_ != nullptr) {
      g_main_loop_quit(gst_main_loop_);
    }
    if (gst_main_loop_thread_.joinable()) {
      gst_main_loop_thread_.join();
    }
    if (gst_main_loop_ != nullptr) {
      g_main_loop_unref(gst_main_loop_);
      gst_main_loop_ = nullptr;
    }
  }

private:
  void rebalance_video()
  {
    std::vector<video_control::Demand> demands;
    for (const auto & p : pipelines_) {
      demands.push_back({p->high_priority(), p->allocation_capacity()});
    }
    const auto rates = video_control::allocate(video_budget_kbps_, demands);
    int total = 0;
    for (const auto rate : rates) {total += rate;}
    wire_clock_.set_rate(total);
    for (size_t i = 0; i < pipelines_.size(); ++i) {
      pipelines_[i]->apply_budget(rates[i]);
    }
  }
  void publish_video_states(uint16_t selected = 0, uint8_t result = 0)
  {
    for (const auto & p : pipelines_) {
      if (selected && p->port() != selected) {continue;}
      std_msgs::msg::UInt8MultiArray msg;
      msg.data.push_back(video_control::kState);
      video_control::append(msg.data, boot_id_);
      const auto ack = command_acks_[p->port()];
      video_control::append(msg.data, ack.first);
      video_control::append(msg.data, ack.second);
      p->append_status(msg.data);
      msg.data.push_back(selected ? result : command_results_[p->port()]);
      state_pub_->publish(msg);
    }
  }
  void handle_video_control(const std::vector<uint8_t> & b)
  {
    if (b.empty() ||
      (b[0] == video_control::kKeyframe ? b.size() != 11 : b[0] ==
      video_control::kSet ? b.size() != 13 :
      b[0] == video_control::kFeedback ? (b.size() != 23 && b.size() != 31) : true)) {return;}
    const auto session = video_control::read(b, 1);
    const auto request = video_control::read(b, 5);
    if (have_session_ && session != control_session_ &&
      !video_control::newer(session, control_session_)) {return;}
    if (!have_session_ || session != control_session_) {
      control_session_ = session; have_session_ = true; feedback_seq_ = 0; have_feedback_ = false;
      last_probe_id_ = 0; probe_->cancel();
      video_budget_kbps_ = 0; rebalance_video();
    }
    expire_stream_lease_if_needed();
    if (b[0] == video_control::kKeyframe) {
      const auto port = video_control::read(b, 9, 2);
      for (const auto & p : pipelines_) {if (p->port() == int(port)) {p->request_keyframe();}}
      return;
    }
    if (b[0] == video_control::kFeedback) {
      if (have_feedback_ && !video_control::newer(request, feedback_seq_)) {return;}
      feedback_seq_ = request; have_feedback_ = true;
      last_feedback_at_ = std::chrono::steady_clock::now();
      if (!stream_lease_active()) {
        video_budget_kbps_ = std::clamp(int(video_control::read(b, 9)), 0, 100000);
        rebalance_video();
        if (b.size() == 31) {
          const auto id = uint16_t(video_control::read(b, 23, 2));
          if (id && id != last_probe_id_ && wire_clock_.backlog(rtp_send_time::mono_us()) < 20000) {
            last_probe_id_ = id;
            const int target = std::clamp(int(video_control::read(b, 25)), 0, 100000);
            const int receive = std::clamp(int(video_control::read(b, 13)), 0, 100000);
            const int padding = video_budget_kbps_ == 0 ? std::clamp(target, 0, 8) :
              video_budget_kbps_ < 200 ? std::clamp(
              int(std::ceil(
                target * .9)), 0, video_budget_kbps_ * 2) :
              std::clamp(target - receive, 0, video_budget_kbps_ * 2);
            probe_->start(
              id, padding, std::clamp(
                int(video_control::read(
                  b, 29,
                  2)), 0, 200),
              control_session_);
          }
        }
      }
      return;
    }
    const auto port = video_control::read(b, 9, 2);
    if (b[11] > 1 || b[12] > 1) {return;}
    for (const auto & p : pipelines_) {
      if (p->port() != int(port)) {continue;}
      auto & ack = command_acks_[port];
      if (ack.first == session && ack.second != request &&
        !video_control::newer(request, ack.second)) {return;}
      const bool duplicate = ack.first == session && ack.second == request;
      if (duplicate) {publish_video_states(port, command_results_[port]); return;}
      ack = {session, request};
      command_results_[port] = stream_lease_active() ? 1 : 0;
      if (stream_lease_active()) {publish_video_states(port, 1); return;}
      if (!duplicate) {
        p->set_priority(b[12] != 0);
        p->set_enabled(b[11] != 0);
        rebalance_video();
      }
      publish_video_states(port);
      return;
    }
  }
  bool stream_lease_active() const
  {
    return !stream_lease_owner_.empty() &&
           std::chrono::steady_clock::now() < stream_lease_expires_at_;
  }

  void expire_stream_lease_if_needed()
  {
    if (!stream_lease_owner_.empty() &&
      std::chrono::steady_clock::now() >= stream_lease_expires_at_)
    {
      const auto owner = stream_lease_owner_;
      restore_stream_lease();
      RCLCPP_WARN(get_logger(), "Expired video stream lease for %s", owner.c_str());
    }
  }

  std::shared_ptr<StreamPipeline> find_pipeline(const std::string & stream_id) const
  {
    const auto found = std::find_if(
      pipelines_.begin(), pipelines_.end(), [&stream_id](const auto & pipeline) {
        return pipeline->stream_id() == stream_id;
      });
    return found == pipelines_.end() ? nullptr : *found;
  }

  void acquire_stream_lease(
    const mr2_latency_msgs::srv::AcquireVideoStreamLease::Request & request,
    mr2_latency_msgs::srv::AcquireVideoStreamLease::Response & response)
  {
    expire_stream_lease_if_needed();
    if (!is_valid_lease_owner(request.owner_id)) {
      response.message = "invalid lease owner";
      return;
    }
    if (stream_lease_active()) {
      response.message = "video streams are already leased by " + stream_lease_owner_;
      return;
    }
    if (request.stream_ids.empty()) {
      response.message = "at least one stream is required";
      return;
    }

    std::set<std::string> selected;
    for (const auto & stream_id : request.stream_ids) {
      if (!find_pipeline(stream_id)) {
        response.message = "unknown stream " + stream_id;
        return;
      }
      selected.insert(stream_id);
    }

    stream_lease_previous_states_.clear();
    for (const auto & pipeline : pipelines_) {
      stream_lease_previous_states_[pipeline->stream_id()] = pipeline->is_enabled();
      if (pipeline->is_enabled()) {
        response.previous_stream_ids.push_back(pipeline->stream_id());
      }
      pipeline->set_enabled(selected.count(pipeline->stream_id()) > 0);
      if (pipeline->is_enabled()) {
        response.effective_stream_ids.push_back(pipeline->stream_id());
      }
    }
    stream_lease_owner_ = request.owner_id;
    rebalance_video();
    const double requested_timeout = request.lease_timeout_s > 0.0 ?
      request.lease_timeout_s : stream_lease_timeout_s_;
    const double timeout_s = std::clamp(requested_timeout, 5.0, 300.0);
    stream_lease_expires_at_ = std::chrono::steady_clock::now() +
      std::chrono::milliseconds(static_cast<int64_t>(timeout_s * 1000.0));
    response.accepted = true;
    response.message = "video stream lease acquired";
  }

  std::vector<std::string> restore_stream_lease()
  {
    std::vector<std::string> restored;
    for (const auto & pipeline : pipelines_) {
      const auto previous = stream_lease_previous_states_.find(pipeline->stream_id());
      if (previous == stream_lease_previous_states_.end()) {
        continue;
      }
      pipeline->set_enabled(previous->second);
      if (pipeline->is_enabled()) {
        restored.push_back(pipeline->stream_id());
      }
    }
    stream_lease_owner_.clear();
    rebalance_video();
    stream_lease_previous_states_.clear();
    stream_lease_expires_at_ = std::chrono::steady_clock::time_point::min();
    return restored;
  }

  void release_stream_lease(
    const mr2_latency_msgs::srv::ReleaseVideoStreamLease::Request & request,
    mr2_latency_msgs::srv::ReleaseVideoStreamLease::Response & response)
  {
    expire_stream_lease_if_needed();
    if (stream_lease_owner_.empty()) {
      response.message = "no active video stream lease";
      return;
    }
    if (request.owner_id != stream_lease_owner_) {
      response.message = "lease owner does not match";
      return;
    }
    response.restored_stream_ids = restore_stream_lease();
    response.released = true;
    response.message = "video stream lease released";
  }

  static std::string default_base_host()
  {
    const char * env_base_ip = std::getenv("MR2_BASE_IP");
    if (env_base_ip != nullptr && env_base_ip[0] != '\0') {
      return env_base_ip;
    }
    const auto env_file_base_ip = read_env_file_value("MR2_BASE_IP");
    if (!env_file_base_ip.empty()) {
      return env_file_base_ip;
    }
    throw std::runtime_error("MR2_BASE_IP environment variable is required");
  }

  static std::string read_env_file_value(const std::string & name)
  {
    std::error_code ec;
    auto directory = std::filesystem::current_path(ec);
    if (ec) {
      return "";
    }

    while (true) {
      const auto env_path = directory / ".env";
      if (std::filesystem::is_regular_file(env_path, ec)) {
        std::ifstream input(env_path);
        std::string line;
        while (std::getline(input, line)) {
          const auto first = line.find_first_not_of(" \t");
          if (first == std::string::npos || line[first] == '#') {
            continue;
          }
          const auto equals = line.find('=', first);
          if (equals == std::string::npos) {
            continue;
          }
          const auto key_end = line.find_last_not_of(" \t", equals - 1);
          const auto key = line.substr(first, key_end - first + 1);
          if (key != name) {
            continue;
          }
          const auto value_start = line.find_first_not_of(" \t", equals + 1);
          if (value_start == std::string::npos) {
            return "";
          }
          const auto value_end = line.find_last_not_of(" \t\r");
          auto value = line.substr(value_start, value_end - value_start + 1);
          if (value.size() >= 2 &&
            ((value.front() == '"' && value.back() == '"') ||
            (value.front() == '\'' && value.back() == '\'')))
          {
            value = value.substr(1, value.size() - 2);
          }
          return value;
        }
      }

      const auto parent = directory.parent_path();
      if (parent == directory) {
        break;
      }
      directory = parent;
    }
    return "";
  }

  rclcpp::Publisher<std_msgs::msg::UInt8MultiArray>::SharedPtr state_pub_;
  rclcpp::Subscription<std_msgs::msg::UInt8MultiArray>::SharedPtr control_sub_;
  uint32_t boot_id_{uint32_t(rtp_send_time::mono_us()) ^ uint32_t(getpid())};
  unsigned state_tick_{0};
  uint32_t control_session_{0}, feedback_seq_{0};
  bool have_session_{false}, have_feedback_{false};
  int video_budget_kbps_{0};
  SharedWireClock wire_clock_;
  std::unique_ptr<link_probe::Sender> probe_;
  uint16_t last_probe_id_{0};
  std::map<int, std::pair<uint32_t, uint32_t>> command_acks_;
  std::map<int, uint8_t> command_results_;
  std::chrono::steady_clock::time_point last_feedback_at_{};
  std::string video_config_path_;
  std::string base_host_;
  double stream_lease_timeout_s_{60.0};
  std::set<std::string> disabled_stream_ids_;
  std::string stream_lease_owner_;
  std::map<std::string, bool> stream_lease_previous_states_;
  std::chrono::steady_clock::time_point stream_lease_expires_at_{
    std::chrono::steady_clock::time_point::min()};
  GMainLoop * gst_main_loop_{nullptr};
  std::thread gst_main_loop_thread_;
  rclcpp::TimerBase::SharedPtr monitor_timer_;
  std::vector<std::shared_ptr<StreamPipeline>> pipelines_;
  std::vector<rclcpp::Subscription<sensor_msgs::msg::Image>::SharedPtr> subscriptions_;
  std::vector<rclcpp::Service<std_srvs::srv::SetBool>::SharedPtr> stream_enable_services_;
  rclcpp::Service<mr2_latency_msgs::srv::AcquireVideoStreamLease>::SharedPtr
    acquire_stream_lease_service_;
  rclcpp::Service<mr2_latency_msgs::srv::ReleaseVideoStreamLease>::SharedPtr
    release_stream_lease_service_;
};

}  // namespace mr2_video_streaming

int main(int argc, char ** argv)
{
  gst_init(&argc, &argv);
  rclcpp::init(argc, argv);

  try {
    auto node = std::make_shared<mr2_video_streaming::VideoStreamingNode>();
    rclcpp::spin(node);
  } catch (const std::exception & error) {
    std::fprintf(stderr, "video_streaming_node failed: %s\n", error.what());
    rclcpp::shutdown();
    return 1;
  }

  rclcpp::shutdown();
  return 0;
}
