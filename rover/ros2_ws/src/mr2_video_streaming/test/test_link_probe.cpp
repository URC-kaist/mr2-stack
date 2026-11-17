#include "link_probe.hpp"
#include <poll.h>
#include <iostream>
#include <stdexcept>

static void require(bool value, const char * message)
{
  if (!value) {throw std::runtime_error(message);}
}
int main()
{
  gst_init(nullptr, nullptr);
  require(
    link_probe::token(
      100,
      7) == 1759324492u,
    "probe session token differs from the base implementation");
  require(link_probe::token(123, 42) == 774359804u, "probe session/id mixing mismatch");
  require(
    link_probe::token(100, 7) != link_probe::token(
      101,
      7),
    "old gateway session reused a probe token");
  const int fd = socket(AF_INET, SOCK_DGRAM | SOCK_NONBLOCK, 0);
  sockaddr_in local{}; local.sin_family = AF_INET; local.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
  require(bind(fd, reinterpret_cast<sockaddr *>(&local), sizeof(local)) == 0, "bind failed");
  socklen_t size = sizeof(local); getsockname(fd, reinterpret_cast<sockaddr *>(&local), &size);
  link_probe::Sender sender("127.0.0.1", ntohs(local.sin_port));
  for (int rate : {16, 32, 100, 1000, 8000, 16000}) {
    const auto started = rtp_send_time::mono_us();
    sender.start(42, rate, 200);
    int count = 0;
    uint64_t first = 0, last = 0;
    while (rtp_send_time::mono_us() - started < 300000) {
      uint8_t data[2048];
      const auto bytes = recv(fd, data, sizeof(data), MSG_DONTWAIT);
      if (bytes < 0) {pollfd p{fd, POLLIN, 0}; poll(&p, 1, 10); continue;}
      GstBuffer * buffer = gst_buffer_new_allocate(nullptr, bytes, nullptr);
      gst_buffer_fill(buffer, 0, data, bytes);
      GstRTPBuffer rtp = GST_RTP_BUFFER_INIT;
      require(gst_rtp_buffer_map(buffer, GST_MAP_READ, &rtp), "invalid RTP");
      require(gst_rtp_buffer_get_ssrc(&rtp) == link_probe::kSsrc, "wrong probe SSRC");
      require(gst_rtp_buffer_get_payload_type(&rtp) == 127, "probe sent as H264");
      require(gst_rtp_buffer_get_timestamp(&rtp) == 42, "wrong cluster id");
      guint8 app = 0; gpointer ext = nullptr; guint length = 0;
      require(
        gst_rtp_buffer_get_extension_twobytes_header(
          &rtp, &app, 7, 0, &ext,
          &length) && length == 16,
        "missing send-time extension");
      const auto at = GST_READ_UINT64_BE(static_cast<uint8_t *>(ext));
      if (!first) {first = at;}
      last = at; ++count;
      gst_rtp_buffer_unmap(&rtp); gst_buffer_unref(buffer);
    }
    require(count >= 5, "too few probe packets");
    require(last - first >= 30000 && last - started < 250000, "unbounded cluster duration");
  }
  sender.start(99, 1000, 1000);
  const auto cancel = rtp_send_time::mono_us(); sender.cancel();
  require(rtp_send_time::mono_us() - cancel < 20000, "probe cancellation blocked video control");
  close(fd);
  std::cout << "Probe framing, six rates, timing and cancellation passed\n";
}
