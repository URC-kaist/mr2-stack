#pragma once
#include <cmath>
#include <cstdint>
#include <cstring>
#include <limits>
#include <linux/can.h>

namespace mr2_devices_robstride::protocol {
// RS06User Manual251112, private CAN protocol (not MIT/CANopen).
constexpr uint8_t kFeedback = 2, kEnable = 3, kStop = 4;
constexpr uint8_t kRead = 17, kWrite = 18, kFault = 21;
constexpr uint16_t kRunMode = 0x7005, kSpeed = 0x700A;
constexpr uint16_t kCurrentLimit = 0x7018, kAcceleration = 0x7022;
constexpr uint16_t kCanTimeout = 0x7028;

inline can_frame command(uint8_t type, uint8_t host, uint8_t motor) {
  can_frame f{};
  f.can_id = CAN_EFF_FLAG | (uint32_t(type) << 24) |
             (uint32_t(host) << 8) | motor;
  f.can_dlc = 8;
  return f;
}
inline uint16_t u16_le(const uint8_t *p) {
  return uint16_t(p[0]) | (uint16_t(p[1]) << 8);
}
inline uint32_t u32_le(const uint8_t *p) {
  return uint32_t(p[0]) | (uint32_t(p[1]) << 8) |
         (uint32_t(p[2]) << 16) | (uint32_t(p[3]) << 24);
}
inline void put_u32_le(uint8_t *p, uint32_t value) {
  for (int i = 0; i < 4; ++i) p[i] = (value >> (8 * i)) & 0xff;
}
inline can_frame parameter(uint8_t type, uint8_t host, uint8_t motor,
                           uint16_t index, uint32_t value = 0) {
  auto f = command(type, host, motor);
  f.data[0] = index & 0xff;
  f.data[1] = index >> 8;
  put_u32_le(f.data + 4, value);
  return f;
}
inline can_frame write_float(uint8_t host, uint8_t motor, uint16_t index,
                             float value) {
  static_assert(sizeof(float) == 4 && std::numeric_limits<float>::is_iec559);
  uint32_t bits;
  std::memcpy(&bits, &value, sizeof(bits));
  return parameter(kWrite, host, motor, index, bits);
}
// Include flags in the mask: standard, RTR and error frames must not match.
constexpr uint32_t kReplyMask = CAN_EFF_FLAG | CAN_RTR_FLAG | CAN_ERR_FLAG |
                                0x1f00ffffU;
inline uint32_t reply_id(uint8_t type, uint8_t host, uint8_t motor) {
  return CAN_EFF_FLAG | (uint32_t(type) << 24) |
         (uint32_t(motor) << 8) | host;
}
inline bool is_reply(const can_frame &f, uint8_t type, uint8_t host,
                     uint8_t motor) {
  return f.can_dlc == 8 &&
         (f.can_id & kReplyMask) == reply_id(type, host, motor);
}
struct Scale {
  double position_max{12.57}, velocity_max{50.0}, torque_max{36.0};
};
struct Feedback {
  double position, velocity, torque, temperature;
  uint8_t faults, mode;
};
inline double symmetric_u16(const uint8_t *p, double maximum) {
  const auto value = (uint16_t(p[0]) << 8) | p[1];
  return double(value) * (2.0 * maximum / 65535.0) - maximum;
}
inline Feedback decode(const can_frame &f, const Scale &s) {
  return {symmetric_u16(f.data, s.position_max),
          symmetric_u16(f.data + 2, s.velocity_max),
          symmetric_u16(f.data + 4, s.torque_max),
          double((uint16_t(f.data[6]) << 8) | f.data[7]) / 10.0,
          uint8_t((f.can_id >> 16) & 0x3f),
          uint8_t((f.can_id >> 22) & 3)};
}
} // namespace mr2_devices_robstride::protocol
