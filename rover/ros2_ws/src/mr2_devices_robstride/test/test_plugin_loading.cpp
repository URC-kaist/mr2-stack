#include "mr2_can_bus_core/can_device.hpp"
#include <gtest/gtest.h>
#include <pluginlib/class_loader.hpp>

TEST(PluginLoading, MixedDeviceClassesAndIndependentCommandMemory) {
  pluginlib::ClassLoader<CanDevice> loader("mr2_can_bus_core", "CanDevice");
  auto custom = loader.createSharedInstance("mr2_devices_output_actuator/CustomActuatorDevice");
  auto rs1 = loader.createSharedInstance("mr2_devices_robstride/RobstrideDevice");
  auto rs2 = loader.createSharedInstance("mr2_devices_robstride/RobstrideDevice");
  double *a, *b, *c;
  custom->export_command(a);
  rs1->export_command(b);
  rs2->export_command(c);
  ASSERT_NE(a, nullptr);
  ASSERT_NE(b, nullptr);
  ASSERT_NE(c, nullptr);
  EXPECT_NE(a, b);
  EXPECT_NE(b, c);
  *a = 1;
  *b = 2;
  *c = -3;
  EXPECT_EQ(*a, 1);
  EXPECT_EQ(*b, 2);
  EXPECT_EQ(*c, -3);
  CanFeedback feedback;
  EXPECT_TRUE(rs1->copy_feedback(feedback));
}
