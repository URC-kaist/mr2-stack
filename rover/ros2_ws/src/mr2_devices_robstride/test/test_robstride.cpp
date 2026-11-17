#include "../src/robstride_device.cpp"
#include <gtest/gtest.h>
#include <atomic>
#include <map>
#include <thread>
#include <vector>

namespace mr2_devices_robstride {
struct DeviceTestPeer {
  static void parameters(RobstrideDevice &d,
                         const hardware_interface::ComponentInfo &info) {
    d.configure_parameters(info);
  }
  static void prepare(RobstrideDevice &d) {
    d.motor_id_ = 7;
    d.activation_timeout_ms_ = 5;
    d.bus_ = std::make_shared<CanBusManager>();
  }
  static void receive(RobstrideDevice &d, const can_frame &f, int64_t ns) {
    d.receive_at(f, ns);
  }
  static void tick(RobstrideDevice &d, int64_t ns) { d.process_at(ns); }
};
} // namespace mr2_devices_robstride
using Device = mr2_devices_robstride::RobstrideDevice;
using Peer = mr2_devices_robstride::DeviceTestPeer;
namespace p = mr2_devices_robstride::protocol;

// In-memory motor endpoint. It uses literal wire IDs/indices and independently
// encodes big-endian telemetry so tests also check byte order and addressing.
struct Motor {
  Device &device;
  std::vector<can_frame> sent;
  int64_t ns{1000000000};
  uint8_t mode{0}, run_mode{0}, read_mode{2};
  bool reply{true}, enable_works{true};
  uint16_t drop_index{0};
  uint8_t fault{0};
  float target{0.0};
  explicit Motor(Device &d) : device(d) {
    Peer::prepare(d);
    device.set_tx_override([this](const can_frame &f) { command(f); });
  }
  void status(double position = 0, double velocity = 0, double torque = 0) {
    can_frame f{};
    f.can_id = CAN_EFF_FLAG | 0x020007fdU | (uint32_t(mode) << 22) |
               (uint32_t(fault) << 16);
    f.can_dlc = 8;
    const double values[] = {position, velocity, torque};
    const double maxima[] = {12.57, 50, 36};
    for (int i = 0; i < 3; ++i) {
      const auto raw = uint16_t(std::lround((values[i] + maxima[i]) * 65535 / (2 * maxima[i])));
      f.data[i * 2] = raw >> 8;
      f.data[i * 2 + 1] = raw & 255;
    }
    f.data[6] = 1;
    f.data[7] = 144; // 40 degrees C
    Peer::receive(device, f, ns += 1000000);
  }
  void command(const can_frame &f) {
    sent.push_back(f);
    EXPECT_EQ(f.can_dlc, 8);
    EXPECT_EQ(f.can_id & 0xffffU, 0xfd07U);
    EXPECT_NE(f.can_id & CAN_EFF_FLAG, 0U);
    const auto type = (f.can_id >> 24) & 31;
    const auto index = uint16_t(f.data[0] | (uint16_t(f.data[1]) << 8));
    if (!reply || (type == 18 && index == drop_index)) return;
    if (type == 4) mode = 0;
    if (type == 3 && enable_works) mode = 2;
    if (type == 18 && index == 0x7005) run_mode = f.data[4];
    if (type == 18 && index == 0x700a) {
      uint32_t bits = uint32_t(f.data[4]) | (uint32_t(f.data[5]) << 8) |
                      (uint32_t(f.data[6]) << 16) | (uint32_t(f.data[7]) << 24);
      std::memcpy(&target, &bits, 4);
    }
    if (type == 17) {
      can_frame response{};
      response.can_id = CAN_EFF_FLAG | 0x110007fdU;
      response.can_dlc = 8;
      response.data[0] = f.data[0];
      response.data[1] = f.data[1];
      response.data[4] = read_mode;
      Peer::receive(device, response, ns += 1000000);
    } else {
      status();
    }
  }
  void start() {
    ASSERT_TRUE(device.on_activate());
    Peer::tick(device, ns + 1000000); // zero and arm watchdog
    Peer::tick(device, ns + 1000000); // accept fresh feedback
    sent.clear();
  }
};

