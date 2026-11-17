#pragma once
#include "mr2_control/device_adapter.hpp"
#include "transmission_interface/four_bar_linkage_transmission_loader.hpp"
#include "transmission_interface/simple_transmission_loader.hpp"
#include "transmission_interface/transmission.hpp"
#include "transmission_interface/transmission_interface_exception.hpp"
#include <algorithm>
#include <cmath>
#include <limits>
#include <memory>
#include <set>
#include <stdexcept>
#include <string>
#include <unordered_map>
#include <vector>

namespace mr2_control {
// Ordinary, control-owned conversion state. URDF remains the configuration
// source.
class JointMotorConverter {
public:
  struct JointData {
    explicit JointData(std::string name_in) : name(std::move(name_in)) {}

    std::string name;
    double command{std::numeric_limits<double>::quiet_NaN()};
    double state{std::numeric_limits<double>::quiet_NaN()};
    double velocity{std::numeric_limits<double>::quiet_NaN()};
    double effort{std::numeric_limits<double>::quiet_NaN()};
    double lower_limit{-std::numeric_limits<double>::infinity()};
    double upper_limit{std::numeric_limits<double>::infinity()};
    double origin_offset{0.0};
    double boot_reference_position{std::numeric_limits<double>::quiet_NaN()};
    double transmission_passthrough{std::numeric_limits<double>::quiet_NaN()};
    double transmission_velocity{std::numeric_limits<double>::quiet_NaN()};
    double transmission_effort{std::numeric_limits<double>::quiet_NaN()};
    std::string command_interface{hardware_interface::HW_IF_POSITION};
    std::string actuator_name;
    bool has_velocity_state{false};
    bool has_effort_state{false};
    bool uses_boot_origin{false};
    bool boot_reference_valid{false};
  };

  struct DevicePointers {
    double *state_ptr{nullptr};
    double *velocity_ptr{nullptr};
    double *effort_ptr{nullptr};
    double *command_ptr{nullptr};
  };

  struct ActuatorData {
    explicit ActuatorData(std::string name_in) : name(std::move(name_in)) {}

    std::string name;
    double command{std::numeric_limits<double>::quiet_NaN()};
    double state{std::numeric_limits<double>::quiet_NaN()};
    double velocity{std::numeric_limits<double>::quiet_NaN()};
    double effort{std::numeric_limits<double>::quiet_NaN()};
    double transmission_passthrough{std::numeric_limits<double>::quiet_NaN()};
    double transmission_velocity{std::numeric_limits<double>::quiet_NaN()};
    double transmission_effort{std::numeric_limits<double>::quiet_NaN()};
    DevicePointers *device{nullptr};
    bool configured{false};
    bool uses_velocity_command{false};
  };

  JointData &get_joint(const std::string &name) {
    auto it = joint_index_.find(name);
    if (it == joint_index_.end()) {
      joints_.emplace_back(name);
      joint_index_[name] = joints_.size() - 1;
      return joints_.back();
    }

    return joints_[it->second];
  }

