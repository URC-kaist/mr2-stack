# Weak-link freshness validation — 2026-10-30

This change targets delayed playback when a distant rover's radio capacity
collapses. It combines retained delay evidence, faster budget/profile cuts,
an independent packet freshness gate, and a controlled media pause/resume.
See [the design](../../weak-link-latency-control.md) for boundaries, profile
thresholds and operating limits.

## Packet-level model

The earlier FIFO fixture capped waiting time at 150 ms and could not reproduce
deep radio queues. The new fixture services a byte FIFO at its **current**
capacity and can retain a full MiB. Feedback uses a separate 40 ms path. It
models distinct monotonic epochs, 200 ppm relative drift, five milliseconds of
arrival jitter, and media plus probe traffic sharing the bottleneck.

- Nine 90-second logical cases: a running 8000 kbps sender faces 32, 100 or
  300 kbps capacity for 30 seconds, then 8000 kbps returns; each case runs with
  0%, 2% or 5% random packet loss.
- Three cold-start cases: 32, 100 or 1000 kbps from the first packet, with 1%
  random loss and no clean transit baseline established beforehand.
- One control case without the freshness guard reproduces **14,756 ms** old
  media being admitted for playback.

All 13 cases pass. The nine guarded collapse/recovery cases admitted no packet
older than **454.8 ms** despite some received backlog being approximately
30 seconds old. First fresh packet arrival after capacity returns was
0–1.652 seconds, and all resumed media without a process restart. Thirty
seconds after recovery the budgets were 2084–8620 kbps, with further headroom
discovery possible. Cold 32 kbps operation stayed at a 32 kbps budget and
admitted 132 media packets in 45 logical seconds.

These are **packet-model results**, not measured physical camera-to-screen
latency or a guarantee of continuous video during the weak interval. The
`first_fresh_in_weak_phase_s` field can count an already in-flight fresh packet
near the capacity boundary; it must not be interpreted as proof of continuous
weak-link playback. The guard intentionally rejects stale backlog and may
pause while the radio drains it. Raw summary: `deep-queue-report.json`.

The other model scenarios cover busy/application-limited links from 32 to
8000 kbps, isolated/persistent queue tails, sudden loss and recovery, 0–15 ms
jitter, a full outage, probe accounting, and a long clock-drift trace.

## Actual codecs and production receiver/controller

- `test_receive_freshness.py` passes real H.264 through a user-space UDP relay
  that retains packets for **1.2 seconds**. The control run decodes delayed
  frames; the guarded run observes those old packets for GCC but discards
  them before decoding, admits current frames after the relay clears, and
  stops output after an explicit pause. Evidence:
  `receive-freshness-live.log`.
- `test_freshness_base.py` connects the production JavaScript controller and
  native receiver, with a simulated XBee command/reply and real H.264. It
  exercises blocked startup, a session-bound causal probe, a later 1.2-second
  delayed path, pause and fresh recovery. Both ARM64 rover and x86-64 base
  runs passed. This test is distinct from physical XBee validation.
- Eight persistent-capture phases pass: 600→1800→300→100→32→0→32→1800 kbps.
  Zero budget keeps the capture worker alive and emits no media. Output RTP
  timestamp steps verify 30/15/5/3 FPS. Synthetic branch rebuilds took
  8–53 ms; these are not measured USB-camera restart times.
- Seventeen real encode/decode budget scenarios pass, including minimum and
  zero budgets, abrupt drops/recovery, class changes, missing/reconnected
  sources and feedback timeout to zero. The separate cumulative-cut case
  verifies 5400→4320→3456 kbps: the second 20% cut crosses 30% relative to the
  applied profile and produces the downgraded decoded image in
  **249/319/336 ms** across three synthetic cameras.
- The actual ROS C++ XBee bridge/video node also pass a pseudo-terminal
  integration test covering commands, ACKs, keyframes, reconnect and timeout
  pause/recovery. No physical serial port is opened by that fixture.

