#include "rtp_send_time.hpp"
#include <cstdlib>
#include <iostream>

static void require(bool condition, const char * message)
{
  if (!condition) {std::cerr << message << '\n'; std::exit(1);}
}

int main()
{
  // The same 45 KB IDR takes about 210 ms at the configured 1800 kbps cap.
  // At 6 FPS this must not cause permanent starvation or a geometry ceiling.
  rtp_send_time::Pacer slow;
  slow.kbps = 1800;
  slow.set_framerate(6, 1);
  require(slow.admit_frame(45000, false, 1000000), "6 FPS IDR was rejected");
  require(slow.admit_frame(34000, true, 1000000), "6 FPS delta was starved by the 30 FPS bound");
  require(!slow.admit_frame(45000, true, 1000000), "delta frame exceeded its source-period bound");
  require(!slow.admit_frame(1000, true, 2000000), "broken GOP delta was sent");
  require(slow.admit_frame(45000, false, 2000000), "IDR did not recover the GOP");
  require(slow.admit_frame(1000, true, 2100000), "recovered GOP stayed blocked");
  require(!slow.admit_frame(90000, false, 3000000), "IDR exceeded 300 ms cap");
  slow.set_framerate(1, 1);
  require(!slow.admit_frame(90000, false, 4000000), "very low FPS removed hard cap");
  slow.set_framerate(0, 0);
  require(slow.frame_deadline_us(false) <= 300000, "unknown FPS removed hard cap");

  rtp_send_time::Pacer fast;
  fast.kbps = 1800;
  fast.set_framerate(30, 1);
  require(fast.admit_frame(45000, false, 1000000), "30 FPS IDR cannot recover within 300 ms");
  require(!fast.admit_frame(75000, false, 1000000), "IDR exceeded 300 ms bound");
  require(fast.admit_frame(1000, false, 2000000), "30 FPS keyframe recovery failed");
  require(!fast.admit_frame(34000, true, 2000000), "30 FPS delta bypassed its 150 ms bound");
  require(fast.admit_frame(1000, false, 2000000), "30 FPS GOP recovery failed");
  fast.next_us = 2200000;
  require(!fast.admit_frame(1000, true, 2000000), "queued data bypassed deadline");
  fast.reset();
  require(fast.next_us == 0 && !fast.recovering, "restart retained pacing debt");
  rtp_send_time::Pacer minimum;
  minimum.kbps = 100; minimum.set_framerate(5, 1);
  require(minimum.admit_frame(2500, false, 1000000), "minimum-rate keyframe rejected");
  require(!minimum.admit_frame(5000, false, 1000000), "minimum rate bypassed deadline");
  minimum.reset();
  require(
    !minimum.admit_frame(
      2500, false, 1000000,
      150000), "old IDR ignored its remaining age budget");
  minimum.reset();
  require(minimum.admit_frame(2500, false, 1000000, 10000), "fresh low-rate IDR was starved");

  SharedWireClock shared, peer;
  require(shared.create(), "shared pacing clock creation failed");
  require(peer.attach(shared.fd()), "shared pacing clock attachment failed");
  shared.set_rate(8000);
  require(peer.rate() == 8000, "wire rate not shared");
  require(shared.reserve(1000, 1000000) == 1000000, "first aggregate reservation delayed");
  require(peer.reserve(1000, 1000000) == 1001000, "independent cameras bypass aggregate cap");
  require(shared.backlog(1000000) == 2000, "aggregate debt mismatch");
  shared.set_rate(1000);
  require(
    peer.reserve(1000, 1000000) == 1002000 && shared.backlog(
      1000000) == 10000, "rate drop lost aggregate debt");

  std::cout << "FPS-aware pacing, hard bounds and GOP recovery passed\n";
}
