#pragma once
#include <cstdint>
struct SenderControl
{
  uint32_t magic{0x4d523243};
  uint32_t encoder_kbps{0}, wire_kbps{0};
  uint32_t width{0}, height{0}, fps{0}, vbv{0}, key_interval{0}, flags{0};
};
static_assert(sizeof(SenderControl) == 36);
