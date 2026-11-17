# Sender processing latency in Uplink diagnostics

Uplink now correlates source and encoder pad probes with the exact RTP marker
packet identified by `(stream_id, SSRC, RTP timestamp, marker sequence)` and the
existing browser-render sample. Averages or percentiles from different frames
are never added together.

## Boundaries

| Report key | Dashboard row | Start → end |
| --- | --- | --- |
| `source_to_rtp` | Jetson frame → RTP | GStreamer source output → rover packet capture |
| `h264_encode` | H.264 encode (included above) | Encoder sink pad → encoder source pad |
| `total` | RTP → Render | Existing rover packet capture → browser canvas draw |
| `source_to_render` | Jetson frame → Render | GStreamer source output → browser canvas draw |

`h264_encode` measures the frame's residence in the encoder, including internal
waiting. It is already part of `source_to_rtp`; do not add it again. For V4L2,
source output is when the captured USB frame reaches the GStreamer pipeline.
Sensor exposure, camera-internal encoding, earlier USB/kernel delivery, and
physical monitor scanout are outside these boundaries. For ROS image sources,
the boundary is appsrc output, after ROS delivery/normalization.

## Implementation

- `video_sender` replaces `gst-launch-1.0` for V4L2 pipelines, preserving one
  process per camera and existing retry behavior. No video payload is modified.
- Read-only pad probes match running-time PTS (including encoder segment offsets),
  then attach the exact RTP marker identity. HW encoding preserves input PTS.
- At most 256 pending frames per sender. Unix datagrams are nonblocking; a slow
  or absent diagnostics consumer cannot backpressure the video pipeline.
- The rover agent listens only during capture on the abstract Unix socket
  `mr2-video-timing-<uid>`. It retains at most 2000 samples in the existing metadata
  upload, keeping within the gateway's 2 MiB JSON limit. Truncation is reported.
- Encoder elapsed time uses a monotonic clock. Source timestamps use the rover
  wall clock to join tcpdump; samples with wall/monotonic disagreement above 2 ms,
  impossible timestamp ordering, duplicate identities, or missing matches are
  excluded. Existing locked-clock checks still apply throughout the capture.
- Report schema 2 gains additive distributions. Old reports remain readable:
  missing sender timing appears as `N=0` / `--`, never as measured zero latency.
  Each row reports its own sample count; incomplete coverage raises a warning.

## Verification

```bash
source /opt/ros/humble/setup.bash
source rover/ros2_ws/install/setup.bash
python3 -m unittest scripts.latency.test_rtp_latency scripts.latency.test_sender_timing
python3 -m pytest -q rover/ros2_ws/src/mr2_latency_diagnostics/test
python3 scripts/latency/test_video_sender_probes.py
npm --prefix dashboard run test:latency
npm --prefix dashboard run check
```

The probe test sends generated frames through real software and Jetson hardware
encoders without opening a camera. Live acceptance uses the dashboard's normal
Uplink action and verifies nonzero sender sample counts for each selected camera.
Rebuild `mr2_video_streaming` and `mr2_latency_diagnostics`, restart those nodes,
sync the analyzer to base, and rebuild/deploy the dashboard on both hosts.

## Live acceptance — 2026-09-21

Deployed to rover and base. Two USB MJPEG cameras (640×360 capture, 320×180
H.264 output, 30 fps) were measured using a Chromium browser on base over the
development LAN. The video-only test launch held both clocks; no motor nodes ran.

- 219 rendered frames matched across RTP, browser, and sender probes; every
  matched frame had sender timing. 294 sender records collected, 65,652-byte
  metadata upload. No report warnings and 0% measured RTP packet loss.
- Aggregate H.264 residence: p50 **1.787 ms**, p95 **1.945 ms**.
- Source output → RTP: p50 **4.803 ms**, p95 **5.093 ms** (includes encoding).
- RTP → browser render: p50 **73.762 ms**, p95 **87.478 ms**.
- Source output → browser render: p50 **78.594 ms**, p95 **91.989 ms**.
- Evidence on rover: `/home/mr2/uplink-test-20260921/with-encoding/`.
  Base artifacts: `/home/mr2/latency-router-20260918/artifacts/uplink-RFrUY7/`.

Validation: 13 RTP/analyzer tests, 12 rover diagnostics tests, real encoder probe
matching (30/30 frames each for x264 and Jetson), dashboard latency tests,
TypeScript, eslint, and both dashboard profile builds passed. Test processes were
stopped after acceptance. The normal launch needs no extra timing option.

## Base address selection

Real ROS launch clock acquisition now records the acknowledged base peer in the
launch context. The video destination defaults to that peer, so development
(`192.168.0.62`) and field (`192.168.1.101`) operation follow the same base used for
clock synchronization. An explicit `video_base_host:=...` still takes precedence;
without an acknowledged peer, the existing `MR2_BASE_IP` fallback remains.
Rover-direct continues to default to loopback. `scripts/start_chrony.sh` no longer
hardcodes the field address and forwards additional launch arguments.

Verified with 26 launch/profile tests and a real clock-lease launch fixture.
