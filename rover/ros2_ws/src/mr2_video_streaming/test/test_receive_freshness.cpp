#include "receive_freshness.hpp"
#include <cstdlib>
#include <iostream>

static void require(bool c, const char * message)
{
  if (!c) {std::cerr << message << '\n'; std::exit(1);}
}
int main()
{
  ReceiveFreshness gate;
  require(gate.permits(0, 1000000), "standalone legacy receiver was blocked");
  gate.set({true, 0, 0, 20000000});
  require(!gate.permits(1000000, 1010000), "bootstrap/drain let media through");
  // Base monotonic epoch is a million seconds ahead of the rover's. The
  // probe's causal offset bound, not NTP/wall clock, determines frame age.
  gate.set({false, 1000000000000LL, 1000001000000ULL, 1000016000000ULL});
  require(
    gate.permits(1000000, 1000001030000ULL),
    "fresh packet with different clock epochs failed");
  require(!gate.permits(1000000, 1000003000000ULL), "two-second-old video was admitted");
  require(!gate.permits(1000000, 1000017000000ULL), "expired causal clock bound was used");
  require(!gate.permits(0, 1000001030000ULL), "missing sender timestamp bypassed freshness");
  require(
    !gate.permits(4000000, 1000001030000ULL),
    "future/impossible timestamp bypassed freshness");
  gate.set({false, -9000000, 1000000, 2000000});
  require(gate.permits(10000000, 1010000), "negative base/rover clock offset failed");
  gate.set({true, 0, 0, 0});
  require(!gate.permits(10000000, 1010000), "drain did not immediately close the gate");
  std::cout << "packet freshness, clock-epoch independence and fail-closed policy passed\n";
}
