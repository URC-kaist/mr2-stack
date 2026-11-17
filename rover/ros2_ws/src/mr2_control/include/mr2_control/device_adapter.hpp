#pragma once
#include "mr2_can_bus_core/can_device.hpp"
#include "mr2_control/shutdown_sender.hpp"
#include "pluginlib/class_loader.hpp"
#include <chrono>
#include <cmath>
#include <map>
#include <memory>
#include <stdexcept>
#include <string>

namespace mr2_control {
// No thread or command mailbox. The owner serializes all lifecycle/command
// calls.
class DeviceAdapter {
public:
  struct Entry {
    std::shared_ptr<CanDevice> device;
    CanFeedback feedback, simulated;
    std::shared_ptr<ShutdownSender> shutdown_sender;
    double *command{};
    bool has_velocity{}, has_effort{}, velocity_mode{};
    double mock_command{std::numeric_limits<double>::quiet_NaN()};
  };
  explicit DeviceAdapter(rclcpp::Node::SharedPtr node, bool mock = false)
      : node_(std::move(node)), mock_(mock) {}
  ~DeviceAdapter() { stop(); }
  Entry &add(const hardware_interface::ComponentInfo &joint,
             const std::string &plugin, const std::string &name) {
    if (entries_.count(name))
      return entries_.at(name);
    if (mock_) {
      auto &entry = entries_[name];
      order_.push_back(&entry);
      entry.command = &entry.mock_command;
      entry.velocity_mode = joint.command_interfaces.at(0).name == "velocity";
      for (const auto &state : joint.state_interfaces) {
        entry.has_velocity |= state.name == "velocity";
        entry.has_effort |= state.name == "effort";
      }
      entry.feedback = entry.simulated = {0.0, 0.0, 0.0, 0, 0};
      return entry;
    }
    if (!loader_)
      loader_ = std::make_unique<pluginlib::ClassLoader<CanDevice>>(
          "mr2_can_bus_core", "CanDevice");
    // Keep ownership even if configuration fails after registering a listener.
    auto &entry = entries_[name];
    order_.push_back(&entry);
    entry.device = loader_->createSharedInstance(plugin);
    entry.shutdown_sender =
        std::make_shared<ShutdownSender>(joint.parameters.at("can_iface"));
    entry.device->configure(joint, node_.get());
    entry.velocity_mode = joint.command_interfaces.at(0).name == "velocity";
    double *position{}, *velocity{}, *effort{};
    entry.device->export_state(position, velocity, effort);
    entry.device->export_command(entry.command);
    entry.has_velocity = velocity != nullptr;
    entry.has_effort = effort != nullptr;
    if (!position || !entry.command ||
        !entry.device->copy_feedback(entry.feedback))
      throw std::runtime_error("Device lacks coherent feedback/command API: " +
                               name);
    return entry;
  }
  bool activate() {
    activated_ = true; // Also clean up partial activation if a driver throws.
    for (auto *entry_ptr : order_) {
      auto &entry = *entry_ptr;
      if (!rclcpp::ok(node_->get_node_base_interface()->get_context())) {
        deactivate();
        return false;
      }
      if (entry.device && !entry.device->on_activate()) {
        deactivate();
        return false;
      }
    }
    activated_ = true;
    return true;
  }
  void deactivate() {
    activated_ = false;
    // The runtime has already joined its control loop. Discard pending normal
    // targets before sending terminal frames, so stale targets cannot follow.
    for (auto *entry : order_)
      if (entry->device)
        entry->device->stop_transport();
    for (auto *entry : order_) {
      if (!entry->device || !entry->shutdown_sender)
        continue;
      entry->device->set_tx_override([this, entry](const can_frame &frame) {
        if (!entry->shutdown_sender->send(frame))
          RCLCPP_ERROR(node_->get_logger(),
                       "Shutdown CAN write failed for id 0x%x: %s",
                       frame.can_id, std::strerror(errno));
      });
      entry->device->on_deactivate();
    }
  }
  void copyFeedback() {
    for (auto *entry : order_)
      if (entry->device)
        entry->device->copy_feedback(entry->feedback);
      else
        entry->feedback = entry->simulated;
  }
  void process(const rclcpp::Time &now) {
    const auto stamp = std::chrono::steady_clock::now().time_since_epoch();
    const auto ns =
        std::chrono::duration_cast<std::chrono::nanoseconds>(stamp).count();
    for (auto *entry : order_) {
      if (entry->device) {
        entry->device->process(now);
        continue;
      }
      const double dt =
          entry->simulated.velocity_received_ns > 0
              ? (ns - entry->simulated.velocity_received_ns) * 1e-9
              : 0.0;
      if (std::isfinite(*entry->command)) {
        if (entry->velocity_mode) {
          entry->simulated.velocity = *entry->command;
          entry->simulated.position += *entry->command * dt;
        } else
          entry->simulated.position = *entry->command;
      }
      entry->simulated.position_received_ns =
          entry->simulated.velocity_received_ns = ns;
    }
  }
  void stop() {
    if (activated_)
      deactivate();
    for (auto *entry : order_)
      if (entry->device)
        entry->device->stop_transport();
  }
  const auto &entries() const { return entries_; }

private:
  // Destruction: stop callbacks first; devices before loader and node.
  rclcpp::Node::SharedPtr node_;
  std::unique_ptr<pluginlib::ClassLoader<CanDevice>> loader_;
  std::map<std::string, Entry> entries_;
  std::vector<Entry *> order_;
  bool mock_;
  bool activated_{};
};
} // namespace mr2_control
