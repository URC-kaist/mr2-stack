#include "mr2_can_bus_core/can_device.hpp"
#include "mr2_devices_robstride/protocol.hpp"
#include <algorithm>
#include <chrono>
#include <cmath>
#include <condition_variable>
#include <functional>
#include <mutex>
#include <stdexcept>
#include <string>
#include <pluginlib/class_list_macros.hpp>

namespace mr2_devices_robstride {
namespace p = protocol;

class RobstrideDevice : public CanDevice {
  friend struct DeviceTestPeer;

public:
  void configure(const hardware_interface::ComponentInfo &info,
                 rclcpp::Node *node) override {
    node_ = node;
    if (node_) logger_ = node_->get_logger();
    configure_parameters(info);
    bus_ = CanBusRegistry::get(iface_);
    if (!bus_) throw std::runtime_error("Cannot open CAN interface " + iface_);
    for (const auto type : {p::kFeedback, p::kRead, p::kFault})
      add_filter(bus_, p::reply_id(type, host_id_, motor_id_), p::kReplyMask,
                 [this](const can_frame &frame) {
                   receive_at(frame, clock_.now().nanoseconds());
                 });
  }

  bool on_activate() override {
    active_ = false;
    tripped_ = false;
    streaming_ = false;
    priming_start_ns_ = 0;
    desired_velocity_ = 0.0;
    {
      std::lock_guard<std::mutex> lock(mutex_);
      rx_ = {};
    }
    // Stop first: never switch modes while running. Each step needs a new
    // status frame; cached telemetry must not satisfy the next step.
    if (!exchange(p::command(p::kStop, host_id_, motor_id_), 0) ||
        !exchange(p::parameter(p::kWrite, host_id_, motor_id_, p::kRunMode, 2)) ||
        !confirm_velocity_mode() ||
        !exchange(p::write_float(host_id_, motor_id_, p::kCurrentLimit,
                                static_cast<float>(current_limit_a_))) ||
        !exchange(p::write_float(host_id_, motor_id_, p::kAcceleration,
                                static_cast<float>(acceleration_))) ||
        !exchange(p::parameter(p::kWrite, host_id_, motor_id_, p::kCanTimeout,
                              0)) ||
        !exchange(p::write_float(host_id_, motor_id_, p::kSpeed, 0.0f)) ||
        !exchange(p::command(p::kEnable, host_id_, motor_id_), 2)) {
      RCLCPP_ERROR(logger_, "RS-06 motor %u activation failed; stopping", motor_id_);
      on_deactivate();
      return false;
    }
    active_ = true;
    return true;
  }

  void on_deactivate() override {
    active_ = false;
    tripped_ = false;
    streaming_ = false;
    desired_velocity_ = 0.0;
    // Use CanDevice::send so both CanHW's queue and mr2_control's terminal
    // transmit override get the same zero/stop sequence.
    send(p::write_float(host_id_, motor_id_, p::kSpeed, 0.0f), bus_);
    send(p::command(p::kStop, host_id_, motor_id_), bus_);
  }

  void process(const rclcpp::Time &) override {
    process_at(clock_.now().nanoseconds());
  }

  void export_state(double *&position, double *&velocity, double *&effort) override {
    position = &exported_.position;
    velocity = &exported_.velocity;
    effort = &exported_.effort;
  }
  void export_command(double *&command) override { command = &desired_velocity_; }

