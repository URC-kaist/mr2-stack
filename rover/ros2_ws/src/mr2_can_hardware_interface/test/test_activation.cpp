#include "../src/can_hw.cpp"
#include <gtest/gtest.h>

namespace mr2_can_hardware_interface {
struct CanHWTestPeer {
  static void devices(CanHW &hw, std::vector<std::shared_ptr<CanDevice>> devices) {
    hw.node_ = rclcpp::Node::make_shared("can_hw_activation_test");
    hw.devs_ = std::move(devices);
  }
};
} // namespace mr2_can_hardware_interface

struct TestDevice : CanDevice {
  bool succeeds{true};
  int activations{0}, stops{0};
  void configure(const hardware_interface::ComponentInfo &, rclcpp::Node *) override {}
  bool on_activate() override { ++activations; return succeeds; }
  void on_deactivate() override { ++stops; }
  void process(const rclcpp::Time &) override {}
  void export_state(double *&, double *&, double *&) override {}
  void export_command(double *&) override {}
};

TEST(CanHWActivation, LaterDeviceFailureStopsAllConfiguredDevices) {
  rclcpp::init(0, nullptr);
  {
    mr2_can_hardware_interface::CanHW hw;
    auto first = std::make_shared<TestDevice>();
    auto failed = std::make_shared<TestDevice>();
    auto later = std::make_shared<TestDevice>();
    failed->succeeds = false;
    mr2_can_hardware_interface::CanHWTestPeer::devices(hw, {first, failed, later});
    EXPECT_EQ(hw.on_activate(rclcpp_lifecycle::State()), hardware_interface::CallbackReturn::ERROR);
    EXPECT_EQ(first->activations, 1);
    EXPECT_EQ(failed->activations, 1);
    EXPECT_EQ(later->activations, 0);
    EXPECT_EQ(first->stops, 1);
    EXPECT_EQ(failed->stops, 1);
    EXPECT_EQ(later->stops, 1);
  }
  rclcpp::shutdown();
}
