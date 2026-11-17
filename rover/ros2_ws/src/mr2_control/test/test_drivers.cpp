#include "../../mr2_devices_ak_servo/src/ak_servo_device.cpp"
#include "../../mr2_devices_output_actuator/src/custom_actuator_device.cpp"
#include <atomic>
#include <gtest/gtest.h>
#include <thread>

// Per-device capture leaves CanBusManager completely unchanged.
struct CanBusTestPeer {
  inline static std::map<CanBusManager *, std::vector<can_frame>> captured;
  static void capture(CanDevice &device,
                      const std::shared_ptr<CanBusManager> &bus) {
    captured[bus.get()].clear();
    device.set_tx_override([key = bus.get()](const can_frame &frame) {
      captured[key].push_back(frame);
    });
  }
  static std::vector<can_frame> drain(CanBusManager &bus) {
    auto frames = std::move(captured[&bus]);
    captured[&bus].clear();
    return frames;
  }
};
namespace mr2_devices_ak_servo {
struct DeviceTestPeer {
  static auto prepare(AkServoDevice &device) {
    device.id_ = 101;
    device.bus_ = std::make_shared<CanBusManager>();
    CanBusTestPeer::capture(device, device.bus_);
    return device.bus_;
  }
  static void receive(AkServoDevice &device, const can_frame &frame) {
    device.on_status(frame);
  }
  static void receiveAt(AkServoDevice &device, const can_frame &frame, int64_t ns) {
    device.on_status_at(frame, ns);
  }
  static void processAt(AkServoDevice &device, int64_t ns) {
    device.process_at(ns);
  }
  static void stabilize(AkServoDevice &device, const can_frame &frame) {
    const auto end = device.clock_.now().nanoseconds();
    for (int i = 0; i <= 10; ++i)
      receiveAt(device, frame, end - 200000000 + i * 20000000);
  }
  static bool warningDue(AkServoDevice &device, int64_t ns) {
    std::lock_guard<std::mutex> lock(device.feedback_mutex_);
    return device.startup_filter_.warning_due(ns);
  }
  static void configureStartup(AkServoDevice &device,
                               const hardware_interface::ComponentInfo &info) {
    device.configure_startup_filter(info);
  }
};
} // namespace mr2_devices_ak_servo
namespace mr2_devices_output_actuator {
struct DeviceTestPeer {
  static auto prepare(CustomActuatorDevice &device, bool velocity = false) {
    device.node_id_ = 3;
    device.auto_arm_ = false;
    device.channel_ =
        velocity ? CommandChannel::Velocity : CommandChannel::Angle;
    device.bus_ = std::make_shared<CanBusManager>();
    CanBusTestPeer::capture(device, device.bus_);
    return device.bus_;
  }
  static void angle(CustomActuatorDevice &device, int32_t mdeg) {
    can_frame frame{};
    frame.can_dlc = 4;
    encode_i32_le(frame, mdeg);
    device.on_angle_status(frame);
  }
  static void velocity(CustomActuatorDevice &device, int32_t mdeg) {
    can_frame frame{};
    frame.can_dlc = 4;
    encode_i32_le(frame, mdeg);
    device.on_velocity_status(frame);
  }
  static void node(CustomActuatorDevice &device, rclcpp::Node *node) {
    device.node_ = node;
  }
  static void optional(CustomActuatorDevice &device, bool required) {
    device.auto_arm_ = true;
    device.required_at_activation_ = required;
    device.activation_timeout_ms_ = 1;
  }
  static void ready(CustomActuatorDevice &device) {
    device.auto_arm_ = true;
    device.desired_profile_ = Profile::DirectInput;
    device.require_limits_status_ = false;
    device.require_config_status_ = false;
    device.activation_timeout_ms_ = 200;
  }
  static void diag(CustomActuatorDevice &device, bool armed) {
    can_frame frame{};
    frame.can_dlc = 8;
    frame.data[0] = 0xFB;
    frame.data[1] = 3;
    frame.data[2] = 3;
    frame.data[3] = 1;
    frame.data[4] = 0x06;
    frame.data[7] = armed ? 0x02 : 0;
    device.on_runtime_diag(frame);
  }
};
} // namespace mr2_devices_output_actuator
using Ak = mr2_devices_ak_servo::AkServoDevice;
using AkPeer = mr2_devices_ak_servo::DeviceTestPeer;
using Output = mr2_devices_output_actuator::CustomActuatorDevice;
using OutputPeer = mr2_devices_output_actuator::DeviceTestPeer;
TEST(Drivers, AkBytesHoldTimeoutRecoveryAndInvalidFrame) {
  Ak device;
  auto bus = AkPeer::prepare(device);
  double *command;
  device.export_command(command);
  const rclcpp::Time now(0, 0, RCL_STEADY_TIME);
  device.process(now);
  EXPECT_TRUE(CanBusTestPeer::drain(*bus).empty());
  can_frame frame{};
  frame.can_id = CAN_EFF_FLAG | 0x2965;
  frame.can_dlc = 8;
  frame.data[0] = 0x03;
  frame.data[1] = 0x84; // 90 degrees
  AkPeer::stabilize(device, frame);
  device.process(now);
  auto frames = CanBusTestPeer::drain(*bus);
  ASSERT_EQ(frames.size(), 1u);
  EXPECT_EQ(frames[0].can_id, CAN_EFF_FLAG | 0x465u);
  const std::array<uint8_t, 4> expected{0, 0x0d, 0xbb,
                                        0xa0}; // 900000 big endian
  for (size_t i = 0; i < 4; ++i)
    EXPECT_EQ(frames[0].data[i], expected[i]);
  *command = -M_PI / 2;
  device.process(now);
  frames = CanBusTestPeer::drain(*bus);
  const std::array<uint8_t, 4> negative{0xff, 0xf2, 0x44, 0x60};
  for (size_t i = 0; i < 4; ++i)
    EXPECT_EQ(frames[0].data[i], negative[i]);
  frame.can_dlc = 2;
  frame.data[0] = 0;
  AkPeer::receive(device, frame);
  CanFeedback feedback;
  device.copy_feedback(feedback);
  EXPECT_NEAR(feedback.position, M_PI / 2, 1e-12);
  std::this_thread::sleep_for(std::chrono::milliseconds(510));
  device.process(now);
  EXPECT_TRUE(CanBusTestPeer::drain(*bus).empty());
  frame.can_dlc = 8;
  AkPeer::receive(device, frame);
  device.process(now);
  EXPECT_EQ(CanBusTestPeer::drain(*bus).size(), 1u);
}
TEST(Drivers, OutputBytesInactiveInvalidAndShutdown) {
  Output device;
  auto bus = OutputPeer::prepare(device);
  double *command;
  device.export_command(command);
  const rclcpp::Time now(0, 0, RCL_STEADY_TIME);
  OutputPeer::angle(device, 90000);
  device.process(now);
  EXPECT_TRUE(CanBusTestPeer::drain(*bus).empty());
  ASSERT_TRUE(device.on_activate());
  device.process(now);
  auto frames = CanBusTestPeer::drain(*bus);
  ASSERT_EQ(frames.size(), 1u);
  const std::array<uint8_t, 4> expected{0x90, 0x5f, 0x01,
                                        0}; // 90000 little endian
  EXPECT_EQ(frames[0].can_id, 0x203u);
  for (size_t i = 0; i < 4; ++i)
    EXPECT_EQ(frames[0].data[i], expected[i]);
  *command = -M_PI / 2;
  device.process(now);
  frames = CanBusTestPeer::drain(*bus);
  const std::array<uint8_t, 4> negative{0x70, 0xa0, 0xfe, 0xff};
  for (size_t i = 0; i < 4; ++i)
    EXPECT_EQ(frames[0].data[i], negative[i]);
  *command = NAN;
  device.process(now);
  frames = CanBusTestPeer::drain(*bus);
  for (size_t i = 0; i < 4; ++i)
    EXPECT_EQ(frames[0].data[i], negative[i]);
  device.on_deactivate();
  device.process(now);
  EXPECT_TRUE(CanBusTestPeer::drain(*bus).empty());
  Output velocity;
  auto velocity_bus = OutputPeer::prepare(velocity, true);
  velocity.on_activate();
  velocity.export_command(command);
  *command = M_PI / 2;
  velocity.process(now);
  frames = CanBusTestPeer::drain(*velocity_bus);
  EXPECT_EQ(frames[0].can_id, 0x213u);
  for (size_t i = 0; i < 4; ++i)
    EXPECT_EQ(frames[0].data[i], expected[i]);
  *command = NAN;
  velocity.process(now);
  frames = CanBusTestPeer::drain(*velocity_bus);
  for (size_t i = 0; i < 4; ++i)
    EXPECT_EQ(frames[0].data[i], 0);
  velocity.on_deactivate();
  frames = CanBusTestPeer::drain(*velocity_bus);
  ASSERT_EQ(frames.size(), 1u);
  for (size_t i = 0; i < 4; ++i)
    EXPECT_EQ(frames[0].data[i], 0);
}
TEST(Drivers, ActivationFeedbackWaitAndOptionalAbsence) {
  Output optional;
  auto bus = OutputPeer::prepare(optional);
  OutputPeer::optional(optional, false);
  EXPECT_TRUE(optional.on_activate());
  optional.process(rclcpp::Time(0, 0, RCL_STEADY_TIME));
  EXPECT_TRUE(CanBusTestPeer::drain(*bus).empty());
  Output required;
  OutputPeer::prepare(required);
  OutputPeer::optional(required, true);
  EXPECT_FALSE(required.on_activate());
  Output device;
  auto active_bus = OutputPeer::prepare(device);
  OutputPeer::ready(device);
  std::atomic<bool> stop{false};
  std::thread receiver([&] {
    bool armed = false;
    while (!stop) {
      for (const auto &frame : CanBusTestPeer::drain(*active_bus))
        if (frame.can_id == 0x233)
          armed = frame.data[0] != 0;
      OutputPeer::angle(device, 12000);
      OutputPeer::diag(device, armed);
      std::this_thread::sleep_for(std::chrono::milliseconds(1));
    }
  });
  const bool activated = device.on_activate();
  stop = true;
  receiver.join();
  EXPECT_TRUE(activated);
  device.on_deactivate();
}
TEST(Drivers, ConcurrentFeedbackCopyProcessAndLifecycleReaders) {
  Ak ak;
  auto ak_bus = AkPeer::prepare(ak);
  can_frame initial{};
  initial.can_id = CAN_EFF_FLAG | 0x2965;
  initial.can_dlc = 8;
  AkPeer::stabilize(ak, initial);
  Output output;
  auto output_bus = OutputPeer::prepare(output, true);
  output.on_activate();
  std::atomic<bool> done{false};
  std::thread receiver([&] {
    for (int i = 1; i <= 20000; ++i) {
      can_frame frame{};
      frame.can_id = CAN_EFF_FLAG | 0x2965;
      frame.can_dlc = 8;
      frame.data[1] = i % 100;
      frame.data[3] = i % 100;
      frame.data[5] = i % 100;
      AkPeer::receive(ak, frame);
      OutputPeer::velocity(output, i);
      OutputPeer::angle(output, i);
    }
    done = true;
  });
  size_t copies = 0;
  std::vector<double> snapshot_us;
  while (!done) {
    CanFeedback sample, output_sample;
    const auto copy_start = std::chrono::steady_clock::now();
    ak.copy_feedback(sample);
    output.copy_feedback(output_sample);
    snapshot_us.push_back(std::chrono::duration<double, std::micro>(
                              std::chrono::steady_clock::now() - copy_start)
                              .count());
    if (std::isfinite(sample.position)) {
      EXPECT_NEAR(sample.velocity, sample.position * 600, 1e-9);
      EXPECT_NEAR(sample.effort, sample.position * 18 / M_PI, 1e-9);
      EXPECT_EQ(sample.position_received_ns, sample.velocity_received_ns);
    }
    ak.process(rclcpp::Time(0, 0, RCL_STEADY_TIME));
    output.process(rclcpp::Time(0, 0, RCL_STEADY_TIME));
    output.copy_feedback(sample);
    CanBusTestPeer::drain(*ak_bus);
    CanBusTestPeer::drain(*output_bus);
    ++copies;
  }
  receiver.join();
  EXPECT_GT(copies, 0u);
  std::sort(snapshot_us.begin(), snapshot_us.end());
  if (!snapshot_us.empty())
    std::cout << "Concurrent RX: two-driver snapshot latency us: samples="
              << copies
              << " p99=" << snapshot_us[(snapshot_us.size() - 1) * 99 / 100]
              << " max=" << snapshot_us.back() << std::endl;
  output.on_deactivate();
}

