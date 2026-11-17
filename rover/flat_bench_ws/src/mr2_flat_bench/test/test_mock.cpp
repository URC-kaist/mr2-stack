#include "../src/mock_can_device.cpp"
#include <gtest/gtest.h>

namespace mr2_flat_bench {
struct MockTestPeer {
  static void advance(MockCanDevice &d, int64_t ns) { d.advance(ns); }
  static void start(MockCanDevice &d) { d.last_process_ns_ = 1000000000; }
};
} // namespace mr2_flat_bench
using Device = mr2_flat_bench::MockCanDevice;
using Peer = mr2_flat_bench::MockTestPeer;
static void configure(Device &d, const std::string &kind) {
  hardware_interface::ComponentInfo info;
  hardware_interface::InterfaceInfo command;
  command.name = kind;
  info.command_interfaces.push_back(command);
  info.parameters["can_iface"] = "intentionally_missing_interface";
  d.configure(info, nullptr);
  d.on_activate(); Peer::start(d);
}
TEST(MockDevice, VelocityIntegratesAndInvalidCommandStopsWithoutCAN) {
  Device d; configure(d, "velocity");
  int sent = 0;
  d.set_tx_override([&](const can_frame &) { ++sent; });
  double *command; d.export_command(command); *command = 2;
  Peer::advance(d, 1100000000);
  CanFeedback f; d.copy_feedback(f);
  EXPECT_DOUBLE_EQ(f.position, 0.2); EXPECT_DOUBLE_EQ(f.velocity, 2);
  EXPECT_EQ(f.position_received_ns, 1100000000);
  *command = std::numeric_limits<double>::quiet_NaN();
  Peer::advance(d, 1200000000); d.copy_feedback(f);
  EXPECT_DOUBLE_EQ(f.position, 0.2); EXPECT_DOUBLE_EQ(f.velocity, 0);
  EXPECT_EQ(sent, 0);
}
TEST(MockDevice, PositionFollowsTargetAndHoldsOnNaN) {
  Device d; configure(d, "position");
  double *command; d.export_command(command); *command = 0.7;
  Peer::advance(d, 1100000000);
  CanFeedback f; d.copy_feedback(f);
  EXPECT_DOUBLE_EQ(f.position, 0.7); EXPECT_NEAR(f.velocity, 7, 1e-12);
  *command = std::numeric_limits<double>::quiet_NaN();
  Peer::advance(d, 1200000000); d.copy_feedback(f);
  EXPECT_DOUBLE_EQ(f.position, 0.7); EXPECT_DOUBLE_EQ(f.velocity, 0);
}
TEST(MockDevice, InstancesHaveIndependentMemoryAndDeactivateStops) {
  Device a, b; configure(a, "velocity"); configure(b, "velocity");
  double *ca, *cb; a.export_command(ca); b.export_command(cb);
  ASSERT_NE(ca, cb); *ca = -1; *cb = 2;
  Peer::advance(a, 1100000000); Peer::advance(b, 1100000000);
  a.on_deactivate(); Peer::advance(a, 1200000000);
  CanFeedback fa, fb; a.copy_feedback(fa); b.copy_feedback(fb);
  EXPECT_DOUBLE_EQ(fa.position, -0.1); EXPECT_DOUBLE_EQ(fa.velocity, 0);
  EXPECT_DOUBLE_EQ(fb.position, 0.2); EXPECT_DOUBLE_EQ(fb.velocity, 2);
}
TEST(MockDevice, RejectsUnsupportedCommandInterface) {
  Device d;
  EXPECT_THROW(configure(d, "effort"), std::runtime_error);
}
