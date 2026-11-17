#pragma once
#include <array>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <mutex>
#include <vector>
namespace mr2_control {
using Clock = std::chrono::steady_clock;
struct Twist {
  struct Vector {
    double x{}, y{}, z{};
  } linear, angular;
};
struct DriveCommand {
  Twist twist;
  Clock::time_point received{};
  bool valid{false};
};
struct PositionCommand {
  std::vector<double> values;
  Clock::time_point received{};
  uint64_t sequence{};
  bool valid{false};
};
struct Inputs {
  DriveCommand manual, autonomous;
  uint8_t mission_state{};
  PositionCommand arm, gripper;
};
// Copy payload and timing together; processing happens after releasing the
// lock.
template <class T> class Latest {
public:
  explicit Latest(T value = {}) : value_(std::move(value)) {}
  void copyInto(T &out) const {
    std::lock_guard<std::mutex> lock(mutex_);
    out = value_;
  }
  template <class F> void update(F f) {
    std::lock_guard<std::mutex> lock(mutex_);
    f(value_);
  }

private:
  mutable std::mutex mutex_;
  T value_;
};
} // namespace mr2_control