namespace {
constexpr int64_t kStart = 1000000000;
constexpr int64_t kMs = 1000000;
can_frame akStatus(int16_t tenths, uint8_t error = 0) {
  can_frame frame{};
  frame.can_id = CAN_EFF_FLAG | 0x2965;
  frame.can_dlc = 8;
  const auto raw = static_cast<uint16_t>(tenths);
  frame.data[0] = raw >> 8;
  frame.data[1] = raw & 0xff;
  frame.data[7] = error;
  return frame;
}
void expectUnavailable(Ak &device) {
  CanFeedback feedback;
  ASSERT_TRUE(device.copy_feedback(feedback));
  EXPECT_TRUE(std::isnan(feedback.position));
  EXPECT_TRUE(std::isnan(feedback.velocity));
  EXPECT_TRUE(std::isnan(feedback.effort));
  EXPECT_EQ(feedback.position_received_ns, 0);
  double *position, *velocity, *effort;
  device.export_state(position, velocity, effort);
  EXPECT_TRUE(std::isnan(*position));
  EXPECT_TRUE(std::isnan(*velocity));
  EXPECT_TRUE(std::isnan(*effort));
}
} // namespace

TEST(Drivers, AkObservedStartupJumpsNeverEscapeOrTransmit) {
  // Motor angles observed in the passive power-cycle experiments, in 0.1 deg.
  for (const auto &angles : {std::pair<int16_t, int16_t>{141, 718},
                             {-226, -520}, {42, 1464}, {-126, 971}}) {
    for (bool has_command : {false, true}) {
      Ak device;
      auto bus = AkPeer::prepare(device);
      double *command;
      device.export_command(command);
      if (has_command)
        *command = -M_PI / 2;
      for (int i = 0; i < 10; ++i) {
        const auto ns = kStart + i * 20 * kMs;
        AkPeer::receiveAt(device, akStatus(i ? angles.second : angles.first), ns);
        expectUnavailable(device);
        AkPeer::processAt(device, ns);
        EXPECT_TRUE(CanBusTestPeer::drain(*bus).empty());
      }
      const auto ready_ns = kStart + 200 * kMs;
      AkPeer::receiveAt(device, akStatus(angles.second), ready_ns);
      CanFeedback feedback;
      device.copy_feedback(feedback);
      EXPECT_NEAR(feedback.position, angles.second * M_PI / 1800, 1e-12);
      EXPECT_EQ(feedback.position_received_ns, ready_ns);
      AkPeer::processAt(device, ready_ns);
      const auto frames = CanBusTestPeer::drain(*bus);
      ASSERT_EQ(frames.size(), 1u);
      const int32_t encoded = static_cast<int32_t>(
          (uint32_t(frames[0].data[0]) << 24) |
          (uint32_t(frames[0].data[1]) << 16) |
          (uint32_t(frames[0].data[2]) << 8) | frames[0].data[3]);
      EXPECT_EQ(encoded, has_command ? -900000 : angles.second * 1000);
      // After startup, normal movement must remain visible immediately.
      AkPeer::receiveAt(device, akStatus(angles.second + 100), ready_ns + 20 * kMs);
      device.copy_feedback(feedback);
      EXPECT_NEAR(feedback.position, (angles.second + 100) * M_PI / 1800, 1e-12);
    }
  }
}

