# Video latency implementation and live validation — 2026-10-30

The latency changes are installed on rover and base. Normal startup commands
displayed all five connected physical cameras. Long testing was stopped at the
user's request after approximately **12 min 40 s** of sampled observation.
The complete run is **not accepted as uninterrupted five-camera operation**:
a USB hub disconnected, and the Left camera subsequently disconnected and
re-enumerated repeatedly. Four other feeds recovered. No further long run was
started after the stop request.

## Same-frame before/after measurement

Measurements match source output and browser canvas drawing using stream ID,
SSRC, RTP timestamp and marker sequence. Both runs use the same cameras,
configured budgets and normal commands. Each comparison selects a 100-second
stable interval, 70–170 seconds after the first browser frame, before operator
actions, stress fixtures, forced browser fallback or the USB disconnect.
USB streams are 640×360/30 FPS and RGB-D is 640×480/30 FPS in these intervals.

| Measurement | Before P50 / P95 | Improved P50 / P95 |
| --- | --- | --- |
| GStreamer source output → browser canvas | 76.06 / 102.07 ms | **27.02 / 44.08 ms** |
| Decoder call → decoded frame output | 34.80 / 55.00 ms | **1.40 / 3.20 ms** |
| Source output → RTP payloader | 6.22 / 13.62 ms | 6.35 / 9.20 ms |
| Hardware encoder residence | 3.50 / 10.75 ms | 3.46 / 5.17 ms |

Total P50 fell **64.5%** and P95 **56.8%**. These are directly matched total
measurements, not sums of component percentiles. There were 14,962 matched
frames before and 14,993 after, out of approximately 15,000 expected frames in
each interval. Improved per-camera rates were 29.97–30.00 FPS. Median sender
residence did not improve in this scene; its tail and encoder tail improved.

This boundary excludes sensor exposure/readout, USB time before source output,
and physical monitor scanout. It is different from the dashboard's Rover →
Base packet-delay statistic. The browser is base Chromium 147 headless with
GPU disabled; a laptop's browser/GPU can produce different results. The same
decoder settings are delivered to laptop browsers when the dashboard reloads.

The observed maximum browser render gap during the first minute of startup
and adaptation was 1.03–2.31 s across cameras before, and 0.12–0.31 s after.
This is an observed startup comparison, not a guaranteed bound for every
resolution transition or USB failure.

Recomputable matched samples are in `same-frame-samples.csv.gz`; detailed
per-camera statistics and time windows are in `comparison.json`.

## Implemented changes

- Browser low-delay decoding, bounded retention of the first IDR and its
  dependent frames during asynchronous configuration, and explicit recovery
  when decoder backlog exceeds the limit. Worker/opaque canvas rendering has
  a main-thread fallback. Display resizing no longer tears down the decoder.
- Persistent V4L2 capture across bitrate/resolution/FPS adaptation. Only the
  encoder branch is rebuilt when necessary. Raw input and complete encoded
  access units use bounded queues; stale raw images are dropped before encode.
  Hardware VBV is retained for safe live bitrate changes, with branch rebuild
  when a new VBV is required. `videorate skip-to-first` prevents a restarted
  branch from temporarily exceeding the requested lower FPS.
- A shared aggregate media pacing clock and bounded per-camera bursts, retaining
  80:20 HIGH/LOW allocation and JSON-prefix admission. A slow camera waits for
  its own pacing eligibility before reserving the shared link. Source age and
  reference-frame dependencies remain bounded; tiny-budget startup is retained.
- Native receiver records carry a complete AU and verified RTP identity on one
  pipe. Optional identity failures emit video immediately. The duplicate RTP
  metadata tee and correlation timeout are removed from the native live path.
- Recovery IDR requests pass over the existing XBee link, coalesced/rate limited.
  GCC uses a 20 ms median extra-queue target and a persistent tail trigger:
  recent 250 ms P95 above 60 ms for 400 ms, with at least 20 packets. A single
  camera's encoder/SSRC change preserves shared congestion evidence.