  ActuatorData &get_actuator(const std::string &name) {
    auto it = actuator_index_.find(name);
    if (it == actuator_index_.end()) {
      actuators_.emplace_back(name);
      actuator_index_[name] = actuators_.size() - 1;
      return actuators_.back();
    }

    return actuators_[it->second];
  }
  bool configure(const hardware_interface::HardwareInfo &info,
                 rclcpp::Node::SharedPtr node, DeviceAdapter &devices) {
    node_ = std::move(node);
    std::set<std::string> known_joints, mapped_joints, known_actuators,
        mapped_actuators;
    for (const auto &joint : info.joints) {
      if (!known_joints.insert(joint.name).second)
        throw std::invalid_argument("Duplicate joint " + joint.name);
      const auto actuator = joint.parameters.find("actuator");
      known_actuators.insert(
          actuator == joint.parameters.end() ? joint.name : actuator->second);
      for (const auto &key :
           {"origin_offset", "joint_lower_limit", "joint_upper_limit"}) {
        const auto parameter = joint.parameters.find(key);
        if (parameter != joint.parameters.end() &&
            !std::isfinite(std::stod(parameter->second)))
          throw std::invalid_argument("Nonfinite " + std::string(key) +
                                      " for " + joint.name);
      }
    }
    for (const auto &transmission : info.transmissions) {
      for (const auto &joint : transmission.joints) {
        if (!known_joints.count(joint.name) ||
            !mapped_joints.insert(joint.name).second ||
            !std::isfinite(joint.mechanical_reduction) ||
            joint.mechanical_reduction == 0 || !std::isfinite(joint.offset))
          throw std::invalid_argument("Invalid/duplicate transmission joint " +
                                      joint.name);
      }
      for (const auto &actuator : transmission.actuators) {
        if (!known_actuators.count(actuator.name) ||
            !mapped_actuators.insert(actuator.name).second ||
            !std::isfinite(actuator.mechanical_reduction) ||
            actuator.mechanical_reduction == 0)
          throw std::invalid_argument(
              "Invalid/duplicate transmission actuator " + actuator.name);
      }
    }
    if (known_joints != mapped_joints || known_actuators != mapped_actuators)
      throw std::invalid_argument(
          "Every joint and actuator requires a transmission");
    joint_index_.clear();
    actuator_index_.clear();
    joints_.clear();
    actuators_.clear();
    transmissions_.clear();
    device_states_.clear();

    // Transmission handles point into these vectors: prohibit later growth.
    joints_.reserve(info.joints.size());
    actuators_.reserve(info.joints.size());
    transmission_interface::SimpleTransmissionLoader simple_loader;
    transmission_interface::FourBarLinkageTransmissionLoader four_bar_loader;

    for (const auto &joint : info.joints) {
      auto &joint_data = get_joint(joint.name);
      joint_data.has_velocity_state = false;
      joint_data.has_effort_state = false;

      if (joint.command_interfaces.size() != 1 ||
          (joint.command_interfaces[0].name !=
               hardware_interface::HW_IF_POSITION &&
           joint.command_interfaces[0].name !=
               hardware_interface::HW_IF_VELOCITY)) {
        RCLCPP_ERROR(node_->get_logger(),
                     "Joint %s must expose a single position or velocity "
                     "command interface",
                     joint.name.c_str());
        return false;
      }
      joint_data.command_interface = joint.command_interfaces[0].name;

      bool has_position_state = false;
      for (const auto &state_iface : joint.state_interfaces) {
        if (state_iface.name == hardware_interface::HW_IF_POSITION) {
          has_position_state = true;
        } else if (state_iface.name == hardware_interface::HW_IF_VELOCITY) {
          joint_data.has_velocity_state = true;
        } else if (state_iface.name == hardware_interface::HW_IF_EFFORT) {
          joint_data.has_effort_state = true;
        } else {
          RCLCPP_ERROR(node_->get_logger(),
                       "Joint %s exposes unsupported state interface '%s'",
                       joint.name.c_str(), state_iface.name.c_str());
          return false;
        }
      }

      if (!has_position_state) {
        RCLCPP_ERROR(node_->get_logger(),
                     "Joint %s must expose a position state interface",
                     joint.name.c_str());
        return false;
      }

      const auto plugin_it = joint.parameters.find("device_plugin");
      if (plugin_it == joint.parameters.end()) {
        RCLCPP_ERROR(node_->get_logger(),
                     "Joint %s missing <param name=\"device_plugin\">",
                     joint.name.c_str());
        return false;
      }

      const auto actuator_it = joint.parameters.find("actuator");
      joint_data.actuator_name = actuator_it != joint.parameters.end()
                                     ? actuator_it->second
                                     : joint.name;
      const auto lower_limit_it = joint.parameters.find("joint_lower_limit");
      if (lower_limit_it != joint.parameters.end()) {
        joint_data.lower_limit = std::stod(lower_limit_it->second);
      }
      const auto upper_limit_it = joint.parameters.find("joint_upper_limit");
      if (upper_limit_it != joint.parameters.end()) {
        joint_data.upper_limit = std::stod(upper_limit_it->second);
      }
      if (std::isfinite(joint_data.lower_limit) &&
          std::isfinite(joint_data.upper_limit) &&
          joint_data.lower_limit > joint_data.upper_limit) {
        RCLCPP_ERROR(node_->get_logger(),
                     "Joint %s lower limit (%.6f) exceeds upper limit (%.6f)",
                     joint.name.c_str(), joint_data.lower_limit,
                     joint_data.upper_limit);
        return false;
      }

      const auto origin_offset_it = joint.parameters.find("origin_offset");
      if (origin_offset_it != joint.parameters.end()) {
        joint_data.uses_boot_origin = true;
        joint_data.origin_offset = std::stod(origin_offset_it->second);
      }

      auto &actuator = get_actuator(joint_data.actuator_name);
      if (joint_data.command_interface == hardware_interface::HW_IF_VELOCITY) {
        actuator.uses_velocity_command = true;
      }

      if (!actuator.configured) {
        auto &entry = devices.add(joint, plugin_it->second, actuator.name);
        DevicePointers &device_state = device_states_[actuator.name];
        device_state.state_ptr = &entry.feedback.position;
        device_state.velocity_ptr =
            entry.has_velocity ? &entry.feedback.velocity : nullptr;
        device_state.effort_ptr =
            entry.has_effort ? &entry.feedback.effort : nullptr;
        device_state.command_ptr = entry.command;
        actuator.configured = true;
        actuator.device = &device_state;
      }
    }

    for (const auto &transmission_info : info.transmissions) {
      std::shared_ptr<transmission_interface::Transmission> transmission;
      try {
        if (transmission_info.type ==
            "transmission_interface/SimpleTransmission") {
          transmission = simple_loader.load(transmission_info);
        } else if (transmission_info.type ==
                   "transmission_interface/FourBarLinkageTransmission") {
          transmission = four_bar_loader.load(transmission_info);
        } else {
          RCLCPP_ERROR(node_->get_logger(),
                       "Unsupported transmission type '%s'",
                       transmission_info.type.c_str());
          return false;
        }
      } catch (
          const transmission_interface::TransmissionInterfaceException &ex) {
        RCLCPP_ERROR(node_->get_logger(),
                     "Error while loading transmission '%s': %s",
                     transmission_info.name.c_str(), ex.what());
        return false;
      }

      if (!transmission) {
        RCLCPP_ERROR(node_->get_logger(),
                     "Loader returned null transmission for '%s'",
                     transmission_info.name.c_str());
        return false;
      }

      std::vector<transmission_interface::JointHandle> joint_handles;
      joint_handles.reserve(transmission_info.joints.size() * 3);

      for (const auto &joint_info : transmission_info.joints) {
        if (!joint_index_.count(joint_info.name)) {
          throw std::invalid_argument("Transmission references unknown joint " +
                                      joint_info.name);
        }
        auto &joint = get_joint(joint_info.name);

        bool position_added = false;
        bool velocity_added = false;
        bool effort_added = false;

        auto add_joint_handle = [&](const std::string &interface) {
          if (interface == hardware_interface::HW_IF_POSITION &&
              !position_added) {
            joint_handles.emplace_back(joint_info.name,
                                       hardware_interface::HW_IF_POSITION,
                                       &joint.transmission_passthrough);
            position_added = true;
          } else if (interface == hardware_interface::HW_IF_VELOCITY &&
                     !velocity_added) {
            joint_handles.emplace_back(joint_info.name,
                                       hardware_interface::HW_IF_VELOCITY,
                                       &joint.transmission_velocity);
            velocity_added = true;
          } else if (interface == hardware_interface::HW_IF_EFFORT &&
                     !effort_added) {
            joint_handles.emplace_back(joint_info.name,
                                       hardware_interface::HW_IF_EFFORT,
                                       &joint.transmission_effort);
            effort_added = true;
          }
        };

        add_joint_handle(hardware_interface::HW_IF_POSITION);

        if (joint.has_velocity_state) {
          add_joint_handle(hardware_interface::HW_IF_VELOCITY);
        }

        if (joint.has_effort_state) {
          add_joint_handle(hardware_interface::HW_IF_EFFORT);
        }

        for (const auto &cmd_iface : joint_info.command_interfaces) {
          add_joint_handle(cmd_iface);
        }

        for (const auto &state_iface : joint_info.state_interfaces) {
          add_joint_handle(state_iface);
        }
      }

      std::vector<transmission_interface::ActuatorHandle> actuator_handles;
      actuator_handles.reserve(transmission_info.actuators.size() * 3);

      for (const auto &actuator_info : transmission_info.actuators) {
        if (!actuator_index_.count(actuator_info.name)) {
          throw std::invalid_argument(
              "Transmission references unknown actuator " + actuator_info.name);
        }
        auto &actuator = get_actuator(actuator_info.name);

        if (!actuator.configured) {
          RCLCPP_ERROR(node_->get_logger(),
                       "Transmission '%s' references actuator '%s' with no "
                       "configured device",
                       transmission_info.name.c_str(),
                       actuator_info.name.c_str());
          return false;
        }

        bool position_added = false;
        bool velocity_added = false;
        bool effort_added = false;

        auto add_actuator_handle = [&](const std::string &interface) {
          if (interface == hardware_interface::HW_IF_POSITION &&
              !position_added) {
            actuator_handles.emplace_back(actuator_info.name,
                                          hardware_interface::HW_IF_POSITION,
                                          &actuator.transmission_passthrough);
            position_added = true;
          } else if (interface == hardware_interface::HW_IF_VELOCITY &&
                     !velocity_added) {
            actuator_handles.emplace_back(actuator_info.name,
                                          hardware_interface::HW_IF_VELOCITY,
                                          &actuator.transmission_velocity);
            velocity_added = true;
          } else if (interface == hardware_interface::HW_IF_EFFORT &&
                     !effort_added) {
            actuator_handles.emplace_back(actuator_info.name,
                                          hardware_interface::HW_IF_EFFORT,
                                          &actuator.transmission_effort);
            effort_added = true;
          }
        };

        add_actuator_handle(hardware_interface::HW_IF_POSITION);

        if (actuator.device && actuator.device->velocity_ptr) {
          add_actuator_handle(hardware_interface::HW_IF_VELOCITY);
        }

        if (actuator.device && actuator.device->effort_ptr) {
          add_actuator_handle(hardware_interface::HW_IF_EFFORT);
        }

        for (const auto &cmd_iface : actuator_info.command_interfaces) {
          add_actuator_handle(cmd_iface);
        }

        for (const auto &state_iface : actuator_info.state_interfaces) {
          add_actuator_handle(state_iface);
        }
      }

      try {
        transmission->configure(joint_handles, actuator_handles);
      } catch (
          const transmission_interface::TransmissionInterfaceException &ex) {
        RCLCPP_ERROR(node_->get_logger(),
                     "Error while configuring transmission '%s': %s",
                     transmission_info.name.c_str(), ex.what());
        return false;
      }

      transmissions_.push_back(std::move(transmission));
    }
    if (transmissions_.empty()) {
      RCLCPP_ERROR(node_->get_logger(),
                   "No transmissions defined for CAN hardware interface");
      return false;
    }

    RCLCPP_INFO(
        node_->get_logger(),
        "Joint/motor converter configured: %zu joints, %zu actuators, %zu transmissions",
        joints_.size(), actuators_.size(), transmissions_.size());

    return true;
  }

