#include "video_control.hpp"
#undef NDEBUG
#include <cassert>
#include <numeric>
#include <random>
#include <iostream>
using namespace video_control;
int main()
{
  assert(
    (allocate(6000, {{true, 5000}, {true, 5000}, {false, 5000}, {false, 5000}}) ==
    std::vector<int>{2400, 2400, 600, 600}));
  assert((allocate(6000, {{true, 1000}, {false, 9000}}) == std::vector<int>{1000, 5000}));
  assert((allocate(6000, {{true, 9000}, {false, 100}}) == std::vector<int>{5900, 100}));
  assert((allocate(6000, {{false, 9000}, {false, 9000}}) == std::vector<int>{3000, 3000}));
  assert((allocate(6000, {{true, 0}, {false, 0}}) == std::vector<int>{0, 0}));
  assert(
    (allocate(
      300,
      {{false, 1800}, {false, 1800}, {false, 1800}}) == std::vector<int>{300, 0, 0}));
  assert(
    (allocate(
      600,
      {{false, 1800}, {false, 1800}, {false, 1800}}) == std::vector<int>{300, 300, 0}));
  assert(
    (allocate(
      1500, {{true, 1800}, {false, 1800}, {false, 1800}, {false,
          1800}}) == std::vector<int>{1200, 300, 0, 0}));
  assert((allocate(100, {{false, 1800}, {true, 1800}}) == std::vector<int>{0, 100}));
  assert(
    (allocate(
      300,
      {{false, 0}, {false, 1800}, {false, 1800}}) == std::vector<int>{0, 300, 0}));
  for (int budget = 0; budget < 200; ++budget) {
    const auto out = allocate(budget, {{true, 100}, {false, 10}, {true, 30}});
    assert(std::accumulate(out.begin(), out.end(), 0) <= std::min(budget, 140));
  }
  assert(profile(1300, 640, 360, 30).width == 640);
  assert(profile(700, 640, 480, 30).height == 360);
  assert(profile(200, 640, 360, 30).fps == 15);
  assert(newer(1, 0xffffffff)); assert(!newer(0xffffffff, 1));
  std::mt19937 random(20261029);
  for (int trial = 0; trial < 100000; ++trial) {
    const int count = random() % 16 + 1, budget = random() % 30001;
    std::vector<Demand> demands;
    int capacity = 0;
    for (int i = 0; i < count; ++i) {
      const int maximum = random() % 5 ? 100 + random() % 1701 : 0;
      demands.push_back({bool(random() % 2), maximum}); capacity += maximum;
    }
    const auto rates = allocate(budget, demands);
    const int used = std::accumulate(rates.begin(), rates.end(), 0);
    assert(used <= std::min(budget, capacity));
    assert(used > std::min(budget, capacity) - kMinimumWireKbps);
    bool waiting[2] = {false, false};
    for (int i = 0; i < count; ++i) {
      assert(rates[i] >= 0 && rates[i] <= demands[i].maximum_kbps);
      if (!demands[i].maximum_kbps) {continue;}
      assert(!waiting[demands[i].high] || rates[i] == 0);
      if (!rates[i]) {waiting[demands[i].high] = true;}
    }
  }
  std::cout << "100000 allocation scenarios: conservation, caps and JSON order passed\n";
}
