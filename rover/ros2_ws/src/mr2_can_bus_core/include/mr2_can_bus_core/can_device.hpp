#pragma once
#include "can_bus_registry.hpp"
#include <algorithm>
#include <hardware_interface/component_parser.hpp>
#include <hardware_interface/types/hardware_interface_type_values.hpp>
#include <limits>
#include <linux/can.h>
#include <memory>
#include <rclcpp/node.hpp>
#include <rclcpp/time.hpp>
#include <string>
#include <utility>
#include <vector>

struct CanFeedback {
  double position{std::numeric_limits<double>::quiet_NaN()};
  double velocity{std::numeric_limits<double>::quiet_NaN()};
  // Driver units (AK amperes), not a physical joint torque measurement.
  double effort{std::numeric_limits<double>::quiet_NaN()};
  int64_t position_received_ns{0};
  int64_t velocity_received_ns{0};
};

class CanDevice {
public:
  virtual ~CanDevice() = default;

  // Opt-in coherent snapshot API. Legacy-only plugins keep their old API.
  virtual bool copy_feedback(CanFeedback &) { return false; }

  /**
   * Configure the device with parameters taken from the joint / component
   * description and the ROS node that owns it.
   */
  virtual void configure(const hardware_interface::ComponentInfo &info,
                         rclcpp::Node *node) = 0;

  /**
   * Optional lifecycle hooks called by the owning SystemInterface.
   */
  virtual bool on_activate() { return true; }
  virtual void on_deactivate() {}

  /**
   * Called once per controller update cycle – typically send a CAN command.
   */
  virtual void process(const rclcpp::Time &now) = 0;

  /**
   * Export state arrays (pointers) so the ros2_control SystemInterface can
   * wire them into the hardware state interfaces (e.g.
   * position/velocity/effort) that controllers and broadcasters consume.
   */
  virtual void export_state(double *&position, double *&velocity,
                            double *&effort) = 0;

  /**
   * Export command array pointers (usually position commands).
   */
  virtual void export_command(double *&command) = 0;

  void stop_transport() {
    for (const auto &bus : buses_)
      bus->stop();
  }

  // Owner-thread-only override; empty preserves the shared bus path.
  void set_tx_override(std::function<void(const can_frame &)> sink) {
    tx_override_ = std::move(sink);
  }

protected:
  /** Shorthand for sending on the appropriate bus */
  void send(const struct can_frame &f,
            const std::shared_ptr<CanBusManager> &bus) {
    if (tx_override_)
      tx_override_(f);
    else
      bus->enqueue_tx(f);
  }

  /** Register a listener for this device on its bus */
  void add_filter(const std::shared_ptr<CanBusManager> &bus, uint32_t id,
                  uint32_t mask, CanBusManager::RxCallback cb) {
    if (std::find(buses_.begin(), buses_.end(), bus) == buses_.end())
      buses_.push_back(bus);
    bus->register_listener(id, mask, std::move(cb));
  }

private:
  std::function<void(const can_frame &)> tx_override_;
  std::vector<std::shared_ptr<CanBusManager>> buses_;
};
