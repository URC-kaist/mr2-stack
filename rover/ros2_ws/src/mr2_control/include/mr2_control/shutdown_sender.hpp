#pragma once
#include <cerrno>
#include <cstring>
#include <linux/can.h>
#include <linux/can/raw.h>
#include <net/if.h>
#include <stdexcept>
#include <string>
#include <sys/socket.h>
#include <unistd.h>

namespace mr2_control {
// Runtime-owned socket, used only after the normal command producer and bus
// worker have stopped. Successful write means kernel acceptance, not an ECU
// ack.
class ShutdownSender {
public:
  explicit ShutdownSender(const std::string &iface) {
    const unsigned index = if_nametoindex(iface.c_str());
    if (!index)
      throw std::runtime_error("Unknown shutdown CAN interface: " + iface);
    fd_ = socket(PF_CAN, SOCK_RAW | SOCK_NONBLOCK | SOCK_CLOEXEC, CAN_RAW);
    if (fd_ < 0)
      throw std::runtime_error("Cannot open shutdown CAN socket");
    sockaddr_can address{};
    address.can_family = AF_CAN;
    address.can_ifindex = index;
    if (setsockopt(fd_, SOL_CAN_RAW, CAN_RAW_FILTER, nullptr, 0) < 0 ||
        bind(fd_, reinterpret_cast<sockaddr *>(&address), sizeof(address)) <
            0) {
      close(fd_);
      throw std::runtime_error("Cannot bind shutdown CAN socket: " + iface);
    }
  }
  ~ShutdownSender() { close(fd_); }
  ShutdownSender(const ShutdownSender &) = delete;
  ShutdownSender &operator=(const ShutdownSender &) = delete;
  bool send(const can_frame &frame) const {
    ssize_t result;
    do {
      result = write(fd_, &frame, sizeof(frame));
    } while (result < 0 && errno == EINTR);
    return result == sizeof(frame);
  }

private:
  int fd_{-1};
};
} // namespace mr2_control