## Build, installation and regression checks

- Rover gateway: **153/153** tests pass. Base gateway: **152/152** tests pass
  in its existing checkout.
- Rover native C++ and package checks: **9/9** pass, including pacing,
  freshness, correlation, probe tokens and lint checks.
- Dashboard functional tests, typecheck, lint and production build pass.
- ARM64 sender/node/receiver are rebuilt and installed on rover. Both native
  helper binaries are compiled on x86-64 and installed on base. Dashboard
  assets are published before replacing its HTML index.
- Scoped pre-deployment backups on base are under
  `/tmp/mr2-weak-link-deploy/`. Temporary detailed test data are under
  `/tmp/mr2-weak-link/` on the respective hosts.

No field IP defaults, host interface settings, routes, radio settings or
Internet connectivity are changed. Normal startup arguments remain valid.

## Physical link check

The actual serial aliases are `/dev/ttyXBEE`: rover points to `ttyUSB0`, base
to `ttyUSB1`. Both applications already default to this alias. The initial
claim that XBee was absent came from checking only the incorrect `/dev/XBEE`
name; that diagnosis was incorrect and was corrected before physical testing.

The normal launch commands were then run for a 120-second browser observation
with physical XBee feedback and USB cameras, using only development address
overrides:

```bash
# Base, with the installed ROS/Node environments sourced:
cd ~/mr2-stack/base/gateway
npm start -- --rover-host 192.168.0.57

# Rover:
cd ~/mr2-stack/rover/ros2_ws
ros2 launch mr2_launch rover_real.launch.py \
  enable_manipulator_module:=false enable_xbee_sim:=false \
  video_base_host:=192.168.0.55
```

Front Left, Front Right, Right and RGBD all reached their full 640-pixel-wide
30 FPS profiles. Actual render rates over the final 20 seconds were
28.9/29.1/28.9/29.9 FPS. The aggregate final budget was 7200 kbps, matching
the four functioning cameras' configured caps. All 120 UI samples displayed
numeric delay; none displayed `Clock sync`.

In the stable interval, 7720 directly matched source-output/browser-render
frames gave **28.7 ms P50 / 45.4 ms P95**. This boundary excludes sensor
exposure, USB delivery before source output and physical screen scanout. It
is not a new before/after speedup comparison and was measured before the
final pause-watchdog hardening. The summary and recomputable matches are in
`physical-summary.json` and `physical-matched-samples.csv.gz`.

**Five-camera acceptance failed on Left**, which produced no frames. Its
capture checks failed while the kernel repeatedly disconnected USB path
`1-2.4.2` and reported UVC `-19` errors. That path is the actual
`/dev/videoLEFT` device. This agrees with the earlier recurrent USB problem;
it is not evidence of radio congestion. Neither the hub nor interface was
reset. `left-usb-kernel.log` records this limitation. Unavailable CAN/GNSS
modules in the ordinary launch were also logged; actuation/localization were
outside this video validation.

The pause/resume watchdog is hardened to start a new encoder-progress grace
period after a deliberate zero-budget pause. A separate short physical-camera
test exercises a 6.5-second pause, including a 700 ms scheduling stall on
resume, and checks that the same capture worker remains open and decoded video
returns. The final test retained the same worker throughout and decoded 64
frames in the three-second resume phase; its first frame arrived 968 ms after
resumption, including the deliberately injected 700 ms scheduling stall.
`physical-pause-resume.json` records the result. This is a targeted final-code
check rather than a new long run.

Final scoped source/configuration and built-asset hashes match across hosts
for 89 files. The base's published HTML matches the deployed dashboard build;
see `deployment-hashes.json`. Native binaries have separate ARM64/x86-64
hashes because they are built for their respective machines.

All test-launched base, rover and browser processes are stopped afterward.
A large-distance RF attenuation test has not been conducted; the controlled
deep queues and 1.2-second relay delay tests above provide the collapse-case
evidence without changing the working network.