  bool copy_feedback(CanFeedback &feedback) override {
    std::lock_guard<std::mutex> lock(mutex_);
    feedback = rx_.feedback;
    if (rx_.fault_latched)
      feedback.velocity = feedback.effort = std::numeric_limits<double>::quiet_NaN();
    exported_ = feedback;
    return true;
  }

private:
  void configure_parameters(const hardware_interface::ComponentInfo &info) {
    const auto required = [&](const char *key) -> const std::string & {
      const auto it = info.parameters.find(key);
      if (it == info.parameters.end() || it->second.empty())
        throw std::runtime_error(std::string("Missing ") + key);
      return it->second;
    };
    const auto integer = [&](const char *key, const std::string &value,
                             int minimum, int maximum) {
      size_t used = 0;
      const auto n = std::stoi(value, &used);
      if (used != value.size() || n < minimum || n > maximum)
        throw std::runtime_error(std::string("Invalid ") + key);
      return n;
    };
    const auto real = [&](const char *key, double fallback, double maximum) {
      const auto it = info.parameters.find(key);
      if (it == info.parameters.end()) return fallback;
      size_t used = 0;
      const auto n = std::stod(it->second, &used);
      if (used != it->second.size() || !std::isfinite(n) || n <= 0 || n > maximum)
        throw std::runtime_error(std::string("Invalid ") + key);
      return n;
    };
    const auto milliseconds = [&](const char *key, int fallback, int maximum) {
      const auto it = info.parameters.find(key);
      return it == info.parameters.end() ? fallback :
             integer(key, it->second, 1, maximum);
    };
    if (info.command_interfaces.size() != 1 ||
        info.command_interfaces.front().name != "velocity")
      throw std::runtime_error("RobstrideDevice requires one velocity command interface");
    iface_ = required("can_iface");
    motor_id_ = integer("motor_id", required("motor_id"), 1, 127);
    const auto host = info.parameters.find("host_id");
    host_id_ = host == info.parameters.end() ? 253 :
               integer("host_id", host->second, 0, 255);
    if (host_id_ == motor_id_)
      throw std::runtime_error("host_id and motor_id must differ");
    scale_.position_max = real("feedback_position_max_rad", 12.57, 1000000.0);
    scale_.velocity_max = real("feedback_velocity_max_rad_s", 50.0, 1000000.0);
    scale_.torque_max = real("feedback_torque_max_nm", 36.0, 1000000.0);
    max_velocity_ = real("max_velocity_rad_s", 20.0, 50.0);
    if (max_velocity_ > scale_.velocity_max)
      throw std::runtime_error("max_velocity_rad_s exceeds feedback velocity range");
    current_limit_a_ = real("current_limit_a", 14.3, 57.0);
    acceleration_ = real("acceleration_rad_s2", 20.0, 1000000.0);
    feedback_timeout_ms_ = milliseconds("feedback_timeout_ms", 200, 2000);
    can_timeout_ms_ = milliseconds("can_timeout_ms", 200, 2000);
    activation_timeout_ms_ = milliseconds("activation_timeout_ms", 1500, 10000);
    // Shortest-path unwrapping is ambiguous if the shaft can cross half the
    // feedback interval between samples. Reject such timeout configurations.
    if (feedback_timeout_ms_ * 0.001 * scale_.velocity_max >= scale_.position_max)
      throw std::runtime_error("feedback_timeout_ms too large for position unwrapping");
  }

  bool context_ok() const {
    return !node_ || rclcpp::ok(node_->get_node_base_interface()->get_context());
  }

  bool wait_for(const std::function<bool()> &predicate) {
    const auto deadline = std::chrono::steady_clock::now() +
                          std::chrono::milliseconds(activation_timeout_ms_);
    std::unique_lock<std::mutex> lock(mutex_);
    while (context_ok() && !rx_.fault_latched) {
      if (predicate()) return true; // predicate runs with mutex_ held
      if (std::chrono::steady_clock::now() >= deadline) break;
      condition_.wait_for(lock, std::chrono::milliseconds(5));
    }
    return false;
  }

  bool exchange(const can_frame &frame, int expected_mode = -1) {
    uint64_t before;
    {
      std::lock_guard<std::mutex> lock(mutex_);
      before = rx_.sequence;
    }
    if (!context_ok()) return false;
    send(frame, bus_);
    return wait_for([&] {
      return rx_.sequence > before &&
             (expected_mode < 0 || rx_.mode == expected_mode);
    });
  }

  bool confirm_velocity_mode() {
    uint64_t before;
    {
      std::lock_guard<std::mutex> lock(mutex_);
      before = rx_.mode_sequence;
    }
    send(p::parameter(p::kRead, host_id_, motor_id_, p::kRunMode), bus_);
    return wait_for([&] { return rx_.mode_sequence > before && rx_.run_mode == 2; });
  }

  void receive_at(const can_frame &frame, int64_t now_ns) {
    std::lock_guard<std::mutex> lock(mutex_);
    if (p::is_reply(frame, p::kFeedback, host_id_, motor_id_)) {
      const auto sample = p::decode(frame, scale_);
      rx_.fault_latched |= sample.faults != 0;
      if (!rx_.have_position) {
        rx_.feedback.position = sample.position;
        rx_.have_position = true;
      } else if (now_ns <= rx_.feedback.position_received_ns ||
                 (now_ns - rx_.feedback.position_received_ns) * 1e-9 *
                         scale_.velocity_max >= scale_.position_max) {
        // A gap can hide complete revolutions. Do not fabricate odometry.
        if (rx_.monitoring) {
          rx_.fault_latched = true;
          rx_.feedback.position = std::numeric_limits<double>::quiet_NaN();
        } else {
          rx_.feedback.position = sample.position;
        }
      } else {
        rx_.feedback.position += std::remainder(sample.position - rx_.last_position,
                                               2.0 * scale_.position_max);
      }
      rx_.last_position = sample.position;
      rx_.feedback.velocity = sample.velocity;
      rx_.feedback.effort = sample.torque;
      rx_.feedback.position_received_ns = now_ns;
      rx_.feedback.velocity_received_ns = now_ns;
      rx_.mode = sample.mode;
      ++rx_.sequence;
    } else if (p::is_reply(frame, p::kRead, host_id_, motor_id_) &&
               (frame.can_id & 0x00ff0000U) == 0 &&
               p::u16_le(frame.data) == p::kRunMode &&
               frame.data[2] == 0 && frame.data[3] == 0) {
      rx_.run_mode = frame.data[4];
      ++rx_.mode_sequence;
    } else if (p::is_reply(frame, p::kFault, host_id_, motor_id_)) {
      // Type 21 carries fault and warning words. Latch either, requiring an
      // explicit deactivate/activate after the underlying cause is resolved.
      rx_.fault_latched |= p::u32_le(frame.data) != 0 || p::u32_le(frame.data + 4) != 0;
    } else {
      return;
    }
    condition_.notify_all();
  }