- Final gateway hardening bounds pending TCP writes by 150 ms as well as bytes,
  reconnects stale video sockets, and resumes on a fresh IDR. Gateway and local
  browser subscribers no longer replay an old IDR after intervening P-frames.

The final gateway backlog/late-subscriber hardening was unit tested and
deployed after the stable comparison. At the user's stop request, a new full
stack launch of that final revision was omitted. The measured steady-state
improvement belongs to the core latency changes; this last hardening is not
assigned a separate measured speedup.

The receiver jitter setting remains 40 ms. Earlier ordered-loopback evidence
does not support claiming a fixed 30 ms saving from lowering it. Exposure,
30 FPS direct V4L2 RGB-D capture, autonomy defaults, field IP defaults, clock
policy and the existing dashboard delay boundary remain unchanged.

## Verification and runtime events

- Gateway: **131/131** tests; native C++ and package checks: **8/8**.
- Dashboard functional tests, typecheck, lint and production build passed.
- **17** actual encode/decode budget scenarios plus a failed-first-camera
  JSON-prefix scenario passed: zero/minimum budgets, abrupt drops/recovery,
  HIGH/LOW changes, missing/reconnected cameras and feedback loss/recovery.
- **6** persistent-capture reconfiguration cases passed, including 32 kbps,
  hardware/software fallback and 30/15/5/3 FPS. RTP timestamp intervals verify
  the output FPS rather than only counting frames.
- Native receiver tests delivered 1 FPS access units immediately, with and
  without native verified metadata. Sender timing tests cover software and
  hardware encoding; stale NVENC startup frames may be intentionally dropped.
- An isolated pseudo-serial XBee test passed keyframe forwarding, malformed
  payload rejection, commands/ACKs, reconnect, budget fallback and lease locks.
- Actual UI actions changed Right HIGH→LOW and disabled/re-enabled Left. Other
  feeds continued rendering; requested settings were restored.
- 761 UI samples were retained; 759 had numeric delay, zero showed `Clock sync`.
  A forced browser reload accounts for transient missing UI fields. All 384
  captured rover clock reports were synchronized and relatively ready. Maximum
  reported absolute system offset was 0.079 ms and error bound 1.671 ms.
- During forced worker-failure/fallback testing, USB re-enumeration interrupted
  the cameras. Main-canvas rendering occurred, but continuous five-feed fallback
  acceptance is **inconclusive**. Historical counters alone were insufficient;
  `fallback-report.json` records this correction.
- Kernel evidence shows a USB hub and its children disconnecting, followed by
  repeated Left-camera reconnects and UVC `-19` failures. The user confirmed
  nobody moved cables and that the symptom occurs occasionally. The exact
  power/cable/hub/driver cause is not established. The capture layer reported
  buffer-pool/poll failures; this is separate from a radio-bandwidth diagnosis.

No Internet link, route, host address, or radio setting was changed. Test-launched
ROS/base/browser processes were stopped. Chrony services remain active.

## Startup commands used

With ROS/NVM environments sourced, base ran the existing wrapper which executes:

```bash
cd ~/mr2-stack/base/gateway
npm start -- --rover-host 192.168.0.57
```

Rover used the ordinary full real launch, including its usual hardware modules:

```bash
cd ~/mr2-stack/rover/ros2_ws
ros2 launch mr2_launch rover_real.launch.py \
  enable_manipulator_module:=false enable_xbee_sim:=false \
  video_base_host:=192.168.0.55
```

The `.0.55/.0.57` arguments are development-network overrides. Field defaults
remain `.1.101/.1.102`; no new startup flags are required for these changes.
The ordinary full launch also logged existing unavailable CAN/GNSS hardware;
this report validates the video path, not vehicle actuation or localization.

Final source hashes match on both hosts (`deployment-hashes.json`). The base
receiver was compiled on x86-64 and the rover binaries on ARM64. Dashboard
assets were published before replacing the HTML index. Temporary detailed raw
captures remain under `/tmp/mr2-video-implementation/` on the respective hosts.
