#pragma once
#include "mr2_control/types.hpp"
#include <cstddef>
#include <vector>
namespace mr2_control {
// Forward positions in explicit configured order, with no invented timeout.
class PositionProcessor {
public:
  explicit PositionProcessor(std::vector<size_t> indices = {})
      : indices_(std::move(indices)) {}
  void setActive(bool active, uint64_t current_sequence) {
    if (active != active_) {
      active_ = active;
      minimum_sequence_ = current_sequence;
    }
  }
  template <class Joints>
  void update(const PositionCommand &input, Joints &joints) {
    if (!active_ || !input.valid || input.sequence <= minimum_sequence_ ||
        input.values.size() != indices_.size())
      return;
    for (double value : input.values)
      if (!std::isfinite(value))
        return;
    for (size_t i = 0; i < indices_.size(); ++i)
      joints[indices_[i]].command = input.values[i];
  }
  bool active() const { return active_; }

private:
  std::vector<size_t> indices_;
  uint64_t minimum_sequence_{};
  bool active_{};
};
class ArmProcessor : public PositionProcessor {
public:
  using PositionProcessor::PositionProcessor;
};
class GripperProcessor : public PositionProcessor {
public:
  using PositionProcessor::PositionProcessor;
};
} // namespace mr2_control