  void reset() {
    for (auto &joint : joints_) {
      joint.boot_reference_valid = false;
      joint.boot_reference_position = std::numeric_limits<double>::quiet_NaN();

      if (joint.command_interface == hardware_interface::HW_IF_POSITION) {
        joint.command = std::numeric_limits<double>::quiet_NaN();
        joint.transmission_passthrough =
            std::numeric_limits<double>::quiet_NaN();
        joint.transmission_velocity = 0.0;
      } else {
        joint.command = 0.0;
      }
    }
  }
  void motorToJoint() {
    for (auto &actuator : actuators_) {
      const auto *device = actuator.device;
      actuator.state = (device && device->state_ptr)
                           ? *device->state_ptr
                           : std::numeric_limits<double>::quiet_NaN();
      actuator.velocity = (device && device->velocity_ptr)
                              ? *device->velocity_ptr
                              : std::numeric_limits<double>::quiet_NaN();
      actuator.effort = (device && device->effort_ptr)
                            ? *device->effort_ptr
                            : std::numeric_limits<double>::quiet_NaN();

      actuator.transmission_passthrough = actuator.state;
      actuator.transmission_velocity = actuator.velocity;
      actuator.transmission_effort = actuator.effort;
    }

    for (auto &transmission : transmissions_) {
      transmission->actuator_to_joint();
    }

    for (auto &joint : joints_) {
      const double actuator_position = joint.transmission_passthrough;
      if (joint.uses_boot_origin && std::isfinite(actuator_position) &&
          !joint.boot_reference_valid) {
        joint.boot_reference_position = actuator_position;
        joint.boot_reference_valid = true;
      }

      if (joint.uses_boot_origin && joint.boot_reference_valid &&
          std::isfinite(actuator_position)) {
        joint.state = actuator_position - joint.boot_reference_position +
                      joint.origin_offset;
      } else if (!joint.uses_boot_origin && std::isfinite(actuator_position)) {
        joint.state = actuator_position;
      } else if (!std::isfinite(joint.state)) {
        joint.state = joint.uses_boot_origin ? joint.origin_offset : 0.0;
      }

      if (std::isfinite(joint.transmission_velocity)) {
        joint.velocity = joint.transmission_velocity;
      } else if (!std::isfinite(joint.velocity)) {
        joint.velocity = 0.0;
      }

      if (std::isfinite(joint.transmission_effort)) {
        joint.effort = joint.transmission_effort;
      } else if (!std::isfinite(joint.effort)) {
        joint.effort = 0.0;
      }
    }
  }

