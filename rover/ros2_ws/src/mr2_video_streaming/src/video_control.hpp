#pragma once

#include <algorithm>
#include <cmath>
#include <cstdint>
#include <stdexcept>
#include <vector>

namespace video_control
{
constexpr uint8_t kSet = 0x40, kFeedback = 0x41, kState = 0x42;
constexpr uint8_t kKeyframe = 0x43;
enum State : uint8_t { OFF = 0, ON = 1, NO_DEVICE = 2 };
inline uint32_t read(const std::vector<uint8_t> & b, size_t at, size_t n = 4)
{
  if (at + n > b.size()) {throw std::invalid_argument("short video control packet");}
  uint32_t v = 0;
  for (size_t i = 0; i < n; ++i) {
    v |= uint32_t(b[at + i]) << (8 * i);
  }
  return v;
}
inline void append(std::vector<uint8_t> & b, uint32_t v, size_t n = 4)
{
  for (size_t i = 0; i < n; ++i) {
    b.push_back((v >> (8 * i)) & 255);
  }
}
inline bool newer(uint32_t a, uint32_t b) {return int32_t(a - b) > 0;}
struct Demand { bool high; int maximum_kbps; };
constexpr int kMinimumWireKbps = 100;
constexpr int kStartWireKbps = 300;
// Preserve JSON order within each class. Start a prefix with useful budgets
// instead of dividing a small pool into streams that cannot send an IDR.
inline std::vector<int> allocate(int budget, const std::vector<Demand> & demands)
{
  std::vector<int> out(demands.size(), 0);
  std::vector<size_t> groups[2];
  int capacity[2] = {0, 0};
  for (size_t i = 0; i < demands.size(); ++i) {
    if (demands[i].maximum_kbps <= 0) {continue;}
    const int group = demands[i].high ? 1 : 0;
    groups[group].push_back(i); capacity[group] += demands[i].maximum_kbps;
  }
  budget = std::max(0, budget);
  int pools[2] = {budget - budget * 8 / 10, budget * 8 / 10};
  for (int group : {1, 0}) {
    const int spare = std::max(0, pools[group] - capacity[group]);
    pools[group] -= spare; pools[1 - group] += spare;
  }
  auto fill = [&](int group, int pool) {
      const auto & all = groups[group];
      if (all.empty() || pool <= 0) {return pool;}
      std::vector<size_t> ids;
      size_t next = 0;
      while (next < all.size() && out[all[next]] > 0) {ids.push_back(all[next++]);}
      // Small configured caps should not prevent a later camera from starting.
      int required = 0;
      while (ids.empty() && next < all.size() &&
        pool >= std::min(kMinimumWireKbps, demands[all[next]].maximum_kbps))
      {
        const auto i = all[next];
        const int amount = std::min(kStartWireKbps, demands[i].maximum_kbps);
        if (required && required + amount > pool) {break;}
        ids.push_back(i); required += amount; ++next;
        // Select a prefix that can share the startup target.
        while (next < all.size() &&
          required + std::min(kStartWireKbps, demands[all[next]].maximum_kbps) <= pool)
        {
          required += std::min(kStartWireKbps, demands[all[next]].maximum_kbps);
          ids.push_back(all[next++]);
        }
      }
      while (pool > 0) {
        ids.erase(
          std::remove_if(
            ids.begin(), ids.end(), [&](size_t i) {
              return out[i] >= demands[i].maximum_kbps;
            }), ids.end());
        if (ids.empty()) {
          if (next >= all.size() ||
            pool < std::min(kMinimumWireKbps, demands[all[next]].maximum_kbps)) {break;}
          ids.push_back(all[next++]);
        }
        const int share = std::max(1, pool / int(ids.size()));
        for (auto i : ids) {
          const int amount = std::min({share, pool, demands[i].maximum_kbps - out[i]});
          out[i] += amount; pool -= amount;
        }
      }
      return pool;
    };
  int unused = fill(1, pools[1]) + fill(0, pools[0]);
  unused = fill(1, unused);
  unused = fill(0, unused);
  if (budget < kMinimumWireKbps && unused > 0) {
    for (int group : {1, 0}) {
      if (groups[group].empty()) {continue;}
      const auto i = groups[group].front();
      const int amount = std::min(unused, demands[i].maximum_kbps - out[i]);
      out[i] += amount; unused -= amount;
    }
  }
  return out;
}
struct Profile { int width, height, fps; };
inline Profile profile(int kbps, int capture_width, int capture_height, int max_fps)
{
  const double scale = kbps >= 1200 ? 1.0 : kbps >= 650 ? .75 : kbps >= 180 ? .5 :
    kbps >= 100 ? .375 : kbps >= 60 ? .25 : .125;
  return {std::max(2, int(capture_width * scale) / 2 * 2),
    std::max(2, int(capture_height * scale) / 2 * 2),
    std::min(max_fps, kbps >= 300 ? 30 : kbps >= 180 ? 15 : kbps >= 100 ? 10 : kbps >= 60 ? 5 : 3)};
}

inline int vbv_bits(int encoder_kbps, int fps)
{
  return std::max(1000, encoder_kbps * std::max(100, 1000 / std::max(1, fps) + 1));
}
inline bool use_hardware_encoder(bool requested, int width)
{
  return requested && width >= 128;
}
inline int keyframe_interval(int configured, int fps)
{
  return std::max(1, std::min(configured, fps));
}
}  // namespace video_control
