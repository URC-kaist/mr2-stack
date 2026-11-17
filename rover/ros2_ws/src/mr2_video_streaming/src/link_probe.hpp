#pragma once
#include "rtp_send_time.hpp"
#include <arpa/inet.h>
#include <netdb.h>
#include <sys/socket.h>
#include <unistd.h>
#include <array>
#include <cstring>
#include <string>

namespace link_probe
{
constexpr uint32_t kSsrc = 0x4d523250;
constexpr uint8_t kPayloadType = 127;
inline uint32_t token(uint32_t session, uint16_t id)
{
  if (!session) {return id;}
  uint32_t x = session ^ (uint32_t(id) * 0x9e3779b1u);
  x = (x ^ (x >> 16)) * 0x7feb352du;
  x = (x ^ (x >> 15)) * 0x846ca68bu;
  return x ^ (x >> 16);
}

// Short, paced RTP probe clusters share the configured video UDP path. The
// receiver observes these packets, then drops them before its H.264 decoder.
class Sender
{
public:
  Sender(const std::string & host, int port)
  {
    addrinfo hints{}, * result = nullptr;
    hints.ai_family = AF_INET; hints.ai_socktype = SOCK_DGRAM;
    if (getaddrinfo(host.c_str(), std::to_string(port).c_str(), &hints, &result) == 0) {
      std::memcpy(&destination_, result->ai_addr, sizeof(destination_));
      socket_ = socket(AF_INET, SOCK_DGRAM | SOCK_NONBLOCK | SOCK_CLOEXEC, 0);
      freeaddrinfo(result);
    }
  }
  ~Sender() {cancel(); if (socket_ >= 0) {close(socket_);}}
  void cancel()
  {
    stopping_ = true;
    if (thread_.joinable()) {thread_.join();}
  }
  void start(uint16_t id, int kbps, int duration_ms, uint32_t session = 0)
  {
    if (socket_ < 0 || kbps <= 0 || duration_ms <= 0) {return;}
    cancel(); stopping_ = false;
    thread_ = std::thread(
      [this, id, kbps, duration_ms, session]() {
        std::array<uint8_t, 1120> packet{};
        packet[0] = 0x90; packet[1] = kPayloadType;
        GST_WRITE_UINT32_BE(packet.data() + 4, token(session, id));
        GST_WRITE_UINT32_BE(packet.data() + 8, kSsrc);
        packet[12] = 0x10; packet[13] = 0x00;
        packet[14] = 0; packet[15] = 5;
        packet[16] = rtp_send_time::kId; packet[17] = 16;
        const size_t packet_bytes = std::clamp(
          kbps * duration_ms / 80 - 28, 36,
          int(packet.size()));
        const uint64_t interval = (packet_bytes + 28) * 8000 / std::max(1, kbps);
        const auto start = rtp_send_time::mono_us();
        auto next = start;
        uint16_t sequence = 0;
        while (!stopping_ && rtp_send_time::mono_us() - start < uint64_t(duration_ms) * 1000) {
          const auto now = rtp_send_time::mono_us();
          if (now < next) {
            std::this_thread::sleep_for(
              std::chrono::microseconds(
                std::min<uint64_t>(
                  1000,
                  next - now)));
            continue;
          }
          GST_WRITE_UINT16_BE(packet.data() + 2, sequence++);
          GST_WRITE_UINT64_BE(packet.data() + 18, now);
          GST_WRITE_UINT64_BE(packet.data() + 26, rtp_send_time::epoch_us());
          sendto(
            socket_, packet.data(), packet_bytes, MSG_DONTWAIT | MSG_NOSIGNAL,
            reinterpret_cast<const sockaddr *>(&destination_), sizeof(destination_));
          next = std::max(next + interval, now + uint64_t(50));
        }
      });
  }

private:
  int socket_{-1};
  sockaddr_in destination_{};
  std::thread thread_;
  std::atomic<bool> stopping_{false};
};
}  // namespace link_probe
