#include "receiver_correlation.hpp"
#include <cstdlib>
#include <iostream>
#include <vector>
static void check(bool c, const char * m) {if (!c) {std::cerr << m << '\n'; std::exit(1);}}
static void packet(
  ReceiverCorrelation & c, std::vector<uint8_t> payload, int seq, bool marker,
  uint32_t ts = 3000)
{
  auto * b = gst_rtp_buffer_new_allocate(payload.size(), 0, 0);
  GstRTPBuffer r = GST_RTP_BUFFER_INIT;
  gst_rtp_buffer_map(b, GST_MAP_WRITE, &r);
  gst_rtp_buffer_set_ssrc(&r, 123); gst_rtp_buffer_set_timestamp(&r, ts); gst_rtp_buffer_set_seq(
    &r,
    seq);
  gst_rtp_buffer_set_marker(&r, marker);
  std::copy(payload.begin(), payload.end(), static_cast<uint8_t *>(gst_rtp_buffer_get_payload(&r)));
  gst_rtp_buffer_unmap(&r); GST_BUFFER_PTS(b) = GST_SECOND;
  c.packet(b); gst_buffer_unref(b);
}
int main(int argc, char ** argv)
{
  gst_init(&argc, &argv); ReceiverCorrelation c; ReceiverCorrelation::Identity id;
  std::vector<uint8_t> au{0, 0, 0, 1, 0x65, 0x11, 0x22, 0x33, 0x44};
  packet(c, {0x7c, 0x85, 0x11, 0x22}, 1, false); packet(c, {0x7c, 0x45, 0x33, 0x44}, 2, true);
  check(c.match(au.data(), au.size(), GST_SECOND, id), "complete FU-A did not correlate");
  check(id.ssrc == 123 && id.timestamp == 3000 && id.sequence == 2, "wrong marker identity");
  check(!c.match(au.data(), au.size(), GST_SECOND, id), "identity was reused");
  packet(c, {0x7c, 0x85, 0x11, 0x22}, 4, false, 6000); packet(
    c, {0x7c, 0x45, 0x33, 0x44}, 6, true,
    6000);
  check(!c.match(au.data(), au.size(), GST_SECOND, id), "fragment gap acquired timing");
  packet(c, {0x65, 0x11, 0x22, 0x33, 0x44}, 8, true, 9000);
  check(
    !c.match(
      au.data(),
      au.size(), GST_SECOND + 10 * GST_MSECOND, id), "wrong frame PTS acquired timing");
  check(c.match(au.data(), au.size(), GST_SECOND + 1, id), "rounded AU PTS failed correlation");
  std::cout << "native AU correlation and loss isolation passed\n";
}
