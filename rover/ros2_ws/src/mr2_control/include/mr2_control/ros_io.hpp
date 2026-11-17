#pragma once
#include "mr2_control/control_loop.hpp"
#include <array>
#include <diagnostic_msgs/msg/diagnostic_array.hpp>
#include <geometry_msgs/msg/twist.hpp>
#include <mr2_action_interface/msg/mission_status.hpp>
#include <nav_msgs/msg/odometry.hpp>
#include <rclcpp/rclcpp.hpp>
#include <sensor_msgs/msg/joint_state.hpp>
#include <std_msgs/msg/float64_multi_array.hpp>
#include <std_srvs/srv/set_bool.hpp>
#include <string>
namespace mr2_control {
struct DeviceTelemetry {
  std::string name;
  double age_s{};
  bool received{};
};
struct Telemetry {
  bool ready{false};
  std::vector<DeviceTelemetry> devices;
  sensor_msgs::msg::JointState joints;
  nav_msgs::msg::Odometry odometry;
  CycleTiming timing;
  double max_feedback_age_s{};
  size_t missing_feedback{};
  bool arm_active{}, gripper_active{};
};
struct ManagementEvent {
  bool arm{}, active{};
};
struct IoConfig {
  std::string manual_topic{"/base/cmd_vel"}, autonomous_topic{"/cmd_vel"};
  std::string mission_topic{"/mission_status"},
      odom_topic{"/wheel_encoder/odometry"};
  std::string gripper_topic{"/gripper_controller/commands"};
  double joint_rate{100}, odom_rate{30};
  size_t arm_size{}, gripper_size{};
};
class RosIO {
public:
  RosIO(rclcpp::Node::SharedPtr node, const IoConfig &config,
        const Telemetry &initial)
      : node_(std::move(node)), telemetry(initial), publish_copy_(initial) {
    inputs.update([&](Inputs &in) {
      in.arm.values.resize(config.arm_size);
      in.gripper.values.resize(config.gripper_size);
    });
    manual_ = node_->create_subscription<geometry_msgs::msg::Twist>(
        config.manual_topic, rclcpp::SystemDefaultsQoS(),
        [this](geometry_msgs::msg::Twist::ConstSharedPtr msg) {
          receiveDrive(*msg, false);
        });
    autonomous_ = node_->create_subscription<geometry_msgs::msg::Twist>(
        config.autonomous_topic, rclcpp::SystemDefaultsQoS(),
        [this](geometry_msgs::msg::Twist::ConstSharedPtr msg) {
          receiveDrive(*msg, true);
        });
    mission_ =
        node_->create_subscription<mr2_action_interface::msg::MissionStatus>(
            config.mission_topic, 10,
            [this](
                mr2_action_interface::msg::MissionStatus::ConstSharedPtr msg) {
              inputs.update([&](Inputs &in) { in.mission_state = msg->state; });
            });
    if (config.arm_size) {
      arm_ = positionSubscription("/manipulator_controller/commands", true,
                                  config.arm_size);
      arm_service_ = activationService("~/set_arm_active", true);
    }
    if (config.gripper_size) {
      gripper_ = positionSubscription(config.gripper_topic, false,
                                      config.gripper_size);
      gripper_service_ = activationService("~/set_gripper_active", false);
    }
    joint_pub_ = node_->create_publisher<sensor_msgs::msg::JointState>(
        "/joint_states", rclcpp::SystemDefaultsQoS());
    odom_pub_ =
        node_->create_publisher<nav_msgs::msg::Odometry>(config.odom_topic, 10);
    diagnostics_ =
        node_->create_publisher<diagnostic_msgs::msg::DiagnosticArray>(
            "~/diagnostics", 10);
    joint_timer_ = node_->create_wall_timer(
        std::chrono::duration<double>(1.0 / config.joint_rate), [this] {
          telemetry.copyInto(publish_copy_);
          if (publish_copy_.ready)
            joint_pub_->publish(publish_copy_.joints);
        });
    if (config.odom_rate > 0.0)
      odom_timer_ = node_->create_wall_timer(
          std::chrono::duration<double>(1.0 / config.odom_rate), [this] {
            telemetry.copyInto(publish_copy_);
            if (publish_copy_.ready)
              odom_pub_->publish(publish_copy_.odometry);
          });
    diag_timer_ = node_->create_wall_timer(std::chrono::seconds(1),
                                           [this] { publishDiagnostics(); });
  }
  Latest<Inputs> inputs;
  Latest<Telemetry> telemetry;
  size_t takeEvents(std::array<ManagementEvent, 16> &out) {
    std::lock_guard<std::mutex> lock(events_mutex_);
    const auto count = event_count_;
    std::copy_n(events_.begin(), count, out.begin());
    event_count_ = 0;
    return count;
  }

private:
  using Array = std_msgs::msg::Float64MultiArray;
  using SetBool = std_srvs::srv::SetBool;
  void receiveDrive(const geometry_msgs::msg::Twist &msg, bool autonomous) {
    DriveCommand command;
    command.twist.linear.x = msg.linear.x;
    command.twist.linear.y = msg.linear.y;
    command.twist.angular.z = msg.angular.z;
    command.received = Clock::now();
    command.valid = std::isfinite(msg.linear.x) &&
                    std::isfinite(msg.linear.y) && std::isfinite(msg.angular.z);
    inputs.update([&](Inputs &in) {
      (autonomous ? in.autonomous : in.manual) = command;
    });
    if (!command.valid)
      RCLCPP_WARN(node_->get_logger(),
                  "Drive: nonfinite command; treating as stale");
  }
  rclcpp::Subscription<Array>::SharedPtr
  positionSubscription(const std::string &topic, bool arm, size_t size) {
    return node_->create_subscription<Array>(
        topic, rclcpp::SystemDefaultsQoS(),
        [this, arm, size](Array::ConstSharedPtr msg) {
          const bool valid =
              msg->data.size() == size &&
              std::all_of(msg->data.begin(), msg->data.end(),
                          [](double v) { return std::isfinite(v); });
          const auto now = Clock::now();
          inputs.update([&](Inputs &in) {
            auto &command = arm ? in.arm : in.gripper;
            ++command.sequence;
            command.received = now;
            command.valid = valid;
            if (valid)
              std::copy(msg->data.begin(), msg->data.end(),
                        command.values.begin());
          });
          if (!valid)
            RCLCPP_WARN(node_->get_logger(),
                        "%s: malformed position array; retaining target",
                        arm ? "Arm" : "Gripper");
        });
  }
  rclcpp::Service<SetBool>::SharedPtr activationService(const std::string &name,
                                                        bool arm) {
    return node_->create_service<SetBool>(
        name, [this, arm](SetBool::Request::SharedPtr request,
                          SetBool::Response::SharedPtr response) {
          std::lock_guard<std::mutex> lock(events_mutex_);
          response->success = event_count_ < events_.size();
          if (response->success) {
            events_[event_count_++] = {arm, request->data};
            response->message = "Queued for next control boundary; see "
                                "diagnostics for applied state";
          } else
            response->message = "Management queue full; retry";
        });
  }
  void publishDiagnostics() {
    telemetry.copyInto(publish_copy_);
    if (!publish_copy_.ready)
      return;
    diagnostic_msgs::msg::DiagnosticArray msg;
    msg.header.stamp = node_->now();
    diagnostic_msgs::msg::DiagnosticStatus status;
    status.name = "mr2_control/runtime";
    status.level = publish_copy_.missing_feedback ? 1 : 0;
    status.message =
        publish_copy_.missing_feedback
            ? "Missing device feedback; driver-specific handling applies"
            : "Running";
    auto add = [&](const std::string &key, const std::string &value) {
      diagnostic_msgs::msg::KeyValue item;
      item.key = key;
      item.value = value;
      status.values.push_back(item);
    };
    add("cycles", std::to_string(publish_copy_.timing.cycles));
    add("missed_deadlines",
        std::to_string(publish_copy_.timing.missed_deadlines));
    add("max_work_us", std::to_string(publish_copy_.timing.max_work_us));
    add("max_lateness_us",
        std::to_string(publish_copy_.timing.max_lateness_us));
    add("max_feedback_age_s", std::to_string(publish_copy_.max_feedback_age_s));
    add("missing_feedback", std::to_string(publish_copy_.missing_feedback));
    add("arm_active", publish_copy_.arm_active ? "true" : "false");
    add("gripper_active", publish_copy_.gripper_active ? "true" : "false");
    for (size_t i = 0; i < 6; ++i)
      add("work_bucket_" + std::to_string(i),
          std::to_string(publish_copy_.timing.work_histogram[i]));
    msg.status.push_back(std::move(status));
    for (const auto &device : publish_copy_.devices) {
      diagnostic_msgs::msg::DiagnosticStatus state;
      state.name = "mr2_control/device/" + device.name;
      state.level = (!device.received || device.age_s > .5) ? 1 : 0;
      state.message =
          !device.received
              ? "No feedback; driver-specific startup handling applies"
          : device.age_s > .5
              ? "Feedback older than 0.5s; driver-specific handling applies"
              : "Feedback received";
      diagnostic_msgs::msg::KeyValue age;
      age.key = "feedback_age_s";
      age.value = std::to_string(device.age_s);
      state.values.push_back(age);
      msg.status.push_back(std::move(state));
    }
    diagnostics_->publish(msg);
  }
  rclcpp::Node::SharedPtr node_;
  Telemetry publish_copy_;
  std::mutex events_mutex_;
  std::array<ManagementEvent, 16> events_{};
  size_t event_count_{};
  rclcpp::Subscription<geometry_msgs::msg::Twist>::SharedPtr manual_,
      autonomous_;
  rclcpp::Subscription<mr2_action_interface::msg::MissionStatus>::SharedPtr
      mission_;
  rclcpp::Subscription<Array>::SharedPtr arm_, gripper_;
  rclcpp::Service<SetBool>::SharedPtr arm_service_, gripper_service_;
  rclcpp::Publisher<sensor_msgs::msg::JointState>::SharedPtr joint_pub_;
  rclcpp::Publisher<nav_msgs::msg::Odometry>::SharedPtr odom_pub_;
  rclcpp::Publisher<diagnostic_msgs::msg::DiagnosticArray>::SharedPtr
      diagnostics_;
  rclcpp::TimerBase::SharedPtr joint_timer_, odom_timer_, diag_timer_;
};
} // namespace mr2_control
