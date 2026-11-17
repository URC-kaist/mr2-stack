# Sequential adaptive video validation — 2026-10-29

The allocator now funds a usable prefix of each priority class, in the actual
`streams` array order of `rover/ros2_ws/src/mr2_launch/config/video_streams.json`.
Display sorting and the order of UI clicks do not select the winner. HIGH/LOW
retain 80:20 shares, with unused or unusably small shares lent to the other
class. Missing, disabled, and failed capture sources consume no budget.

An enabled camera waiting for budget keeps ON and reports zero encoder bitrate.
The pipeline stops at zero allocation and resumes automatically. Tiny outputs
use x264 where Jetson cannot encode them; current-target VBV, bounded pacing,
one-second GOPs, immediate large cuts, and source-aware recovery prevent the
previous IDR rejection loop. A silent input is distinguished from a producing
encoder whose frames are rejected. V4L2 capture checks run outside the budget
and use bounded subprocess termination, so a faulty first camera cannot prevent
later working cameras from starting.

The controller follows GCC AIMD and probing principles: multiplicative discovery
when capacity is unknown, additive growth near learned capacity, backoff toward
85% of received throughput, and short measured probes for application-limited
video. Probe bytes do not appear in camera traffic, FPS or packet loss. It is
a receiver-side adaptation for the XBee feedback path, not full libwebrtc GoogCC.
Native receiver output now preserves complete H.264 AU boundaries using length
prefixes, avoiding an extra source-frame wait at low FPS.

## Automated evidence

| Check | Result / artifact |
| --- | --- |
| Allocation, pacer and real UDP probes | 3/3 CTests; 100,000 seeded budget/class/cap/presence combinations, `ctest.log` |
| Gateway suite on both hosts | 119/119 each, `gateway-tests-rover.log`, `gateway-tests-base.log` |
| Packet-level bottleneck simulation | 32–8000 kbps, busy/application-limited traffic, 6000→300→5000 kbps, 0/5/15 ms jitter, outage, 2% loss, 20 simulated minutes of 200 ppm clock drift |
| Production node and real encoders/decoder | 17 phases, `node-scenarios.json` |
| Failed first capture source | Valid later camera decodes while the failed prefix receives zero, `failed-first-camera.json` |
| Capture subprocess ignores SIGTERM | Bounded termination and later camera progress, `hung-first-camera.json` |
| Actual bridge through PTY | ON/OFF/NO DEVICE, retries, extended probe feedback, 80:20, resolution change, exclusive trial lock, `xbee-pty.log` |
| Encoder profile checks | 16 hardware cases and 3 tiny-output cases, `hardware-profiles.json`, `ultralow-profiles.json` |
| Current helper RTP/IPC smoke | Real x264/Jetson packet spacing and live rate cuts, `native-smoke.log` |
| Immediate native AU delivery | 1 FPS source, first frame about 44–45 ms after process start, `receiver-framing-*.json` |
| Physical capture | All five connected cameras pass one-frame capture, `physical-camera-capture.json` |

The 17 node phases include 32/0/100/600/900/5400 kbps, abrupt 5400→100 and
recovery, HIGH/LOW changes, first camera disabled/missing/reconnected, rapid
priority changes, all inputs missing/returning, feedback timeout/recovery and
idempotent probe requests. Actual decoded frames and reported allocations are
checked, including restored geometry after input recovery.

For those isolated node scenarios, 4,570 joined capture-ingest→last-paced-UDP-send
samples measured p50 41.089 ms, p95 128.008 ms, maximum 331.693 ms. These are
sender-side timings, not rover-to-browser latency or RF field measurements.

## Real base/rover and browser

The regular rover launch was used with only `video_base_host:=192.168.0.55`.
The base used its normal gateway with per-run development IP overrides
(`--rover-host 192.168.0.57 --base-rocket 192.168.0.244 --rover-rocket 192.168.0.245
--drone-rocket ''`). Real XBee commands and feedback were used. Chromium opened
the deployed dashboard; tests counted actual decoded `VideoFrame` draws rather
than inferring playback from state messages.

`live-browser.json`, `live-browser.log` and the PNGs record these phases:

| Scenario | Expected funded cameras |
| --- | --- |
| Normal, then recovered from a low budget | All five |
| 32 kbps, all LOW | RGB-D only, 80×60 at 3 FPS |
| 600 kbps, all LOW | RGB-D and Front Left |
| 600 kbps, Front Right selected HIGH before Front Left | Front Left gets HIGH's 480 kbps; RGB-D gets LOW's 120 kbps |
| 1500 kbps, Front Left HIGH | Front Left 1200 kbps; RGB-D 300 kbps |
| RGB-D disabled / reenabled | Other four continue; RGB-D returns after enable |

The normal test uses RGB-D 640×480 and four USB outputs at 640×360. RGB-D,
Front Left, Front Right and Right render about 30 FPS. Left varies around
22–25 FPS on this physical source despite the requested 30 FPS capture profile.
Camera input FPS remains an upper bound on output. No forced ROS/6 FPS RGB-D
override was reintroduced.

The runner restores an uncapped gateway and original connected-camera priorities
(Front Left/Front Right HIGH, RGB-D/Left/Right LOW), with all five enabled.
`deployment.json` records the final code hashes, camera state and development
arguments. The live runner and restart helper are included for reproducibility;
they restart the gateway and alter real camera controls, so run them only on
an authorized, idle test setup. Place `restart_gateway.py` at
`/tmp/mr2-adaptive-restart-gateway.py` before running `live-base.cjs` on the base.
The runner requires the base's installed Node/ws and Chromium.

## Deployment and scope

Both architectures' native video helpers were rebuilt and the changed gateway
files were applied to the real base over SSH. Source and installed stream JSONs
match. Field IP defaults remain base 192.168.1.101, rover 192.168.1.102, base
Rocket 192.168.1.100, rover Rocket 192.168.1.106, drone 192.168.1.103. Normal
startup commands and autonomous-module defaults remain as previously configured.

This validates the current development LAN, real capture/encode/XBee/browser
path, bounded sender latency and simulated network bottlenecks. It does not
establish Rocket-only RF congestion/interference performance or a field
rover-to-display latency bound. The ordinary bench launch also reports missing
GNSS and CAN activation failures; vehicle hardware operation was outside these
video tests, and no motion commands were sent.

Reproduction commands for isolated tests are in `docs/video-pipeline.md`.
