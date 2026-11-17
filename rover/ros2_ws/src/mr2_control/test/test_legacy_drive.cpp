#include "mr2_control/drive_processor.hpp"
#include "mr2_rover_control/twist_to_commands_controller.hpp"
#include <gtest/gtest.h>
#include <random>
#include <thread>
using namespace mr2_control;
TEST(LegacyDrive, RepresentativeCommandsMatchActualController) {
  if (!rclcpp::ok())
    rclcpp::init(0, nullptr);
  mr2_rover_control::TwistToCommandsController legacy;
  ASSERT_EQ(legacy.init("legacy_comparison"),
            controller_interface::return_type::OK);
  auto node = legacy.get_node();
  node->set_parameter(rclcpp::Parameter(
      "wheel_joints", std::vector<std::string>{"w0", "w1", "w2", "w3"}));
  node->set_parameter(rclcpp::Parameter(
      "steering_joints", std::vector<std::string>{"s0", "s1", "s2", "s3"}));
  node->set_parameter(
      rclcpp::Parameter("cmd_vel_topic_nominal", "/test_control/manual"));
  node->set_parameter(
      rclcpp::Parameter("cmd_vel_topic_running", "/test_control/auto"));
  node->set_parameter(
      rclcpp::Parameter("mission_status_topic", "/test_control/mission"));
  node->set_parameter(
      rclcpp::Parameter("wheel_odom_topic", "/test_control/odom"));
  ASSERT_EQ(legacy.on_configure(rclcpp_lifecycle::State()),
            controller_interface::CallbackReturn::SUCCESS);
  std::array<double, 8> states{}, commands{};
  std::vector<hardware_interface::CommandInterface> command_handles;
  std::vector<hardware_interface::StateInterface> state_handles;
  command_handles.reserve(8);
  state_handles.reserve(8);
  for (size_t i = 0; i < 8; ++i) {
    command_handles.emplace_back("j" + std::to_string(i),
                                 i < 4 ? "velocity" : "position", &commands[i]);
    state_handles.emplace_back("j" + std::to_string(i),
                               i < 4 ? "velocity" : "position", &states[i]);
  }
  std::vector<hardware_interface::LoanedCommandInterface> loaned_commands;
  std::vector<hardware_interface::LoanedStateInterface> loaned_states;
  for (size_t i = 0; i < 8; ++i) {
    loaned_commands.emplace_back(command_handles[i]);
    loaned_states.emplace_back(state_handles[i]);
  }
  legacy.assign_interfaces(std::move(loaned_commands),
                           std::move(loaned_states));
  legacy.on_activate(rclcpp_lifecycle::State());
  auto publisher_node = std::make_shared<rclcpp::Node>("comparison_inputs");
  auto pub = publisher_node->create_publisher<geometry_msgs::msg::Twist>(
      "/test_control/manual", rclcpp::SystemDefaultsQoS());
  rclcpp::executors::SingleThreadedExecutor executor;
  executor.add_node(node->get_node_base_interface());
  for (int i = 0; i < 100 && pub->get_subscription_count() == 0; ++i)
    std::this_thread::sleep_for(std::chrono::milliseconds(10));
  ASSERT_GT(pub->get_subscription_count(), 0u);
  DriveProcessor drive;
  std::mt19937 random(71);
  std::uniform_real_distribution<double> velocity(-1, 1), angle(-2.35, 2.35);
  for (int sample = 0; sample < 100; ++sample) {
    geometry_msgs::msg::Twist msg;
    msg.linear.x = velocity(random);
    msg.linear.y = velocity(random);
    msg.angular.z = velocity(random);
    for (size_t i = 0; i < 4; ++i) {
      states[i] = velocity(random);
      states[i + 4] = angle(random);
    }
    if (sample == 0) {
      msg = geometry_msgs::msg::Twist();
      states.fill(0);
    }
    if (sample == 1) {
      msg.linear.x = -1;
      msg.linear.y = msg.angular.z = 0;
      states.fill(0);
    }
    pub->publish(msg);
    // Wait for DDS delivery, then execute the actual legacy subscription.
    for (int i = 0; i < 3; ++i) {
      std::this_thread::sleep_for(std::chrono::milliseconds(3));
      executor.spin_some();
    }
    const double dt = .01 + (sample % 7) * .003;
    legacy.update(node->now(), rclcpp::Duration::from_seconds(dt));
    DriveCommand command;
    command.valid = true;
    command.received = Clock::now();
    command.twist.linear.x = msg.linear.x;
    command.twist.linear.y = msg.linear.y;
    command.twist.angular.z = msg.angular.z;
    const auto result = drive.update(command, states, command.received, dt);
    for (size_t i = 0; i < 8; ++i)
      EXPECT_NEAR(result[i], commands[i], 1e-12)
          << "sample=" << sample << " joint=" << i;
  }
  std::this_thread::sleep_for(std::chrono::milliseconds(550));
  legacy.update(node->now(), rclcpp::Duration::from_seconds(.01));
  for (double command : commands)
    EXPECT_EQ(command, 0);
  legacy.release_interfaces();
}

#include <pluginlib/class_loader.hpp>
#include <sensor_msgs/msg/joint_state.hpp>
TEST(LegacyFeedback, BroadcasterMissingFieldsAndFrameContract) {
  if (!rclcpp::ok())
    rclcpp::init(0, nullptr);
  pluginlib::ClassLoader<controller_interface::ControllerInterface> loader(
      "controller_interface", "controller_interface::ControllerInterface");
  auto broadcaster = loader.createSharedInstance(
      "joint_state_broadcaster/JointStateBroadcaster");
  ASSERT_EQ(broadcaster->init("feedback_contract"),
            controller_interface::return_type::OK);
  auto node = broadcaster->get_node();
  ASSERT_EQ(broadcaster->on_configure(rclcpp_lifecycle::State()),
            controller_interface::CallbackReturn::SUCCESS);
  double position = .7;
  hardware_interface::StateInterface state("position_only", "position",
                                           &position);
  std::vector<hardware_interface::LoanedStateInterface> handles;
  handles.emplace_back(state);
  broadcaster->assign_interfaces({}, std::move(handles));
  ASSERT_EQ(broadcaster->on_activate(rclcpp_lifecycle::State()),
            controller_interface::CallbackReturn::SUCCESS);
  auto probe = std::make_shared<rclcpp::Node>("feedback_probe");
  sensor_msgs::msg::JointState::SharedPtr received;
  auto subscription = probe->create_subscription<sensor_msgs::msg::JointState>(
      "/joint_states", 10,
      [&](sensor_msgs::msg::JointState::SharedPtr msg) { received = msg; });
  for (int i = 0; i < 100 && !received; ++i) {
    broadcaster->update(node->now(), rclcpp::Duration::from_seconds(.01));
    rclcpp::spin_some(probe);
    std::this_thread::sleep_for(std::chrono::milliseconds(10));
  }
  ASSERT_TRUE(received);
  EXPECT_EQ(received->header.frame_id, "base_link");
  ASSERT_EQ(received->velocity.size(), 1u);
  ASSERT_EQ(received->effort.size(), 1u);
  EXPECT_TRUE(std::isnan(received->velocity[0]));
  EXPECT_TRUE(std::isnan(received->effort[0]));
  EXPECT_EQ(received->position[0], .7);
  broadcaster->on_deactivate(rclcpp_lifecycle::State());
  broadcaster->release_interfaces();
}