TEST(Drivers, AkFivePacketsRequiredAndControlReadsDoNotCount) {
  Ak device;
  auto bus = AkPeer::prepare(device);
  // Four packets span more than the minimum duration, but are still only four.
  for (int i = 0; i < 4; ++i)
    AkPeer::receiveAt(device, akStatus(900), kStart + i * 70 * kMs);
  for (int i = 0; i < 20; ++i) {
    expectUnavailable(device);
    AkPeer::processAt(device, kStart + 220 * kMs);
  }
  EXPECT_TRUE(CanBusTestPeer::drain(*bus).empty());
  auto invalid = akStatus(900);
  invalid.can_dlc = 2;
  AkPeer::receiveAt(device, invalid, kStart + 230 * kMs);
  expectUnavailable(device);
  AkPeer::receiveAt(device, akStatus(900), kStart + 240 * kMs);
  CanFeedback feedback;
  device.copy_feedback(feedback);
  EXPECT_NEAR(feedback.position, M_PI / 2, 1e-12);
}

TEST(Drivers, AkStartupToleranceUsesWholeWindowAndErrorResetsIt) {
  Ak device;
  auto bus = AkPeer::prepare(device);
  for (int i = 0; i <= 20; ++i) {
    // Each step is only 0.1 degree, but the whole five-sample window drifts.
    AkPeer::receiveAt(device, akStatus(900 + i), kStart + i * 20 * kMs);
    expectUnavailable(device);
    AkPeer::processAt(device, kStart + i * 20 * kMs);
  }
  EXPECT_TRUE(CanBusTestPeer::drain(*bus).empty());
  AkPeer::receiveAt(device, akStatus(920, 5), kStart + 420 * kMs);
  for (int i = 0; i < 4; ++i) {
    AkPeer::receiveAt(device, akStatus(920 + i % 2), kStart + (440 + i * 20) * kMs);
    expectUnavailable(device);
  }
  AkPeer::receiveAt(device, akStatus(920), kStart + 520 * kMs);
  CanFeedback feedback;
  device.copy_feedback(feedback);
  EXPECT_NEAR(feedback.position, 92 * M_PI / 180, 1e-12);
}

