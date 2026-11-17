#include "mr2_control/drive_processor.hpp"
#include "mr2_control/drive_selector.hpp"
#include "mr2_control/joint_motor_converter.hpp"
#include "mr2_control/position_processor.hpp"
#include "mr2_control/ros_io.hpp"
#include <set>
#include <yaml-cpp/yaml.h>

namespace mr2_control {
template <class T>
T value(const YAML::Node &node, const char *name, T fallback) {
  return node && node[name] ? node[name].as<T>() : fallback;
}
YAML::Node parameters(const YAML::Node &config, const std::string &name) {
  if (config[name] && config[name]["ros__parameters"])
    return config[name]["ros__parameters"];
  return YAML::Node(YAML::NodeType::Map);
}
class Runtime {
public:
  explicit Runtime(rclcpp::Node::SharedPtr node)
      : node_(std::move(node)),
        devices_(node_, node_->declare_parameter("mock_devices", false)) {
    const auto description =
        node_->declare_parameter<std::string>("robot_description", "");
    const auto path =
        node_->declare_parameter<std::string>("controller_config", "");
    if (description.empty() || path.empty())
      throw std::invalid_argument(
          "robot_description and controller_config are required");
    if (node_->get_parameter("use_sim_time").as_bool())
      throw std::invalid_argument("mr2_control uses hardware/steady "
                                  "scheduling; use_sim_time must be false");
    const auto config = YAML::LoadFile(path);
    rate_ = value<double>(config["controller_manager"]["ros__parameters"],
                          "update_rate", 100.0);
    if (!std::isfinite(rate_) || rate_ <= 0 || rate_ > 10000)
      throw std::invalid_argument("Invalid update_rate");
    auto hardware =
        hardware_interface::parse_control_resources_from_urdf(description);
    if (hardware.size() != 1 || hardware.front().hardware_class_type !=
                                    "mr2_can_hardware_interface/CanHW")
      throw std::invalid_argument("Expected one MR2 CAN hardware block");
    if (!converter_.configure(hardware.front(), node_, devices_))
      throw std::runtime_error("Converter configuration failed");
    const auto drive = parameters(config, "rover_controller");
    const bool enable_arm =
        node_->declare_parameter("enable_manipulator_module", true);
    auto names = value<std::vector<std::string>>(drive, "wheel_joints", {});
    drive_enabled_ = !names.empty();
    if (drive_enabled_) {
      auto steering =
          value<std::vector<std::string>>(drive, "steering_joints", {});
      if (names.size() != 4 || steering.size() != 4)
        throw std::invalid_argument(
            "Drive requires four wheels and four steering joints");
      for (size_t i = 0; i < 4; ++i) {
        drive_indices_[i] = claim(names[i], "velocity");
        drive_indices_[i + 4] = claim(steering[i], "position");
      }
    }
    auto arm_names =
        enable_arm
            ? value<std::vector<std::string>>(
                  parameters(config, "manipulator_controller"), "joints", {})
            : std::vector<std::string>{};
    const bool standalone_gripper =
        static_cast<bool>(config["gripper_position_controller"]);
    const auto gripper_key = standalone_gripper ? "gripper_position_controller"
                                                : "gripper_controller";
    auto gripper_names =
        enable_arm ? value<std::vector<std::string>>(
                         parameters(config, gripper_key), "joints", {})
                   : std::vector<std::string>{};
    std::vector<size_t> arm_indices, gripper_indices;
    for (const auto &name : arm_names)
      arm_indices.push_back(claim(name, "position"));
    for (const auto &name : gripper_names)
      gripper_indices.push_back(claim(name, "position"));
    if (gripper_indices.size() > 1)
      throw std::invalid_argument("Gripper requires one joint");
    arm_ = ArmProcessor(arm_indices);
    gripper_ = GripperProcessor(gripper_indices);
    for (const auto &joint : converter_.joints()) {
      if (!claimed_.count(joint.name))
        RCLCPP_INFO(node_->get_logger(),
                    "Joint %s has no active processor; retaining startup "
                    "target (position hold / zero velocity)",
                    joint.name.c_str());
    }
    DriveConfig dc;
    dc.wheel_base = value(drive, "wheel_base", dc.wheel_base);
    dc.track_width = value(drive, "track_width", dc.track_width);
    dc.wheel_radius = value(drive, "wheel_radius", dc.wheel_radius);
    dc.max_wheel_linear_speed = std::max(
        0.0, value(drive, "max_wheel_linear_speed", dc.max_wheel_linear_speed));
    dc.max_steer = value(drive, "max_steer", dc.max_steer);
    dc.timeout = value(drive, "twist_timeout", dc.timeout);
    dc.rate_limit_vx = value(drive, "rate_limit_linear_x", dc.rate_limit_vx);
    dc.rate_limit_vy = value(drive, "rate_limit_linear_y", dc.rate_limit_vy);
    dc.rate_limit_wz = value(drive, "rate_limit_angular_z", dc.rate_limit_wz);
    const double error_deg = value(drive, "steering_error_ratio_deg", 40.0);
    dc.steering_error_ratio_rad =
        error_deg > 0 ? error_deg * M_PI / 180.0 : dc.max_steer;
    for (const auto &pair : {std::make_pair("odom_wheel_drive_enabled",
                                            &dc.odom_wheel_drive_enabled),
                             std::make_pair("odom_wheel_steer_enabled",
                                            &dc.odom_wheel_steer_enabled)}) {
      const auto mask = value<std::vector<bool>>(drive, pair.first, {});
      if (mask.size() == 4)
        std::copy(mask.begin(), mask.end(), pair.second->begin());
      else if (!mask.empty())
        RCLCPP_WARN(node_->get_logger(),
                    "%s: expected 4 entries; using all true", pair.first);
    }
    for (double number :
         {dc.wheel_base, dc.track_width, dc.wheel_radius,
          dc.max_wheel_linear_speed, dc.max_steer, dc.timeout, dc.rate_limit_vx,
          dc.rate_limit_vy, dc.rate_limit_wz, dc.steering_error_ratio_rad})
      if (!std::isfinite(number))
        throw std::invalid_argument("Nonfinite drive configuration");
    if (dc.max_steer < 0)
      throw std::invalid_argument("max_steer must be nonnegative");
    drive_ = DriveProcessor(dc);
    IoConfig io_config;
    io_config.manual_topic =
        value(drive, "cmd_vel_topic_nominal", io_config.manual_topic);
    io_config.autonomous_topic =
        value(drive, "cmd_vel_topic_running", io_config.autonomous_topic);
    io_config.mission_topic =
        value(drive, "mission_status_topic", io_config.mission_topic);
    io_config.odom_topic =
        value(drive, "wheel_odom_topic", io_config.odom_topic);
    io_config.odom_rate =
        drive_enabled_ ? value(drive, "odom_publish_rate_hz", 30.0) : 0.0;
    if (!std::isfinite(io_config.odom_rate))
      throw std::invalid_argument("Invalid odometry rate");
    io_config.joint_rate = rate_;
    io_config.arm_size = arm_names.size();
    io_config.gripper_size = gripper_names.size();
    if (standalone_gripper)
      io_config.gripper_topic = "/gripper_position_controller/commands";
    snapshot_.arm.values.resize(arm_names.size());
    snapshot_.gripper.values.resize(gripper_names.size());
    for (const auto &joint : converter_.joints())
      output_.joints.name.push_back(joint.name);
    output_.joints.header.frame_id = value<std::string>(
        parameters(config, "joint_state_broadcaster"), "frame_id", "base_link");
    output_.joints.position.resize(converter_.joints().size());
    output_.joints.velocity.resize(converter_.joints().size());
    output_.joints.effort.resize(converter_.joints().size());
    output_.odometry.header.frame_id =
        value<std::string>(drive, "odom_frame_id", "odom");
    output_.odometry.child_frame_id =
        value<std::string>(drive, "base_frame_id", "base_link");
    output_.odometry.pose.pose.orientation.w = 1.0;
    for (size_t i = 0; i < 6; ++i) {
      output_.odometry.pose.covariance[i * 7] = 1e6;
      output_.odometry.twist.covariance[i * 7] = .05;
    }
    for (const auto &[name, entry] : devices_.entries())
      output_.devices.push_back({name, 0, false});
    const bool active =
        node_->declare_parameter("start_manipulator_controllers_active", false);
    arm_.setActive(active && !arm_names.empty(), 0);
    gripper_.setActive(node_->declare_parameter("start_gripper_active",
                                                standalone_gripper || active) &&
                           !gripper_names.empty(),
                       0);
    converter_.reset();
    // Device activation may wait for CAN feedback; the I/O threads are running.
    if (!devices_.activate())
      throw std::runtime_error("Device activation failed");
    activated_ = true;
    if (!rclcpp::ok(node_->get_node_base_interface()->get_context()))
      throw std::runtime_error("Shutdown during device activation");
    // Volatile command subscriptions start only once activation succeeds.
    // Otherwise DDS queues startup commands and callbacks give them fresh ages.
    io_ = std::make_unique<RosIO>(node_, io_config, output_);
    watchdog_ =
        node_->create_wall_timer(std::chrono::milliseconds(100), [this] {
          if (loop_.failed()) {
            try {
              std::rethrow_exception(loop_.failure());
            } catch (const std::exception &error) {
              RCLCPP_ERROR(node_->get_logger(),
                           "Control thread failed: %s; shutting down",
                           error.what());
            } catch (...) {
              RCLCPP_ERROR(node_->get_logger(),
                           "Control thread failed with unknown exception; "
                           "shutting down");
            }
            rclcpp::shutdown();
          }
        });
    loop_.start(rate_,
                [this](Clock::time_point now, double dt,
                       const CycleTiming &timing) { step(now, dt, timing); });
  }
  bool failed() const { return loop_.failed(); }
  ~Runtime() {
    loop_.stop();
    if (activated_)
      devices_.deactivate();
    devices_.stop();
  }

private:
  size_t claim(const std::string &name, const std::string &mode) {
    const auto index = converter_.index(name);
    if (converter_.joints()[index].command_interface != mode ||
        !claimed_.insert(name).second)
      throw std::invalid_argument("Duplicate or incompatible target slot " +
                                  name);
    return index;
  }
  void step(Clock::time_point now, double dt, const CycleTiming &timing) {
    io_->inputs.copyInto(snapshot_);
    const auto count = io_->takeEvents(events_);
    for (size_t i = 0; i < count; ++i) {
      const auto &event = events_[i];
      if (event.arm)
        arm_.setActive(event.active, snapshot_.arm.sequence);
      else
        gripper_.setActive(event.active, snapshot_.gripper.sequence);
    }
    devices_.copyFeedback();
    converter_.motorToJoint();
    auto &joints = converter_.joints();
    if (drive_enabled_) {
      std::array<double, 8> state;
      for (size_t i = 0; i < 4; ++i) {
        state[i] = joints[drive_indices_[i]].velocity;
        state[i + 4] = joints[drive_indices_[i + 4]].state;
      }
      const auto targets =
          drive_.update(selector_.select(snapshot_), state, now, dt);
      for (size_t i = 0; i < 8; ++i)
        joints[drive_indices_[i]].command = targets[i];
      const auto odometry =
          drive_.odometry({state[0], state[1], state[2], state[3]},
                          {state[4], state[5], state[6], state[7]});
      output_.odometry.twist.twist.linear.x = odometry.linear.x;
      output_.odometry.twist.twist.linear.y = odometry.linear.y;
      output_.odometry.twist.twist.angular.z = odometry.angular.z;
    }
    arm_.update(snapshot_.arm, joints);
    gripper_.update(snapshot_.gripper, joints);
    converter_.jointToMotor();
    converter_.applyCommands();
    const auto stamp = node_->now();
    devices_.process(stamp);
    for (size_t i = 0; i < joints.size(); ++i) {
      output_.joints.position[i] = joints[i].state;
      output_.joints.velocity[i] =
          joints[i].has_velocity_state
              ? joints[i].velocity
              : std::numeric_limits<double>::quiet_NaN();
      output_.joints.effort[i] = joints[i].has_effort_state
                                     ? joints[i].effort
                                     : std::numeric_limits<double>::quiet_NaN();
    }
    output_.joints.header.stamp = stamp;
    output_.odometry.header.stamp = stamp;
    output_.timing = timing;
    output_.arm_active = arm_.active();
    output_.gripper_active = gripper_.active();
    output_.missing_feedback = 0;
    output_.max_feedback_age_s = 0;
    const auto ns = std::chrono::duration_cast<std::chrono::nanoseconds>(
                        now.time_since_epoch())
                        .count();
    size_t device_index = 0;
    for (const auto &[name, entry] : devices_.entries()) {
      const auto received = entry.velocity_mode
                                ? entry.feedback.velocity_received_ns
                                : entry.feedback.position_received_ns;
      auto &device_telemetry = output_.devices[device_index++];
      device_telemetry.received = received != 0;
      device_telemetry.age_s =
          received ? std::max(0.0, (ns - received) * 1e-9) : 0.0;
      if (!received)
        ++output_.missing_feedback;
      else
        output_.max_feedback_age_s =
            std::max(output_.max_feedback_age_s, (ns - received) * 1e-9);
    }
    output_.ready = true;
    io_->telemetry.update([this](Telemetry &out) { out = output_; });
  }
  rclcpp::Node::SharedPtr node_;
  DeviceAdapter devices_;
  JointMotorConverter converter_;
  DriveSelector selector_;
  DriveProcessor drive_;
  ArmProcessor arm_;
  GripperProcessor gripper_;
  std::set<std::string> claimed_;
  std::array<size_t, 8> drive_indices_{};
  std::array<ManagementEvent, 16> events_{};
  Inputs snapshot_;
  Telemetry output_;
  std::unique_ptr<RosIO> io_;
  ControlLoop loop_;
  rclcpp::TimerBase::SharedPtr watchdog_;
  double rate_{};
  bool drive_enabled_{}, activated_{};
};
} // namespace mr2_control
int main(int argc, char **argv) {
  rclcpp::init(argc, argv);
  auto node = std::make_shared<rclcpp::Node>("mr2_control");
  int result = 0;
  try {
    mr2_control::Runtime runtime(node);
    rclcpp::executors::SingleThreadedExecutor executor;
    executor.add_node(node);
    executor.spin();
    if (runtime.failed())
      result = 1;
  } catch (const std::exception &error) {
    RCLCPP_ERROR(node->get_logger(), "MR2 control startup/runtime failure: %s",
                 error.what());
    result = 1;
  }
  rclcpp::shutdown();
  return result;
}
