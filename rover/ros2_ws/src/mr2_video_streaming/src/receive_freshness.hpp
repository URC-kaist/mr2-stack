#pragma once
#include <gst/gst.h>
#include <mutex>
#include <cstdint>
#include <cstring>
#include <unistd.h>
#include <fcntl.h>
#include <cstdlib>

// Private same-host control pipe. No new radio packet format or clock sync is
// required. The causal offset lower bound is learned from XBee-requested probes.
class ReceiveFreshness
{
public:
  static constexpr uint32_t kMagic = 0x4d523246;
  static constexpr uint64_t kMaxAgeUs = 500000;
  struct Policy { bool paused{true}; int64_t lower{0}; uint64_t anchor{0}, until{0}; };

private:
  bool enabled_{false};
  int fd_{-1};
  uint8_t pending_[32]{};
  size_t size_{0};
  std::mutex mutex_;
  Policy policy_;

public:
  ReceiveFreshness()
  {
    if (const auto * value = std::getenv("MR2_VIDEO_FRESHNESS_FD")) {
      enabled_ = true; fd_ = std::atoi(value);
      const auto flags = fcntl(fd_, F_GETFL);
      if (flags >= 0) {fcntl(fd_, F_SETFL, flags | O_NONBLOCK);}
    }
  }
  void set(Policy p) {std::lock_guard<std::mutex> guard(mutex_); enabled_ = true; policy_ = p;}
  void poll()
  {
    if (fd_ < 0) {return;}
    while (true) {
      const auto n = read(fd_, pending_ + size_, sizeof(pending_) - size_);
      if (n <= 0) {return;}
      size_ += n;
      if (size_ != sizeof(pending_)) {continue;}
      size_ = 0;
      if (GST_READ_UINT32_LE(pending_) != kMagic) {set({}); continue;}
      const auto raw = GST_READ_UINT64_LE(pending_ + 8);
      int64_t lower; std::memcpy(&lower, &raw, sizeof(lower));
      set(
        {bool(GST_READ_UINT32_LE(pending_ + 4) & 1), lower,
          GST_READ_UINT64_LE(pending_ + 16), GST_READ_UINT64_LE(pending_ + 24)});
    }
  }
  bool permits(uint64_t send_us, uint64_t now_us)
  {
    std::lock_guard<std::mutex> guard(mutex_);
    if (!enabled_) {return true;}
    if (!send_us || policy_.paused || now_us > policy_.until || now_us < policy_.anchor) {
      return false;
    }
    const auto lower = policy_.lower - int64_t((now_us - policy_.anchor) * 250 / 1000000);
    const int64_t age = int64_t(now_us) - int64_t(send_us) - lower;
    return age >= 0 && age <= int64_t(kMaxAgeUs);
  }
};
