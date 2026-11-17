#pragma once
#include "mr2_control/types.hpp"
#include <atomic>
#include <condition_variable>
#include <exception>
#include <functional>
#include <mutex>
#include <thread>
namespace mr2_control {
struct CycleTiming {
  uint64_t cycles{}, missed_deadlines{};
  double last_work_us{}, max_work_us{}, max_lateness_us{};
  std::array<uint64_t, 6>
      work_histogram{}; // <=100,500,1000,5000,10000, >10000 us
};
class ControlLoop {
public:
  ~ControlLoop() { stop(); }
  template <class Step> void start(double hz, Step step) {
    if (!std::isfinite(hz) || hz <= 0.0 || hz > 10000.0)
      throw std::invalid_argument("control update_rate must be in (0, 10000]");
    const auto period = std::chrono::duration_cast<Clock::duration>(
        std::chrono::duration<double>(1.0 / hz));
    running_ = true;
    thread_ = std::thread([this, period, step] {
      auto previous = Clock::now() - period;
      auto deadline = previous + period;
      CycleTiming timing;
      try {
        while (running_) {
          {
            std::unique_lock<std::mutex> lock(wait_mutex_);
            wake_.wait_until(lock, deadline, [this] { return !running_; });
          }
          if (!running_)
            break;
          const auto now = Clock::now();
          timing.max_lateness_us =
              std::max(timing.max_lateness_us,
                       std::chrono::duration<double, std::micro>(now - deadline)
                           .count());
          step(now, std::chrono::duration<double>(now - previous).count(),
               timing);
          previous = now;
          const auto end = Clock::now();
          timing.last_work_us =
              std::chrono::duration<double, std::micro>(end - now).count();
          timing.max_work_us =
              std::max(timing.max_work_us, timing.last_work_us);
          const std::array<double, 5> bounds{100, 500, 1000, 5000, 10000};
          size_t bucket = 0;
          while (bucket < bounds.size() && timing.last_work_us > bounds[bucket])
            ++bucket;
          ++timing.work_histogram[bucket];
          ++timing.cycles;
          deadline += period;
          // Skip missed slots, preserving the absolute schedule; no catch-up
          // burst.
          if (end >= deadline) {
            const auto missed = (end - deadline) / period + 1;
            timing.missed_deadlines += missed;
            deadline += period * missed;
          }
        }
      } catch (...) {
        failure_ = std::current_exception();
        failed_ = true;
        running_ = false;
      }
    });
  }
  void stop() {
    running_ = false;
    wake_.notify_all();
    if (thread_.joinable())
      thread_.join();
  }
  bool failed() const { return failed_; }
  std::exception_ptr failure() const { return failed_ ? failure_ : nullptr; }

private:
  std::exception_ptr failure_;
  std::atomic<bool> running_{false}, failed_{false};
  std::mutex wait_mutex_;
  std::condition_variable wake_;
  std::thread thread_;
};
} // namespace mr2_control