TEST(Robstride, LiteralVelocityAndParameterWireBytes) {
  const auto frame = p::write_float(253, 7, p::kSpeed, -1.0f);
  EXPECT_EQ(frame.can_id, CAN_EFF_FLAG | 0x1200fd07U);
  const uint8_t expected[] = {0x0a, 0x70, 0, 0, 0, 0, 0x80, 0xbf};
  EXPECT_TRUE(std::equal(std::begin(expected), std::end(expected), frame.data));
  EXPECT_EQ(frame.can_dlc, 8);
  const auto mode = p::parameter(p::kWrite, 253, 7, p::kRunMode, 2);
  EXPECT_EQ(mode.data[0], 5);
  EXPECT_EQ(mode.data[1], 0x70);
  EXPECT_EQ(mode.data[4], 2);
  EXPECT_EQ(p::command(p::kEnable, 253, 7).can_id, CAN_EFF_FLAG | 0x0300fd07U);
  EXPECT_EQ(p::command(p::kStop, 253, 7).can_id, CAN_EFF_FLAG | 0x0400fd07U);
}

TEST(Robstride, FeedbackEndpointsAndTemperature) {
  can_frame frame{};
  std::fill(frame.data, frame.data + 6, 0xff);
  frame.data[6] = 1;
  frame.data[7] = 144;
  frame.can_id = CAN_EFF_FLAG | 0x028107fdU;
  auto result = p::decode(frame, {});
  EXPECT_DOUBLE_EQ(result.position, 12.57);
  EXPECT_DOUBLE_EQ(result.velocity, 50);
  EXPECT_DOUBLE_EQ(result.torque, 36);
  EXPECT_DOUBLE_EQ(result.temperature, 40);
  EXPECT_EQ(result.faults, 1);
  EXPECT_EQ(result.mode, 2);
  std::fill(frame.data, frame.data + 6, 0);
  result = p::decode(frame, {6, 30, 18});
  EXPECT_DOUBLE_EQ(result.position, -6);
  EXPECT_DOUBLE_EQ(result.velocity, -30);
  EXPECT_DOUBLE_EQ(result.torque, -18);
}

TEST(Robstride, ActivationOrderSettingsAndZeroUntilFreshFeedback) {
  Device device;
  Motor motor(device);
  ASSERT_TRUE(device.on_activate());
  EXPECT_EQ(motor.sent.front().can_id, CAN_EFF_FLAG | 0x0400fd07U);
  EXPECT_EQ(motor.sent.back().can_id, CAN_EFF_FLAG | 0x0300fd07U);
  EXPECT_EQ(motor.run_mode, 2);
  std::map<uint16_t, uint32_t> parameters;
  for (const auto &f : motor.sent)
    if (((f.can_id >> 24) & 31) == 18)
      parameters[p::u16_le(f.data)] = p::u32_le(f.data + 4);
  EXPECT_EQ(parameters.at(0x7018), 0x4164cccdU); // 14.3 A
  EXPECT_EQ(parameters.at(0x7022), 0x41a00000U); // 20 rad/s^2
  EXPECT_EQ(parameters.at(0x7028), 0U); // idle zero during other motors' activation
  double *command;
  device.export_command(command);
  *command = 3;
  motor.ns += 2000000000; // optional gripper can delay the owner loop
  Peer::tick(device, motor.ns);
  EXPECT_FLOAT_EQ(motor.target, 0);
  EXPECT_EQ(p::u16_le(motor.sent[motor.sent.size() - 2].data), 0x7028);
  EXPECT_EQ(p::u32_le(motor.sent[motor.sent.size() - 2].data + 4), 4000U);
  Peer::tick(device, motor.ns + 1000000);
  EXPECT_FLOAT_EQ(motor.target, 3);
}

