#pragma once
#include "mr2_control/types.hpp"
namespace mr2_control {
class DriveSelector {
public:
  DriveCommand select(const Inputs &inputs) const {
    return inputs.mission_state == 1 ? inputs.autonomous : inputs.manual;
  }
};
} // namespace mr2_control