  void jointToMotor() {
    for (auto &joint : joints_) {
      if (joint.command_interface == hardware_interface::HW_IF_POSITION) {
        double limited_command = joint.command;
        if (std::isfinite(limited_command)) {
          if (std::isfinite(joint.lower_limit)) {
            limited_command = std::max(limited_command, joint.lower_limit);
          }
          if (std::isfinite(joint.upper_limit)) {
            limited_command = std::min(limited_command, joint.upper_limit);
          }
          joint.command = limited_command;
        }
        if (!std::isfinite(joint.command)) {
          joint.transmission_passthrough =
              std::numeric_limits<double>::quiet_NaN();
        } else if (joint.uses_boot_origin) {
          if (joint.boot_reference_valid) {
            joint.transmission_passthrough = joint.command -
                                             joint.origin_offset +
                                             joint.boot_reference_position;
          } else {
            joint.transmission_passthrough =
                std::numeric_limits<double>::quiet_NaN();
          }
        } else {
          joint.transmission_passthrough = joint.command;
        }
        joint.transmission_velocity = 0.0;
      } else {
        if (joint.uses_boot_origin && joint.boot_reference_valid &&
            std::isfinite(joint.state)) {
          joint.transmission_passthrough =
              joint.state - joint.origin_offset + joint.boot_reference_position;
        } else {
          joint.transmission_passthrough = joint.state;
        }
        joint.transmission_velocity = joint.command;
      }
      joint.transmission_effort = 0.0;
    }

    for (auto &transmission : transmissions_) {
      transmission->joint_to_actuator();
    }

    for (auto &actuator : actuators_) {
      if (actuator.uses_velocity_command) {
        // Velocity-mode actuators should never be driven by position fallback.
        // If no finite velocity command is available yet, hold them at zero.
        if (std::isfinite(actuator.transmission_velocity)) {
          actuator.command = actuator.transmission_velocity;
        } else {
          actuator.command = 0.0;
        }
      } else {
        actuator.command = actuator.transmission_passthrough;
      }
    }
  }
  void applyCommands() {
    for (auto &actuator : actuators_) {
      *actuator.device->command_ptr = actuator.command;
    }
  }
  auto &joints() { return joints_; }
  const auto &joints() const { return joints_; }
  const auto &actuators() const { return actuators_; }
  size_t index(const std::string &name) const { return joint_index_.at(name); }

private:
  std::unordered_map<std::string, size_t> joint_index_;
  std::unordered_map<std::string, size_t> actuator_index_;
  std::vector<JointData> joints_;
  std::vector<ActuatorData> actuators_;
  std::vector<std::shared_ptr<transmission_interface::Transmission>>
      transmissions_;
  std::unordered_map<std::string, DevicePointers> device_states_;

  rclcpp::Node::SharedPtr node_;
};

} // namespace mr2_control