TEST(Drivers, AkStartupGapRestartsWaitAndWindow) {
  Ak device;
  auto bus = AkPeer::prepare(device);
  for (int i = 0; i < 10; ++i)
    AkPeer::receiveAt(device, akStatus(900), kStart + i * 20 * kMs);
  // Missing feedback before ready: restart both duration and sample window.
  AkPeer::processAt(device, kStart + 800 * kMs);
  EXPECT_TRUE(CanBusTestPeer::drain(*bus).empty());
  for (int i = 0; i < 10; ++i) {
    AkPeer::receiveAt(device, akStatus(800), kStart + (820 + i * 20) * kMs);
    expectUnavailable(device);
    AkPeer::processAt(device, kStart + (820 + i * 20) * kMs);
    EXPECT_TRUE(CanBusTestPeer::drain(*bus).empty());
  }
  AkPeer::receiveAt(device, akStatus(800), kStart + 1020 * kMs);
  AkPeer::processAt(device, kStart + 1020 * kMs);
  EXPECT_EQ(CanBusTestPeer::drain(*bus).size(), 1u);
}

TEST(Drivers, AkStartupWarningOncePerSecondPerMotorUntilReady) {
  Ak first, second;
  EXPECT_FALSE(AkPeer::warningDue(first, kStart + 10000 * kMs));
  AkPeer::receiveAt(first, akStatus(900, 5), kStart);
  AkPeer::receiveAt(second, akStatus(900), kStart);
  EXPECT_FALSE(AkPeer::warningDue(first, kStart + 999 * kMs));
  EXPECT_TRUE(AkPeer::warningDue(first, kStart + 1000 * kMs));
  EXPECT_TRUE(AkPeer::warningDue(second, kStart + 1000 * kMs));
  EXPECT_FALSE(AkPeer::warningDue(first, kStart + 1000 * kMs));
  EXPECT_FALSE(AkPeer::warningDue(first, kStart + 1999 * kMs));
  EXPECT_TRUE(AkPeer::warningDue(first, kStart + 2000 * kMs));
  for (int i = 0; i <= 10; ++i)
    AkPeer::receiveAt(first, akStatus(900), kStart + (2020 + i * 20) * kMs);
  EXPECT_FALSE(AkPeer::warningDue(first, kStart + 10000 * kMs));
}

