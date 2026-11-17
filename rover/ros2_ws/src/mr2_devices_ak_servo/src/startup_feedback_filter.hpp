#pragma once

#include <algorithm>
#include <cstdint>
#include <vector>

namespace mr2_devices_ak_servo {

// Startup only: once ready, ordinary motion and the existing timeout policy
// remain unchanged. The owner holds feedback_mutex_ for every access.
class StartupFeedbackFilter {
public:
  struct Config {
    int64_t min_duration_ns{200000000};
    size_t stable_samples{5};
    int tolerance_tenths_deg{1};
    int64_t max_gap_ns{100000000};
  };

  StartupFeedbackFilter() { reset(Config{}); }

  void reset(const Config &config) {
    config_ = config;
    samples_.clear();
    samples_.reserve(config_.stable_samples);
    next_sample_ = 0;
    first_ns_ = last_ns_ = next_warning_ns_ = -1;
    ready_ = false;
  }

  bool observe(int16_t position_tenths_deg, uint8_t error, int64_t now_ns) {
    if (ready_)
      return true;
    if (next_warning_ns_ < 0)
      next_warning_ns_ = now_ns + kWarningPeriodNs;
    if (first_ns_ < 0 || now_ns <= last_ns_ ||
        now_ns - last_ns_ > config_.max_gap_ns) {
      first_ns_ = now_ns;
      samples_.clear();
      next_sample_ = 0;
    }
    last_ns_ = now_ns;
    if (error != 0) {
      samples_.clear();
      next_sample_ = 0;
      return false;
    }

    if (samples_.size() < config_.stable_samples)
      samples_.push_back(position_tenths_deg);
    else {
      samples_[next_sample_] = position_tenths_deg;
      next_sample_ = (next_sample_ + 1) % config_.stable_samples;
    }
    const auto bounds = std::minmax_element(samples_.begin(), samples_.end());
    ready_ = samples_.size() == config_.stable_samples &&
             now_ns - first_ns_ >= config_.min_duration_ns &&
             static_cast<int>(*bounds.second) - *bounds.first <=
                 config_.tolerance_tenths_deg;
    return ready_;
  }

  bool warning_due(int64_t now_ns) {
    if (ready_ || next_warning_ns_ < 0 || now_ns < next_warning_ns_)
      return false;
    next_warning_ns_ = now_ns + kWarningPeriodNs;
    return true;
  }

private:
  static constexpr int64_t kWarningPeriodNs = 1000000000; // mr2_control diagnostics: 1 Hz
  Config config_;
  std::vector<int16_t> samples_;
  size_t next_sample_{};
  int64_t first_ns_{-1}, last_ns_{-1}, next_warning_ns_{-1};
  bool ready_{};
};

} // namespace mr2_devices_ak_servo
