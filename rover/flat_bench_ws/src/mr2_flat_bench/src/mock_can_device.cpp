#include "mr2_can_bus_core/can_device.hpp"
#include <pluginlib/class_list_macros.hpp>
#include <cmath>
#include <limits>
#include <stdexcept>

namespace mr2_flat_bench {
class MockCanDevice : public CanDevice {
  friend struct MockTestPeer;
public:
  void configure(const hardware_interface::ComponentInfo &info, rclcpp::Node *) override {
    if (info.command_interfaces.size() != 1 ||
        (info.command_interfaces[0].name != "position" && info.command_interfaces[0].name != "velocity"))
      throw std::runtime_error("MockCanDevice requires one position or velocity command interface");
    velocity_mode_ = info.command_interfaces[0].name == "velocity";
    has_velocity_ = velocity_mode_;
    for (const auto &state : info.state_interfaces) {
      has_velocity_ |= state.name == "velocity";
      has_effort_ |= state.name == "effort";
    }
    feedback_ = {0, 0, 0, 0, 0};
    // Deliberately never obtains a CanBusManager or registers a CAN listener.
  }
  bool on_activate() override {
    active_ = true;
    command_ = velocity_mode_ ? 0 : std::numeric_limits<double>::quiet_NaN();
    last_process_ns_ = clock_.now().nanoseconds();
    stamp(last_process_ns_);
    return true;
  }
  void on_deactivate() override {
    active_ = false;
    command_ = velocity_mode_ ? 0 : std::numeric_limits<double>::quiet_NaN();
    feedback_.velocity = 0;
  }
  void process(const rclcpp::Time &) override { advance(clock_.now().nanoseconds()); }
  void export_command(double *&command) override { command = &command_; }
  void export_state(double *&position, double *&velocity, double *&effort) override {
    position = &feedback_.position;
    velocity = has_velocity_ ? &feedback_.velocity : nullptr;
    effort = has_effort_ ? &feedback_.effort : nullptr;
  }
  bool copy_feedback(CanFeedback &out) override { out = feedback_; return true; }
private:
  void stamp(int64_t ns) {
    feedback_.position_received_ns = feedback_.velocity_received_ns = ns;
  }
  void advance(int64_t ns) {
    if (!active_) return;
    const auto elapsed = ns > last_process_ns_ ? (ns - last_process_ns_) * 1e-9 : 0.0;
    if (velocity_mode_) {
      feedback_.velocity = std::isfinite(command_) ? command_ : 0;
      feedback_.position += feedback_.velocity * elapsed;
    } else {
      const auto before = feedback_.position;
      if (std::isfinite(command_)) feedback_.position = command_;
      feedback_.velocity = elapsed > 0 ? (feedback_.position - before) / elapsed : 0;
    }
    feedback_.effort = 0;
    last_process_ns_ = ns;
    stamp(ns);
  }
  rclcpp::Clock clock_{RCL_STEADY_TIME};
  CanFeedback feedback_{0, 0, 0, 0, 0};
  double command_{std::numeric_limits<double>::quiet_NaN()};
  bool velocity_mode_{false}, active_{false};
  bool has_velocity_{false}, has_effort_{false};
  int64_t last_process_ns_{0};
};
} // namespace mr2_flat_bench
PLUGINLIB_EXPORT_CLASS(mr2_flat_bench::MockCanDevice, CanDevice)
