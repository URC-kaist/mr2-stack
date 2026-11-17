#include "mr2_control/control_loop.hpp"
#include "mr2_control/drive_processor.hpp"
#include "mr2_control/drive_selector.hpp"
#include "mr2_control/position_processor.hpp"
#include <gtest/gtest.h>
using namespace mr2_control;
TEST(Drive, ForwardReverseTimeoutAndSlew) {
  DriveConfig config;
  config.max_wheel_linear_speed = .8;
  DriveProcessor drive(config);
  std::array<double, 8> state{};
  const auto now = Clock::now();
  DriveCommand command;
  command.valid = true;
  command.received = now;
  command.twist.linear.x = .8;
  auto targets = drive.update(command, state, now, .01);
  EXPECT_NEAR(targets[0], .015 / .17, 1e-12);
  targets = drive.update(command, state, now, .6);
  for (size_t i = 0; i < 4; ++i)
    EXPECT_NEAR(targets[i], .8 / .17, 1e-12);
  targets = drive.update(command, state, now + std::chrono::seconds(1), .01);
  for (double target : targets)
    EXPECT_DOUBLE_EQ(target, 0);
  DriveProcessor reverse(config);
  command.twist.linear.x = -.8;
  targets = reverse.update(command, state, now, 1);
  EXPECT_NEAR(targets[0], -.8 / .17, 1e-12);
  EXPECT_DOUBLE_EQ(targets[4], 0);
}
TEST(Drive, SteeringGateInvalidAndOdom) {
  DriveProcessor drive;
  std::array<double, 8> state{};
  DriveCommand command;
  command.valid = true;
  command.received = Clock::now();
  command.twist.linear.y = .8;
  auto targets = drive.update(command, state, command.received, 1);
  EXPECT_DOUBLE_EQ(targets[0], 0);
  EXPECT_NEAR(targets[4], M_PI / 2, 1e-12);
  state[4] = NAN;
  targets = drive.update(command, state, command.received, 1);
  for (double target : targets)
    EXPECT_DOUBLE_EQ(target, 0);
  auto odom = drive.odometry({1, 1, 1, 1}, {0, 0, 0, 0});
  EXPECT_NEAR(odom.linear.x, .17, 1e-12);
  EXPECT_DOUBLE_EQ(odom.linear.y, 0);
  EXPECT_NEAR(odom.angular.z, 0, 1e-12);
}
TEST(Selector, PreservesSourceAge) {
  Inputs inputs;
  inputs.manual.valid = inputs.autonomous.valid = true;
  inputs.manual.received = Clock::now();
  inputs.autonomous.received = inputs.manual.received - std::chrono::seconds(2);
  DriveSelector selector;
  EXPECT_EQ(selector.select(inputs).received, inputs.manual.received);
  inputs.mission_state = 1;
  EXPECT_EQ(selector.select(inputs).received, inputs.autonomous.received);
}
TEST(Position, ActivationOrderAndMalformedInput) {
  struct Joint {
    double command = NAN;
  };
  std::vector<Joint> joints(2);
  ArmProcessor processor({1, 0});
  PositionCommand command;
  command.values = {2, 3};
  command.valid = true;
  command.sequence = 1;
  processor.update(command, joints);
  EXPECT_TRUE(std::isnan(joints[0].command));
  processor.setActive(true, 1);
  processor.update(command, joints);
  EXPECT_TRUE(std::isnan(joints[0].command));
  command.sequence = 2;
  processor.update(command, joints);
  EXPECT_EQ(joints[0].command, 3);
  EXPECT_EQ(joints[1].command, 2);
  command.values = {NAN, 4};
  processor.update(command, joints);
  EXPECT_EQ(joints[0].command, 3);
  command.values = {4};
  processor.update(command, joints);
  EXPECT_EQ(joints[0].command, 3);
  processor.setActive(false, 2);
  command.values = {5, 6};
  processor.update(command, joints);
  EXPECT_EQ(joints[0].command, 3);
}
TEST(Inputs, ConcurrentPayloadAndTimestampRemainCoherent) {
  Latest<Inputs> buffer;
  std::atomic<bool> done{false};
  std::thread writer([&] {
    for (int64_t i = 1; i <= 100000; ++i)
      buffer.update([&](Inputs &in) {
        in.manual.twist.linear.x = static_cast<double>(i);
        in.manual.twist.linear.y = -static_cast<double>(i);
        in.manual.received = Clock::time_point(std::chrono::nanoseconds(i));
        in.manual.valid = true;
      });
    done = true;
  });
  while (!done) {
    Inputs copy;
    buffer.copyInto(copy);
    if (copy.manual.valid) {
      EXPECT_EQ(copy.manual.twist.linear.x, -copy.manual.twist.linear.y);
      EXPECT_EQ(copy.manual.twist.linear.x,
                std::chrono::duration_cast<std::chrono::nanoseconds>(
                    copy.manual.received.time_since_epoch())
                    .count());
    }
  }
  writer.join();
}
TEST(Scheduling, SkipsMissedDeadlinesAndStopsCleanly) {
  ControlLoop loop;
  CycleTiming observed;
  loop.start(100, [&](Clock::time_point, double dt, const CycleTiming &timing) {
    observed = timing;
    if (timing.cycles % 10 == 0)
      std::this_thread::sleep_for(std::chrono::milliseconds(25));
    EXPECT_GT(dt, 0);
  });
  std::this_thread::sleep_for(std::chrono::milliseconds(550));
  loop.stop();
  EXPECT_FALSE(loop.failed());
  EXPECT_GT(observed.cycles, 20u);
  EXPECT_GT(observed.missed_deadlines, 0u);
  EXPECT_GE(observed.max_work_us, 25000);
  std::cout << "timing injected 25ms work: cycles=" << observed.cycles
            << " skipped=" << observed.missed_deadlines
            << " max_work_us=" << observed.max_work_us
            << " max_lateness_us=" << observed.max_lateness_us << std::endl;
}
TEST(Scheduling, ReportsWorkerFailureAndJoins) {
  ControlLoop loop;
  loop.start(100, [](Clock::time_point, double, const CycleTiming &) {
    throw std::runtime_error("injected failure");
  });
  std::this_thread::sleep_for(std::chrono::milliseconds(30));
  loop.stop();
  ASSERT_TRUE(loop.failed());
  EXPECT_THROW(std::rethrow_exception(loop.failure()), std::runtime_error);
}