TEST(Drivers, AkStartupConfigurationIsValidatedAndApplied) {
  Ak device;
  hardware_interface::ComponentInfo info;
  for (const auto &setting : {std::pair<const char *, const char *>{"startup_stable_samples", "0"},
                              {"startup_min_duration_ms", "-1"},
                              {"startup_max_gap_ms", "501"},
                              {"startup_position_tolerance_tenths_deg", "0.1"}}) {
    info.parameters = {{setting.first, setting.second}};
    EXPECT_THROW(AkPeer::configureStartup(device, info), std::exception);
  }
  info.parameters = {{"startup_min_duration_ms", "0"},
                     {"startup_stable_samples", "2"},
                     {"startup_position_tolerance_tenths_deg", "0"}};
  AkPeer::configureStartup(device, info);
  AkPeer::receiveAt(device, akStatus(900), kStart);
  AkPeer::receiveAt(device, akStatus(901), kStart + 20 * kMs);
  expectUnavailable(device);
  AkPeer::receiveAt(device, akStatus(901), kStart + 40 * kMs);
  CanFeedback feedback;
  device.copy_feedback(feedback);
  EXPECT_NEAR(feedback.position, 90.1 * M_PI / 180, 1e-12);
}

TEST(Drivers, CancelActivationWhenRosContextStops) {
  auto context = std::make_shared<rclcpp::Context>();
  context->init(0, nullptr);
  rclcpp::NodeOptions options;
  options.context(context);
  auto node = std::make_shared<rclcpp::Node>("activation_cancel_test", options);
  Output device;
  auto bus = OutputPeer::prepare(device);
  OutputPeer::ready(device);
  OutputPeer::node(device, node.get());
  std::thread shutdown([&] {
    std::this_thread::sleep_for(std::chrono::milliseconds(25));
    context->shutdown("test cancellation");
  });
  const auto start = std::chrono::steady_clock::now();
  const bool active = device.on_activate();
  shutdown.join();
  EXPECT_FALSE(active);
  EXPECT_LT(
      std::chrono::duration<double>(std::chrono::steady_clock::now() - start)
          .count(),
      .15);
  EXPECT_TRUE(CanBusTestPeer::drain(*bus).empty());
}