TEST(Robstride, VelocityClampingInvalidCommandAndDeactivation) {
  Device device;
  Motor motor(device);
  motor.start();
  double *command;
  device.export_command(command);
  for (const auto value : {100.0, -100.0, std::numeric_limits<double>::quiet_NaN(),
                           std::numeric_limits<double>::infinity()}) {
    *command = value;
    Peer::tick(device, motor.ns + 1000000);
    EXPECT_FLOAT_EQ(motor.target, std::isfinite(value) ? std::clamp(value, -20.0, 20.0) : 0);
  }
  device.on_deactivate();
  EXPECT_FLOAT_EQ(motor.target, 0);
  EXPECT_EQ(motor.sent.back().can_id, CAN_EFF_FLAG | 0x0400fd07U);
  motor.sent.clear();
  Peer::tick(device, motor.ns + 1000000);
  EXPECT_TRUE(motor.sent.empty());
}

TEST(Robstride, ActivationRejectsMissingWrongModeFaultAndMissingSettingAck) {
  for (int scenario = 0; scenario < 5; ++scenario) {
    Device device;
    Motor motor(device);
    if (scenario == 0) motor.reply = false;
    if (scenario == 1) motor.read_mode = 1;
    if (scenario == 2) motor.fault = 4;
    if (scenario == 3) motor.enable_works = false;
    if (scenario == 4) motor.drop_index = 0x7018;
    EXPECT_FALSE(device.on_activate()) << scenario;
    ASSERT_GE(motor.sent.size(), 3U);
    EXPECT_EQ(motor.sent.back().can_id, CAN_EFF_FLAG | 0x0400fd07U);
  }
}

TEST(Robstride, TimeoutLatchesStopAndNeverAutoRecovers) {
  Device device;
  Motor motor(device);
  motor.start();
  motor.reply = false;
  Peer::tick(device, motor.ns + 201000000);
  ASSERT_EQ(motor.sent.size(), 2U);
  EXPECT_EQ(motor.sent.back().can_id, CAN_EFF_FLAG | 0x0400fd07U);
  CanFeedback feedback;
  device.copy_feedback(feedback);
  EXPECT_TRUE(std::isnan(feedback.velocity));
  motor.reply = true;
  motor.status();
  double *command;
  device.export_command(command);
  *command = 4;
  Peer::tick(device, motor.ns + 300000000);
  EXPECT_FLOAT_EQ(motor.target, 0);
  motor.start(); // explicit lifecycle restart permits commands again
  *command = 4;
  Peer::tick(device, motor.ns + 1000000);
  EXPECT_FLOAT_EQ(motor.target, 4);
}

TEST(Robstride, StartupPrimingRequiresFreshRunningFeedback) {
  Device device;
  Motor motor(device);
  ASSERT_TRUE(device.on_activate());
  motor.reply = false;
  Peer::tick(device, motor.ns + 1000000);
  Peer::tick(device, motor.ns + 202000000);
  EXPECT_EQ(motor.sent.back().can_id, CAN_EFF_FLAG | 0x0400fd07U);
}

TEST(Robstride, StatusFaultResetAndFaultFrameStop) {
  for (int scenario = 0; scenario < 3; ++scenario) {
    Device device;
    Motor motor(device);
    motor.start();
    if (scenario == 0) {
      motor.fault = 2;
      motor.status();
    } else if (scenario == 1) {
      motor.mode = 0;
      motor.status();
    } else {
      can_frame fault{};
      fault.can_id = CAN_EFF_FLAG | 0x150007fdU;
      fault.can_dlc = 8;
      fault.data[0] = 1;
      Peer::receive(device, fault, motor.ns + 1000000);
    }
    Peer::tick(device, motor.ns + 2000000);
    EXPECT_EQ(motor.sent.back().can_id, CAN_EFF_FLAG | 0x0400fd07U);
  }
}