  void process_at(int64_t now_ns) {
    if (!active_ && !tripped_) return;
    if (active_ && !streaming_) {
      // CanHW activates motors sequentially (including an optional gripper
      // timeout). Keep a zero target and arm the firmware watchdog only when
      // the owner's write loop actually starts. Never send a nonzero target
      // before fresh running feedback has arrived in this phase.
      if (priming_start_ns_ == 0) {
        {
          std::lock_guard<std::mutex> lock(mutex_);
          priming_sequence_ = rx_.sequence;
          rx_.have_position = false;
        }
        priming_start_ns_ = now_ns;
        send(p::parameter(p::kWrite, host_id_, motor_id_, p::kCanTimeout,
                          uint32_t(can_timeout_ms_) * 20U), bus_);
        send(p::write_float(host_id_, motor_id_, p::kSpeed, 0.0f), bus_);
        return;
      }
      bool waiting = false;
      {
        std::lock_guard<std::mutex> lock(mutex_);
        if (!rx_.fault_latched && rx_.sequence > priming_sequence_ && rx_.mode == 2) {
          streaming_ = true;
          rx_.monitoring = true;
        } else if (!rx_.fault_latched &&
                   now_ns - priming_start_ns_ <= int64_t(feedback_timeout_ms_) * 1000000) {
          waiting = true;
        } else {
          rx_.fault_latched = true;
        }
      }
      if (waiting) {
        send(p::write_float(host_id_, motor_id_, p::kSpeed, 0.0f), bus_);
        return;
      }
    }
    bool failed;
    {
      std::lock_guard<std::mutex> lock(mutex_);
      const auto stamp = rx_.feedback.velocity_received_ns;
      failed = rx_.fault_latched || rx_.mode != 2 || stamp <= 0 ||
               now_ns < stamp || now_ns - stamp > int64_t(feedback_timeout_ms_) * 1000000;
      if (failed) {
        rx_.fault_latched = true;
        rx_.feedback.velocity = rx_.feedback.effort =
            std::numeric_limits<double>::quiet_NaN();
      }
    }
    CanFeedback latest;
    copy_feedback(latest);
    if (failed || tripped_) {
      if (!tripped_) {
        RCLCPP_ERROR(logger_, "RS-06 motor %u feedback/fault check failed; stopping until reactivation",
                     motor_id_);
        active_ = false;
        tripped_ = true;
        last_stop_ns_ = 0;
        desired_velocity_ = 0.0;
      }
      // Retry stop if it was dropped, without automatically re-enabling.
      if (last_stop_ns_ == 0 || now_ns - last_stop_ns_ >= 50000000) {
        send(p::write_float(host_id_, motor_id_, p::kSpeed, 0.0f), bus_);
        send(p::command(p::kStop, host_id_, motor_id_), bus_);
        last_stop_ns_ = now_ns;
      }
      return;
    }
    const auto velocity = std::isfinite(desired_velocity_) ?
        std::clamp(desired_velocity_, -max_velocity_, max_velocity_) : 0.0;
    send(p::write_float(host_id_, motor_id_, p::kSpeed,
                        static_cast<float>(velocity)), bus_);
  }

  struct Rx {
    CanFeedback feedback;
    uint64_t sequence{0}, mode_sequence{0};
    uint8_t mode{0}, run_mode{0};
    double last_position{0.0};
    bool have_position{false}, fault_latched{false}, monitoring{false};
  } rx_;
  std::mutex mutex_;
  std::condition_variable condition_;
  rclcpp::Node *node_{nullptr};
  rclcpp::Logger logger_{rclcpp::get_logger("robstride_device")};
  rclcpp::Clock clock_{RCL_STEADY_TIME};
  std::shared_ptr<CanBusManager> bus_;
  std::string iface_;
  uint8_t motor_id_{1}, host_id_{253};
  p::Scale scale_;
  double max_velocity_{20.0}, current_limit_a_{14.3}, acceleration_{20.0};
  int feedback_timeout_ms_{200}, can_timeout_ms_{200}, activation_timeout_ms_{1500};
  // Only lifecycle/control owner writes these; the CAN thread only writes rx_.
  bool active_{false}, tripped_{false}, streaming_{false};
  int64_t priming_start_ns_{0};
  uint64_t priming_sequence_{0};
  int64_t last_stop_ns_{0};
  double desired_velocity_{0.0};
  CanFeedback exported_;
};
} // namespace mr2_devices_robstride

PLUGINLIB_EXPORT_CLASS(mr2_devices_robstride::RobstrideDevice, CanDevice)
