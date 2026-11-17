#include "mr2_control/joint_motor_converter.hpp"
#include "../../mr2_devices_ak_servo/src/ak_servo_device.cpp"
#include <gtest/gtest.h>
namespace mr2_devices_ak_servo {
struct DeviceTestPeer {
  static void receiveAt(AkServoDevice &device, int16_t tenths, int64_t ns) {
    can_frame frame{};
    frame.can_id = CAN_EFF_FLAG | 0x2965;
    frame.can_dlc = 8;
    const auto raw = static_cast<uint16_t>(tenths);
    frame.data[0] = raw >> 8;
    frame.data[1] = raw & 0xff;
    device.on_status_at(frame, ns);
  }
};
} // namespace mr2_devices_ak_servo
using namespace mr2_control;
namespace {
hardware_interface::HardwareInfo model(bool coupled = false, bool origin = true,
                                       bool velocity = false) {
  hardware_interface::HardwareInfo info;
  for (int i = 0; i < (coupled ? 2 : 1); ++i) {
    hardware_interface::ComponentInfo joint;
    joint.name = "j" + std::to_string(i);
    joint.type = "joint";
    hardware_interface::InterfaceInfo command;
    command.name = velocity ? "velocity" : "position";
    joint.command_interfaces.push_back(command);
    for (const auto &name : {"position", "velocity", "effort"}) {
      hardware_interface::InterfaceInfo state;
      state.name = name;
      joint.state_interfaces.push_back(state);
    }
    joint.parameters = {{"actuator", "m" + std::to_string(i)},
                        {"device_plugin", "mock"},
                        {"joint_lower_limit", "-1"},
                        {"joint_upper_limit", "1"}};
    if (origin)
      joint.parameters["origin_offset"] = "0.25";
    info.joints.push_back(joint);
  }
  hardware_interface::TransmissionInfo transmission;
  transmission.name = "t";
  transmission.type = coupled
                          ? "transmission_interface/FourBarLinkageTransmission"
                          : "transmission_interface/SimpleTransmission";
  for (size_t i = 0; i < info.joints.size(); ++i) {
    hardware_interface::JointInfo joint;
    joint.name = info.joints[i].name;
    joint.role = joint.name;
    joint.mechanical_reduction = coupled ? (i == 0 ? -1 : 1) : -2;
    joint.command_interfaces = {velocity ? "velocity" : "position"};
    joint.state_interfaces = {"position", "velocity", "effort"};
    transmission.joints.push_back(joint);
    hardware_interface::ActuatorInfo actuator;
    actuator.name = "m" + std::to_string(i);
    actuator.role = actuator.name;
    actuator.mechanical_reduction = coupled ? 50 : 1;
    actuator.command_interfaces = joint.command_interfaces;
    actuator.state_interfaces = joint.state_interfaces;
    transmission.actuators.push_back(actuator);
  }
  info.transmissions.push_back(transmission);
  return info;
}
class ConverterTest : public ::testing::Test {
protected:
  static void SetUpTestSuite() {
    if (!rclcpp::ok())
      rclcpp::init(0, nullptr);
  }
};
TEST_F(ConverterTest, BootReferenceClampAndReset) {
  auto node = std::make_shared<rclcpp::Node>("converter_test");
  DeviceAdapter devices(node, true);
  JointMotorConverter converter;
  const auto info = model();
  ASSERT_TRUE(converter.configure(info, node, devices));
  converter.reset();
  auto &motor = devices.add(info.joints[0], "mock", "m0");
  motor.feedback.position = NAN;
  converter.motorToJoint();
  EXPECT_EQ(converter.joints()[0].state, .25);
  EXPECT_FALSE(converter.joints()[0].boot_reference_valid);
  converter.joints()[0].command = .5;
  converter.jointToMotor();
  EXPECT_TRUE(std::isnan(converter.actuators()[0].command));
  motor.feedback = {4, 6, 2, 100, 100};
  converter.motorToJoint();
  EXPECT_EQ(converter.joints()[0].state, .25);
  EXPECT_EQ(converter.joints()[0].velocity, -3);
  EXPECT_EQ(converter.joints()[0].effort, -4);
  motor.feedback.position = 6;
  converter.motorToJoint();
  EXPECT_EQ(converter.joints()[0].state, -.75);
  converter.joints()[0].command = 3;
  converter.jointToMotor();
  EXPECT_EQ(converter.joints()[0].command, 1);
  EXPECT_EQ(converter.actuators()[0].command, 2.5);
  motor.feedback.position = NAN;
  converter.motorToJoint();
  EXPECT_EQ(converter.joints()[0].state, -.75);
  converter.reset();
  motor.feedback.position = 8;
  converter.motorToJoint();
  EXPECT_EQ(converter.joints()[0].state, .25);
}
TEST_F(ConverterTest, AkBootReferenceUsesOnlyStabilizedFeedback) {
  auto node = std::make_shared<rclcpp::Node>("ak_startup_converter_test");
  DeviceAdapter devices(node, true);
  JointMotorConverter converter;
  const auto info = model();
  ASSERT_TRUE(converter.configure(info, node, devices));
  converter.reset();
  auto &motor = devices.add(info.joints[0], "mock", "m0");
  mr2_devices_ak_servo::AkServoDevice ak;
  for (int i = 0; i <= 10; ++i) {
    // Recorded J4 first-frame jump; the test transmission has reduction -2.
    mr2_devices_ak_servo::DeviceTestPeer::receiveAt(
        ak, i == 0 ? -226 : -520, 1000000000LL + i * 20000000LL);
    ak.copy_feedback(motor.feedback);
    converter.motorToJoint();
    converter.joints()[0].command = converter.joints()[0].state;
    converter.jointToMotor();
    EXPECT_DOUBLE_EQ(converter.joints()[0].state, .25);
    if (i < 10) {
      EXPECT_FALSE(converter.joints()[0].boot_reference_valid);
      EXPECT_TRUE(std::isnan(converter.actuators()[0].command));
    } else {
      EXPECT_TRUE(converter.joints()[0].boot_reference_valid);
      EXPECT_NEAR(converter.joints()[0].boot_reference_position,
                  26 * M_PI / 180, 1e-12);
      EXPECT_NEAR(converter.actuators()[0].command, -52 * M_PI / 180, 1e-12);
    }
  }
}
TEST_F(ConverterTest, FourBarIndependentExpectedValues) {
  auto node = std::make_shared<rclcpp::Node>("coupling_test");
  DeviceAdapter devices(node, true);
  JointMotorConverter converter;
  const auto info = model(true, false);
  ASSERT_TRUE(converter.configure(info, node, devices));
  converter.reset();
  devices.add(info.joints[0], "mock", "m0").feedback = {50, 100, 0, 1, 1};
  devices.add(info.joints[1], "mock", "m1").feedback = {100, 50, 0, 1, 1};
  converter.motorToJoint();
  EXPECT_EQ(converter.joints()[0].state, -1);
  EXPECT_EQ(converter.joints()[1].state, 3);
  EXPECT_EQ(converter.joints()[0].velocity, -2);
  EXPECT_EQ(converter.joints()[1].velocity, 3);
  converter.joints()[0].command = .5;
  converter.joints()[1].command = -.25;
  converter.jointToMotor();
  EXPECT_EQ(converter.actuators()[0].command, -25);
  EXPECT_EQ(converter.actuators()[1].command, 12.5);
}
TEST_F(ConverterTest, VelocitySignAndInvalidFallback) {
  auto node = std::make_shared<rclcpp::Node>("velocity_test");
  DeviceAdapter devices(node, true);
  JointMotorConverter converter;
  auto info = model(false, false, true);
  ASSERT_TRUE(converter.configure(info, node, devices));
  converter.reset();
  converter.motorToJoint();
  converter.joints()[0].command = 2;
  converter.jointToMotor();
  EXPECT_EQ(converter.actuators()[0].command, -4);
  converter.joints()[0].command = NAN;
  converter.jointToMotor();
  EXPECT_EQ(converter.actuators()[0].command, 0);
}
} // namespace