TEST(Robstride, FiltersRejectOtherMotorHostStandardRtrErrorAndShortFrames) {
  Device device;
  Peer::prepare(device);
  can_frame good{};
  good.can_id = CAN_EFF_FLAG | 0x028007fdU;
  good.can_dlc = 8;
  for (int scenario = 0; scenario < 7; ++scenario) {
    auto f = good;
    if (scenario == 0) f.can_id ^= 0x100U;
    if (scenario == 1) f.can_id ^= 1U;
    if (scenario == 2) f.can_id &= ~CAN_EFF_FLAG;
    if (scenario == 3) f.can_id |= CAN_RTR_FLAG;
    if (scenario == 4) f.can_id |= CAN_ERR_FLAG;
    if (scenario == 5) f.can_dlc = 7;
    if (scenario == 6) f.can_id ^= 0x01000000U;
    Peer::receive(device, f, 1000000000);
    CanFeedback feedback;
    device.copy_feedback(feedback);
    EXPECT_EQ(feedback.velocity_received_ns, 0) << scenario;
  }
  Peer::receive(device, good, 1000000000);
  CanFeedback feedback;
  device.copy_feedback(feedback);
  EXPECT_EQ(feedback.velocity_received_ns, 1000000000);
}

TEST(Robstride, ContinuousPositionUnwrapsBothDirectionsAndDetectsAmbiguousGap) {
  Device device;
  Motor motor(device);
  motor.start();
  // Traverse the wrap boundary twice, then reverse across it.
  const double positions[] = {6, 12, -12, -6, 0, 6, 12, -12, 12};
  CanFeedback feedback;
  double last = 0;
  for (const auto pos : positions) {
    motor.status(pos);
    device.copy_feedback(feedback);
    if (pos == -12) EXPECT_GT(feedback.position, last);
    last = feedback.position;
  }
  EXPECT_NEAR(feedback.position, 12 + 25.14, 0.002);
  motor.ns += 300000000;
  motor.status(0);
  device.copy_feedback(feedback);
  EXPECT_TRUE(std::isnan(feedback.position));
  Peer::tick(device, motor.ns + 1000000);
  EXPECT_EQ(motor.sent.back().can_id, CAN_EFF_FLAG | 0x0400fd07U);
}

TEST(Robstride, RejectsInvalidConfigurationBeforeOpeningAnyBus) {
  hardware_interface::ComponentInfo valid;
  hardware_interface::InterfaceInfo velocity;
  velocity.name = "velocity";
  valid.command_interfaces.push_back(velocity);
  valid.parameters = {{"can_iface", "unopened"}, {"motor_id", "7"}};
  Device device;
  EXPECT_NO_THROW(Peer::parameters(device, valid));
  for (const auto &entry : std::vector<std::pair<std::string, std::string>>{
           {"motor_id", "0"}, {"motor_id", "128"}, {"motor_id", "7junk"},
           {"host_id", "7"}, {"host_id", "256"}, {"current_limit_a", "58"},
           {"current_limit_a", "nan"}, {"max_velocity_rad_s", "51"},
           {"feedback_position_max_rad", "0"}, {"feedback_velocity_max_rad_s", "inf"},
           {"acceleration_rad_s2", "-1"}, {"feedback_timeout_ms", "300"},
           {"can_timeout_ms", "0"}, {"activation_timeout_ms", "2junk"}}) {
    auto invalid = valid;
    invalid.parameters[entry.first] = entry.second;
    EXPECT_THROW(Peer::parameters(device, invalid), std::exception) << entry.first;
  }
  valid.command_interfaces[0].name = "position";
  EXPECT_THROW(Peer::parameters(device, valid), std::runtime_error);
}

TEST(Robstride, FeedbackSnapshotsRemainCoherentWithConcurrentReceive) {
  Device device;
  Peer::prepare(device);
  std::atomic_bool done{false};
  std::thread io([&] {
    can_frame f{};
    f.can_id = CAN_EFF_FLAG | 0x028007fdU;
    f.can_dlc = 8;
    for (int i = 1; i <= 10000; ++i)
      Peer::receive(device, f, 1000000000LL + i * 1000000LL);
    done = true;
  });
  do {
    CanFeedback feedback;
    device.copy_feedback(feedback);
    EXPECT_EQ(feedback.position_received_ns, feedback.velocity_received_ns);
  } while (!done);
  io.join();
}
