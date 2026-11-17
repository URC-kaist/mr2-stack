# MR2 Video Pipeline

This document describes the current rover-to-base-to-browser video path in the
`mr2-stack` repository.

## Overview

The video system is intentionally split into three stages:

1. rover-side ROS 2 + GStreamer encode
2. base-side GStreamer receive + Node.js gateway
3. browser-side WebSocket + WebCodecs decode

The design goal is low latency and freshness. The pipeline prefers dropping old
frames over accumulating delay.

## Data Flow

### 1. Central stream configuration

The authoritative stream mapping lives in:

- `rover/ros2_ws/src/mr2_launch/config/video_streams.json`

Each stream entry defines:

- `stream_id`
- `udp_port`
- `source`
- optional `width`
- optional `height`
- optional `framerate`
- encoder settings
- display metadata

Preferred source forms:

```json
{
  "stream_id": "front_nav_cam",
  "source": {
    "type": "ros_topic",
    "ros_topic": "/front_camera/image_raw",
    "ros_encoding": "rgb8"
  },
  "udp_port": 5000
}
```

```json
{
  "stream_id": "front_usb_cam",
  "source": {
    "type": "v4l2",
    "device": "/dev/video0",
    "pixel_format": "YUY2"
  },
  "udp_port": 5002,
  "width": 1280,
  "height": 720,
  "framerate": 30
}
```

Configuration note:

- every stream must declare an explicit `source` object
- top-level `ros_topic` / `ros_encoding` entries are not supported
- set `encoder.type` to `nvv4l2h264enc` for Jetson hardware H.264 encoding,
  or `x264` for CPU encoding
- omit `width` and `height` on ROS topic streams that should preserve the
  incoming camera frame size

The intended mapping rule is:

`1 ROS image topic = 1 H.264 stream = 1 UDP port = 1 stream ID`

### 2. Rover-side pipeline

The rover publisher is implemented in:

- `rover/ros2_ws/src/mr2_video_streaming/src/video_streaming_node.cpp`

Per configured stream, the node does one of two things:

- `source.type = ros_topic`
  - subscribes to `sensor_msgs/msg/Image`
  - accepts only `rgb8` and `bgr8`
  - normalizes frames to internal RGB
  - lazily creates one GStreamer pipeline on first valid frame
  - pushes frames into `appsrc`
- `source.type = v4l2`
  - starts a direct `v4l2src` GStreamer pipeline at node startup
  - optionally constrains the device by width, height, framerate, and pixel format
  - uses `nvv4l2decoder mjpeg=true` for Jetson or `jpegdec` for x264 when input is MJPG/JPEG

For both source types, the rover node:

- timestamps encoded frames for low-latency live streaming
- keeps queue depth bounded so stale frames are dropped

Conceptual rover pipeline:

```text
appsrc
  ! queue leaky=downstream max-size-buffers=1
  ! videoconvert
  ! videoscale
  ! video/x-raw,format=I420
  ! [x264enc ...] or [nvvidconv ! video/x-raw(memory:NVMM),format=NV12 ! nvv4l2h264enc ...]
  ! h264parse config-interval=1
  ! rtph264pay pt=96 mtu=1176 config-interval=1
  ! udpsink host=<base_host> port=<udp_port> sync=false async=false
```

Conceptual direct V4L2 pipeline:

```text
v4l2src device=<device> do-timestamp=true
  ! queue leaky=downstream max-size-buffers=1
  ! [image/jpeg ... ! jpegdec] or [video/x-raw ...]
  ! videorate
  ! videoscale
  ! videoconvert
  ! video/x-raw,format=I420[,width=...][,height=...][,framerate=.../1]
  ! [x264enc ...] or [nvvidconv ! video/x-raw(memory:NVMM),format=NV12 ! nvv4l2h264enc ...]
  ! h264parse config-interval=1
  ! rtph264pay pt=96 mtu=1176 config-interval=1
  ! udpsink host=<base_host> port=<udp_port> sync=false async=false
```

Current implementation details:

- `x264enc`
- `bframes=0`
- `byte-stream=true`
- `threads=1`
- periodic SPS/PPS insertion via `h264parse` and `rtph264pay`
- keyframe cadence driven by `key-int-max`

### 3. Base-side receive pipeline

The base receiver is implemented in:

- `base/gateway/src/video/receiver.js`

Node.js starts one native `video_receiver` child per configured stream. Each child:

- listens on exactly one UDP port
- receives RTP/H.264
- applies a small jitter stage
- depayloads RTP
- reparses H.264
- outputs complete Annex B access units with a four-byte big-endian length prefix

Conceptual receive pipeline:

```text
udpsrc port=<udp_port> caps="application/x-rtp,media=video,encoding-name=H264,payload=96,clock-rate=90000"
  ! rtpjitterbuffer latency=<ms> drop-on-latency=true
  ! rtph264depay
  ! h264parse disable-passthrough=true
  ! video/x-h264,stream-format=byte-stream,alignment=au
  ! appsink name=video_output sync=false async=false
```

Important boundary:

- Node.js receives **length-prefixed, depayloaded H.264 Annex B access units** on stdout
- a separate pipe carries post-jitter RTP for frame correlation
- another pipe carries pre-jitter packet observations for congestion control
- native AU lengths survive arbitrary pipe chunk boundaries; a frame is emitted
  immediately without waiting for the next source frame, including at 1–3 FPS
- the optional gst-launch fallback retains its unframed Annex B parser

### 4. Node.js H.264 handling

The base video gateway lives in:

- `base/gateway/src/video/service.js`
- `base/gateway/src/video/h264.js`
- `base/gateway/src/video/protocol.js`

Per stream, the gateway maintains:

- latest SPS
- latest PPS
- current codec string
- latest decodable key access unit
- stream availability
- per-client bootstrap state

The Annex B parser performs:

- start-code scanning
- NAL extraction
- SPS/PPS detection
- IDR detection
- access-unit grouping
- codec string derivation from SPS

The gateway only sends one browser message per access unit.

### 5. Browser-side decode

The browser-side implementation lives in:

- `dashboard/src/lib/videoGateway.ts`
- `dashboard/src/lib/videoProtocol.ts`
- `dashboard/src/components/VideoStreamCard.tsx`

The browser:

- fetches stream metadata from `/video/streams`
- subscribes to one or more `stream_id`s on `/video-ws`
- receives `config` and `chunk` binary messages
- configures a `VideoDecoder`
- converts Annex B payloads to AVCC when required
- decodes with WebCodecs
- renders frames to `<canvas>`

The dashboard resolves `/video-ws` and `/video/streams` relative to the
current browser origin unless explicit environment overrides are set.

`/video/streams` exposes the configured source metadata per stream, including:

- `source_type`
- `ros_topic`
- `ros_encoding`
- `v4l2_device`
- `v4l2_pixel_format`

## Browser Caveats

### Safari raw-IP WebSocket instability

Observed during local testing:

- Firefox handled the temporary video viewer reliably, including gateway restart
  and reconnect behavior.
- Recent Safari builds were intermittently flaky when the viewer page and
  `/video-ws` were accessed over a raw IP address such as
  `http://100.x.y.z:18081` and `ws://100.x.y.z:18081/video-ws`.
- The failure mode looked like a first-load browser issue: the page could appear
  stuck even though the same stack worked in Firefox, and a refresh or a second
  navigation could unstick it.

Current interpretation:

- treat this as a Safari/browser-path issue before treating it as a rover/base
  video pipeline defect
- do not rely on Safari Web Inspector alone to prove that a subscribe message
  was or was not sent

Recommended workaround when testing with Safari:

- use a hostname instead of a raw IP address if possible
- prefer `https` + `wss` over plain `http` + `ws` when available

## WebSocket Protocol

The video socket is separate from the control socket:

- control: `/xbee-ws`
- video: `/video-ws`

### Client subscription message

Clients subscribe with JSON:

```json
{"type":"subscribe","stream_id":"front_nav_cam"}
```

Clients may unsubscribe with:

```json
{"type":"unsubscribe","stream_id":"front_nav_cam"}
```

### Binary message types

Binary framing is implemented in:

- `base/gateway/src/video/protocol.js`
- `dashboard/src/lib/videoProtocol.ts`

#### `config`

Sent before normal chunk delivery when a client first joins or needs decoder
re-bootstrap.

Fields:

- `type = 1`
- `stream_id`
- `codec`
- `sps`
- `pps`
- `width`
- `height`

#### `chunk`

One message per encoded access unit.

Fields:

- `type = 2`
- `stream_id`
- `flags`
- `timestamp_us`
- `payload`

`timestamp_us` is the base gateway's epoch timestamp when the Annex-B access
unit emerges from the receive child after RTP jitter buffering, depayloading,
and H.264 parsing. It is not the rover capture timestamp or raw base-NIC packet
arrival time. Browser diagnostics call this observation T7a and use it only for
the base-to-browser boundary; they do not use it to estimate Rocket M2 latency.

Rocket M2 link latency is measured without changing this wire field. The tools
under `scripts/latency/` capture the existing RTP packets on the rover and base
Rocket-facing interfaces and match `(UDP port, SSRC, sequence, RTP timestamp)`.
See `scripts/latency/README.md` for the clock-offset contract and field
procedure.

Current flags:

- bit 0: key
- bit 1: delta

## Bootstrap Rules

A client does not start mid-stream on arbitrary delta units.

The gateway waits until it has:

- valid SPS
- valid PPS
- a decodable key access unit

Then it sends:

1. `config`
2. first key `chunk`
3. subsequent delta/key chunks

If codec state changes, the gateway bumps a per-stream config version and forces
subscribers back through the same bootstrap path.

## Low-Latency Behavior

The current implementation explicitly favors freshness:

- rover `appsrc` uses bounded buffering
- rover queue is `leaky=downstream`
- base receive pipeline uses a bounded jitter buffer
- per-client websocket sending checks `bufferedAmount`
- slow clients are marked for re-bootstrap rather than stalling ingest
- old keyframes may be replaced by newer ones in cache

Acceptable outcomes:

- dropped frames
- dropped deltas
- short visual gaps after loss
- waiting for next keyframe after reconnect

Unacceptable outcome:

- unbounded latency growth

## Current File Map

### Rover

- `rover/ros2_ws/src/mr2_video_streaming/src/video_streaming_node.cpp`
- `rover/ros2_ws/src/mr2_launch/config/video_streams.json`
- `rover/ros2_ws/src/mr2_launch/launch/video_streaming.launch.py`

### Base gateway

- `base/gateway/src/video/stream_config.js`
- `base/gateway/src/video/receiver.js`
- `base/gateway/src/video/h264.js`
- `base/gateway/src/video/protocol.js`
- `base/gateway/src/video/service.js`
- `base/gateway/src/runtime/ws_route_registry.js`
- `base/gateway/src/runtime/http_handlers.js`

### Dashboard

- `dashboard/src/lib/videoGateway.ts`
- `dashboard/src/lib/videoProtocol.ts`
- `dashboard/src/hooks/useVideoStreams.ts`
- `dashboard/src/components/ConfiguredVideoGrid.tsx`
- `dashboard/src/components/VideoStreamCard.tsx`

## Local End-to-End Runner

For local browser verification, the repository also includes:

- `scripts/tmp_run_video_e2e.sh`

That temporary runner:

- starts a fake local XBEE link
- starts the base gateway
- starts the rover video node
- publishes a synthetic ROS image stream
- serves a lightweight browser viewer
- proxies `/video/streams` and `/video-ws` through one browser-facing port

This script is for operator testing only. It is not part of the production
runtime path.

## Adaptive rover video and XBee control

The implementation plan is: (1) binary XBee selection and confirmed state,
(2) packet-level pre-jitter observations, (3) one receiver-side delay controller
for the Rocket link, (4) rover 80:20 allocation and live bitrate IPC, (5) slow
resolution/FPS transitions, and (6) isolated protocol and real encoder tests.

The Live Feed controls now use `/xbee-ws` -> base serial -> rover XBee bridge ->
local `/video/control`. They no longer require rosbridge over Rocket M2.
The rover publishes `/video/state` back through XBee. The three feed states are
`ON`, `OFF`, and `NO DEVICE`; requested enable is retained separately, including
while a camera is absent. There is no video-specific stale-information state.
A command timeout/rejection is an operation error, not another feed state.
Existing local SetBool services remain available. Diagnostics leases reject
selection/priority changes and freeze automatic quality changes until release.

Live Feed headers show decoded resolution and measured playback FPS, for example
`640×360 · 29.8 FPS`, in the same 0.75rem font size as Enable/Disable. FPS counts
frames actually drawn by that browser over the last second; it is not the
encoder's configured FPS cap. With no displayed frames, OFF, or NO DEVICE, the
header shows `— · 0 FPS`. The three XBee feed states and button behavior remain
available internally for control and budget allocation.

Each stream may specify `priority: "high" | "low"`. The defaults put front left
and front right in high priority, or flat camera 1 for the flat configuration.
Operators can change either priority from Live Feed. Preferences last for the
rover node lifetime; node restart restores the JSON defaults.

The rover allocates the aggregate wire budget 80% to the high group and 20% to
the low group, capped by the configured `encoder.bitrate_kbps` per feed.
Within a group, the `streams` array order in
`rover/ros2_ws/src/mr2_launch/config/video_streams.json` determines startup order;
display order and the order of UI clicks do not change it. A small pool funds
the first usable feed, then a prefix grows as the pool can provide roughly
300 kbps per feed. Selected feeds share the remaining pool equally, respecting
their caps. A class share below the useful minimum (100 kbps) is lent to an
already usable class; with a total below 100 kbps the first eligible feed gets
the whole total. Empty/saturated groups also lend unused shares. OFF, missing,
and failed cameras consume no budget. Enabled feeds waiting for an allocation
retain the existing ON state and report zero encoder bitrate. The encoder target is 85% of its allocated
wire rate, leaving room for RTP/UDP overhead and bursts. A single shared link
controller avoids independent cameras competing to increase their rates.

`video_sender` accepts nonblocking local datagram updates for encoder bitrate
and pacing rate. The parent chooses output profiles from the unchanged capture
size, with even dimensions and FPS never exceeding capture FPS:

| Encoder target (kbps) | Capture-size scale | Maximum FPS |
| --- | --- | --- |
| >=1200 | 100% | 30 |
| >=650 | 75% | 30 |
| >=300 | 50% | 30 |
| >=180 | 50% | 15 |
| >=100 | 37.5% | 10 |
| >=60 | 25% | 5 |
| lower | 12.5% | 3 |

These are target/maximum FPS values. The dashboard shows actual rendered FPS,
which can be lower when camera input slows, packets are lost, or downstream
processing drops frames. UVC `exposure_dynamic_framerate` can permit automatic
exposure to lower physical capture rate even when caps request 30 FPS.
The [640×360 / 8 FPS investigation](validation/usb-fps-2026-10-29/README.md)
reproduced approximately 7.67 FPS before encoding on Front Right and Right;
temporarily disabling that camera control restored approximately 30 FPS.

`video_sender` now disables `V4L2_CID_EXPOSURE_AUTO_PRIORITY`
(`exposure_dynamic_framerate=0`) on supported V4L2 cameras before format
negotiation. It uses the capture device's own file descriptor on each open,
including one-frame capture checks and restarts after USB reconnection.
Automatic exposure mode is unchanged. Unsupported controls are skipped;
failures are logged without preventing capture. This removes exposure-driven
capture-rate reduction; it does not override congestion-control output FPS.
In dark scenes, the shorter available exposure can reduce image brightness.

A normal decrease must persist 1 second, an increase 5 seconds, and profile changes are at least 3
seconds apart. Resolution/FPS changes restart only that camera's pipeline;
bitrate changes apply while PLAYING. A cut greater than 30% flushes that
camera's encoder/pacer immediately. A zero allocation stops its pipeline;
restoring its allocation starts it again. New SPS/PPS and IDR bootstrap the existing
browser decoder path. For V4L2 cameras, videorate runs before decoding/conversion
in the native JPEG/raw domain, followed by an explicit output-FPS cap. This
avoids RealSense YUY2 -> NVMM negotiation failures when lowering FPS. Jetson's
VBV is sized from the current encoder target and source frame period (100 ms
minimum); a live increase beyond that buffer's capacity restarts the encoder.
Very small outputs (width below 128) use x264 instead of starting an unsupported
Jetson encoder size. The GOP is capped at one second at the selected FPS,
avoiding ten-second keyframe waits at 3 FPS. Per-feed progress checks distinguish
missing input from repeated local frame rejection: only a producing encoder
with no admitted frames triggers a smaller recovery profile. Higher budgets
and periodic stable retries restore quality. No ROS-only geometry cap is used.
V4L2 cameras first pass a one-frame capture check without using network budget;
failed checks retry with backoff while healthy JSON successors keep streaming.
Child shutdown is bounded, including a stuck capture process.

A per-camera pacer limits packet bursts. Delta-frame completion has a 150 ms
floor, one source period plus 20% margin, and a 300 ms cap. IDRs get 300 ms to
avoid an unrecoverable startup deadlock. Thus 6 FPS uses approximately 200/300 ms rather than
applying the 30 FPS frame-size constraint. Packet pacing still uses the assigned
wire bitrate. Frames exceeding their window are dropped before RTP sequence
assignment; dependent deltas are skipped until a keyframe. Keyframe recovery requests are limited to
once per second. Capture queues remain leaky. IPC/observation consumers cannot
block video. Sender packet timestamps are taken after pacing immediately before
`udpsink`; a private RFC 8285 two-byte extension (ID 7) carries 64-bit big-endian
monotonic and epoch microseconds. `rtph264pay mtu=1176` reserves 24 bytes so the
full RTP packet remains <=1200 bytes. Monotonic time drives control; epoch time
is available to diagnostics and requires valid clock synchronization to mean
absolute one-way delay.

The base uses the new `video_receiver` helper by default. Its udpsrc source-pad
probe records arrival before jitter buffering, using the actual probe timestamp
rather than the later pipe-read time. Nonblocking fd=4 emits 52-byte big-endian
records: arrival monotonic/epoch (u64 each), send monotonic/epoch (u64 each),
SSRC and RTP timestamp (u32 each), RTP sequence and IPv4 wire bytes (u16 each),
magic 0x4d523201 (u32), and cumulative local observation drops (u32).
Local observation gaps reset loss tracking so IPC overload is not classified
as radio loss. Native stdout uses a four-byte big-endian AU byte count followed
by that complete Annex B AU. The helper's `video_output` appsink preserves
GStreamer's AU boundaries; arbitrary pipe chunks cannot add a one-frame wait.
Post-jitter RTP correlation remains on fd=3. Legacy pipelines without this
appsink still produce their original sink output.

`congestion.js` is a receiver-side GCC-inspired delay controller, **not the full
libwebrtc GoogCC**. It retains packet send/arrival/size/sequence observations,
forms 5 ms send groups, smooths accumulated inter-arrival delay, and fits a
60-group trendline over at least 300 ms. A jitter-aware threshold and 150 ms
confirmation reject isolated scheduling spikes. AIMD combines sustained positive
trends and observed loss
with actual receive rate; sequence wrap, duplicates, and 100 ms reordering are
handled. Sender clock offsets cancel in inter-arrival differences. It starts
at 3000 kbps (capped by configured total), with a 32 kbps lower bound. Unknown
capacity uses multiplicative discovery (approximately 8% per second); near a
learned capacity it switches to additive increase. Growth is throughput-bounded,
and congestion reduces the target toward 85% of measured throughput rather
than repeatedly cutting an already low target. A relative transit-delay floor
per packet-size bucket also bounds slowly accumulating queues. These floors
tolerate clock drift and reset during source reconfiguration and major cuts.
Packet observations from different receiver pipes are sorted by their recorded
arrival timestamps before estimation. Selection/profile/SSRC transitions reset
the detector and get 1.5 seconds of encoder warmup. Silence alone does not
halve the budget. Short, paced 100 ms probe clusters can verify spare capacity
even when video traffic is application-limited. Discovery probes normally run
2 seconds apart, slowing to 5 seconds near capacity or after a failed probe.
They use RTP PT 127 and SSRC `0x4d523250` on the first configured video port;
no extra UDP port is needed. The receiver measures then drops them before
jitter buffering/H.264 decoding. Probe bytes never count as camera traffic,
camera FPS, or camera packet loss. Estimates require delivered probe packets,
plausible send/receive spans, low loss, and no excessive queue growth; merely
scheduling a probe cannot increase the estimate. All-OFF and configured caps
stop unnecessary probing. No image frames are synthesized to fill the link.

This adapts the distinction between multiplicative discovery, additive growth
near capacity, throughput backoff, and active probing from the primary
[WebRTC AIMD implementation](https://webrtc.googlesource.com/src/+/main/modules/remote_bitrate_estimator/aimd_rate_control.cc)
and [probe controller](https://webrtc.googlesource.com/src/+/main/modules/congestion_controller/goog_cc/probe_controller.cc).
It remains a receiver-side adaptation for the XBee feedback cadence, not the
full libwebrtc transport, loss estimator, or retransmission/FEC stack.

`--video-initial-kbps` / `VIDEO_INITIAL_KBPS` configure the starting budget
(default 3000). `--video-max-kbps` / `VIDEO_MAX_KBPS` set an optional wire-rate
ceiling; zero/unset derives the ceiling from the configured streams. The actual
controller may choose any rate at or below that ceiling.

Feedback goes over XBee at 5 Hz; no per-packet acknowledgements go over XBee.
The normal wire report contains budget, receive rate, delay trend and loss;
while probing, eight optional bytes specify cluster id, target and duration.
Both 22-byte legacy and 30-byte extended feedback are accepted. The rover
stops increasing and falls back to <=500 kbps after 2 seconds without previously
established feedback. This internal control safeguard adds no UI feed state.
If all feeds are OFF there is no congestion feedback traffic. Status is emitted
per feed at 1 Hz and immediately after commands. Small video writes are skipped
when the base serial writable queue is busy, preserving control traffic.

XBee video payloads are little-endian and retain existing framing/CRC16:

| ID | Payload |
| --- | --- |
| 0x40 SET (12 bytes) | session u32, request u32, UDP port u16, enabled u8, high-priority u8 |
| 0x41 FEEDBACK (22 bytes) | session u32, sequence u32, wire-budget kbps u32, receive kbps u32, delay-trend microseconds/second i32, loss permille u16 |
| 0x41 extended FEEDBACK (30 bytes) | normal FEEDBACK + probe id u16, total probe target kbps u32, duration ms u16 |
| 0x42 STATE (26 bytes) | boot u32, acknowledged session u32, request u32, port u16, state u8 (OFF=0/ON=1/NO DEVICE=2), flags u8 (bit0 high, bit1 requested enable), encoder kbps u32, width u16, height u16, FPS u8, command result u8 (0 success/1 trial lock/2 rejected) |

UDP ports identify configured feeds on both hosts. Session/request serial
arithmetic rejects older commands. Retries resend the same requested state;
duplicate requests do not toggle or reapply. Rejections remain in periodic
reports so loss of a rejection cannot become a false success. The gateway
retries at 500 ms intervals with a 4 second limit. Existing XBee link status
continues to indicate overall connectivity.

Build `mr2_video_streaming`, `mr2_xbee_bridge`, and `mr2_launch` before restarting
the rover. Build `mr2_video_streaming` on base too, for the receiver executable;
the default gateway path is its normal workspace install location. The existing
`VIDEO_GST_BINARY` / `--video-gst-binary` override can point to another helper
install. Using gst-launch instead retains video but provides no adaptive packet
observations. Restart gateway and rebuild the dashboard to load the new controls.
Deployment to running hardware is separate from source verification.

Validation commands (the PTY test uses isolated ROS domain 171 and no hardware):

```bash
npm --prefix base/gateway test
npm --prefix dashboard run check
npm --prefix dashboard run test:latency
# Source ROS and workspace setup before the next commands.
(cd rover/ros2_ws && colcon build --packages-select mr2_video_streaming mr2_xbee_bridge mr2_launch)
python3 scripts/latency/test_adaptive_video.py --bin rover/ros2_ws/install/mr2_video_streaming/lib/mr2_video_streaming --jetson
python3 scripts/latency/test_video_xbee_control.py --install rover/ros2_ws/install
python3 scripts/latency/test_video_budget_scenarios.py --bin rover/ros2_ws/install/mr2_video_streaming/lib/mr2_video_streaming --output /tmp/mr2-video-scenarios
python3 scripts/latency/test_receiver_access_units.py --bin rover/ros2_ws/install/mr2_video_streaming/lib/mr2_video_streaming/video_receiver
```

Radio acceptance still requires a Rocket M2 field test: sudden capacity drops,
recovery, multiple feeds, interference, XBee coexistence with drive/RTCM, and
p95 network/render delay. Loopback tests cannot establish these radio properties.

Implementation verification (isolated build at `/tmp/mr2-adaptive-install`): both
ROS packages compiled, the allocation CTest passed, all 88 gateway tests passed,
and dashboard check/latency tests passed. PTY/ROS integration confirmed all
three states, idempotent retries, the 80:20 split, budget updates, persistent
trial-lock rejection, and decoded 640x360 -> 320x180 profile transitions. The
native gateway receiver path produced 243 packet observations and 59 correlated
H.264 access units from 60 synthetic frames. Real x264 and Jetson encoder runs
verified live bitrate reductions and actual per-packet UDP pacing (including
RTP buffer-list splitting), and the synthetic MJPEG -> Jetson -> 480x270/15 FPS
path negotiated successfully. No real camera, drive node, radio configuration,
or running deployment was changed for these checks.

### Real camera/base acceptance — 2026-10-29

Code was backed up and applied over SSH to base `192.168.0.54`; its native x86
receiver and dashboard were built on base. Both physical FT231X XBee devices
were used. Rover is currently `192.168.0.57`. Base passed 92 gateway tests, and
Chromium on base rendered the real camera streams through the deployed HTTPS
website, gateway WebSocket, and WebCodecs decoder.

At a controlled 2000 kbps aggregate wire ceiling, the actual dashboard's
Disable/Enable buttons stopped and resumed Front Left transmission. The priority
swap kept the encoder targets at 1360:340 kbps (80:20 after the common 15% margin)
and then 340:1360 kbps. Browser-observed output changed from Front Left 640x360
and RGB-D 320x240 to Front Left 320x180 and RGB-D 640x480. The passing run rendered
391 Front Left frames and 418 RGB-D frames across its on/off/priority phases.
Priorities were restored to Front Left high, RGB-D low, with both ON.

Real-device testing found and fixed: FPS negotiation after NVMM conversion,
small-budget keyframe starvation, receiver-pipe ordering/scheduling noise, and
repeated budget halving during intentional camera reconfiguration. Regression
checks cover bounded jitter, reconfiguration and silent-encoder recovery.

Evidence is in `docs/validation/adaptive-video-2026-10-29/`: the browser report,
actual rendered screenshot, repeatable CDP test, and stable XBee feedback samples.
These are video-path tests on the current Ethernet network. An independently
verified Rocket-only path and induced wireless congestion were not tested.
Clock policy was not locked, so no absolute one-way delay figure is claimed.
The Left USB camera returned V4L2 EINVAL and reconnect events even in a plain
capture-only pipeline; it remains OFF pending USB/camera diagnosis.

The following **transient user services** were used for the video-only test and
have since been stopped. Normal operation uses the regular commands below:

- base: `mr2-video-test-gateway.service`
- rover: `mr2-video-test-rover.service`, `mr2-video-test-xbee.service`

Inspect with `systemctl --user status <unit>` on the respective host. Logs and
start scripts are in `/tmp/mr2-adaptive-live/` on each host. Stop these test
services before starting the normal rover/gateway stack to avoid duplicate
camera/serial owners. The existing field profile and environment files were
preserved; the test explicitly selects `video_streams.json`, base `.54`, ROS
domain 172 for local video/XBee IPC, and a 2000 kbps wire ceiling. Original base
sources and static dashboard are backed up under
`/home/mr2/mr2-video-backups/20261028/`.

### Regular startup and network overrides

With ROS and the workspace sourced, the field commands remain:

```bash
# Base, in ~/mr2-stack/base/gateway
npm start
# Equivalent original explicit arguments:
# npm start -- --base-xbee-device /dev/XBEE --gateway-port 8081

# Rover
ros2 launch mr2_launch rover_real.launch.py \
  enable_manipulator_module:=false enable_xbee_sim:=false
```

Video is enabled by default in the real wrapper. Its original profile selects
the normal 13-feed configuration. Real autonomy is OFF by default, and the
RealSense ROS driver defaults to the same setting so it does not claim the RGB
interface for an IMU-only node. Normal RGB-D video uses `/dev/videoRGBD` through
V4L2 at the JSON's configured 30 FPS. The JSON source and FPS are never overridden
by a special RGB-D ROS-topic parameter.

To opt into autonomy, add `enable_autonomous_module:=true`. The existing
RealSense ROS YAML remains 6 FPS for autonomous RGB-D processing; direct V4L2
RGB-D streaming is disabled in that mode to avoid competing camera owners.
Localization defaults to the autonomous-module setting and can be overridden
explicitly. Other configured USB video streams remain available in either mode.
Feed presence for ROS sources follows received images, including while a feed
is OFF. Encoder buffers follow the current target; capacity increases restart
the buffer when needed. ROS ingest uses the pipeline's
actual running time, so callback delays do not accumulate RTP clock drift.
Hardware-encoded Jetson streams negotiate H.264 level 3.1 across quality transitions. If a ROS
frame cannot fit its bounded pacing window, it is dropped and keyframe recovery
is requested. ROS sources use the same bitrate-to-profile rules as V4L2 sources;
there is no additional ROS-only geometry ceiling. This removes the previous
failure mode where a transient large IDR could lock RGB-D at 160x120 until a
recovery rate above the feed's configured maximum was reached.
Delayed controller/localization
launch configurations remain available after inclusion; the wrapper's separate
GNSS ownership flag prevents duplicate receivers.

The first tile in Live Feed shows the last 60 seconds of network history.
Traffic plots the aggregate video receive rate and GCC budget in Mbps; Signal
plots the base and rover Rocket M2 receive signal in dBm. Hovering the graph
shows the timestamp and values. Gaps indicate unavailable telemetry, rather
than carrying forward the previous signal or treating missing data as zero.
History is bounded and sampled once per second while the Live Feed tab is open.

The same tile also shows latency values:

- **Rover → Base:** median absolute packet transit from the sender's post-pacing
  timestamp to the receiver's pre-jitter timestamp, using epoch microseconds.
- **Link P95:** the 95th percentile of that same packet transit distribution.
- **Min since start:** the lowest observed absolute packet transit since the
  dashboard page started, accepting only clock-verified samples. The
  always-mounted diagnostics panel continues tracking across mission tabs.
  Temporary clock/data loss preserves the recorded value; page reload resets
  it. This is distinct from GCC's size-specific queue baseline. The gateway
  supplies each second's minimum as input to this running minimum.
- **Extra queue:** median additional transit above GCC's packet-size-specific
  baseline, using monotonic timestamps and independent of clock offset.

These measure the UDP video link, not camera-to-screen latency. Capture,
encoding, sender pacing, receiver jitter/decode and browser display are outside
the absolute packet transit boundary. Absolute values require the existing
fresh, locked base/rover Chrony readiness checks, including a selected base
peer and a 2 ms error bound. The UI shows `Clock sync` until those checks pass;
the extra-queue value remains usable without synchronization. Missing, invalid,
negative, old-format or expired epoch observations show a dash, not zero.
Probe packets and duplicate observations do not contribute to the distribution.
The gateway publishes these measurements on the existing once-per-second
browser metrics message. GCC budget decisions and the XBee feedback format
are unchanged. See the
[Live Feed latency validation](validation/live-feed-latency-2026-10-29/README.md).

The HIGH/LOW table shows actual received video traffic, allocated wire budget,
and utilization (`received / allocated * 100`). Received rates include
RTP/UDP/IPv4 overhead and use the same recent receive-time window as GCC.
Allocations come from the rover's reported encoder targets divided by 0.85;
the displayed budget is approximate within 1.2 kbps per active feed because
encoder targets are rounded down to integer kbps. OFF and NO DEVICE feeds
do not contribute budget. Unallocated utilization is shown as a dash;
an allocated class receiving no packets shows 0%. Short bursts can exceed
100%. The total GCC budget and the sum of current class allocations can differ
during feedback transitions or when configured feed caps leave spare capacity.
Metrics travel from the base gateway to browsers once per second using the
existing WebSocket; no new XBee messages or Rocket M2 management polls are added.
These are video receive rates, not a measurement of the radio's total capacity.

Field defaults stay base `192.168.1.101`, rover `192.168.1.102`, base Rocket
`192.168.1.100`, rover Rocket `192.168.1.106`, drone `192.168.1.103`.
On the current development LAN, use only per-run IP overrides:

```bash
# Base
npm start -- \
  --rover-host 192.168.0.57 \
  --base-rocket 192.168.0.244 \
  --rover-rocket 192.168.0.245 \
  --drone-rocket ''

# Rover
ros2 launch mr2_launch rover_real.launch.py \
  enable_manipulator_module:=false enable_xbee_sim:=false \
  video_base_host:=192.168.0.55
```

The explicit base also selects the session's clock lease peer; it does not
persist into the clock configuration. The gateway's ROS proxy follows
`--rover-host`, so browser/nginx field addresses need no development edit.
Rocket polling bootstraps the airOS cookie and follows its login redirect with
GET. An unreachable management interface is reported as such; this alone cannot
establish that the RF link is down. The absent drone's explicit empty IP disables
only that poller for the run.

The regular startup was exercised on both hosts with only these IP overrides;
autonomy, RealSense, drive hardware, GNSS, LED and turret retained their normal
defaults. The rover had 39 direct children, including one video node, one XBee
bridge, RealSense, controller manager and the delayed localization nodes, with
no launch exception, video pipeline error, or child exit during the final run.
The deployed browser test rendered 708 Front Left and 98 RGB-D frames, exercised
the real Disable/Enable button, swapped priorities, received ROS video state
through nginx, and confirmed both Rocket management pollers connected. Initial
encoder allocation was 584:146 kbps (80:20); after swapping, the high stream hit
its cap and lent the remaining budget to the low stream. Browser output included
Front Left 640x360/320x180/480x270 and RGB-D 240x180/160x120/640x480.

Final checks passed: 95 gateway tests on each host, 29 launch composition and
destination tests, 15 clock-policy tests, 5 field-configuration tests, the C++
allocation test, and isolated x264/Jetson RTP timing/pacing tests. The 6fps Jetson
encoder also survived live changes between 150 and 1800 kbps for all 36 frames.
Evidence is in `docs/validation/normal-startup-2026-10-29/`. Only the verification
processes were stopped afterward; the installed code and field defaults remain.
This validates normal startup and the current LAN path. Rocket-only transport,
induced RF congestion/interference, absolute one-way delay, and driving remain
outside this run; clock precision was not ready for an absolute latency claim.

### Sequential allocation and congestion recovery verification

The later sequential-allocation revision passed 100,000 allocation combinations,
119 gateway tests on each host, three CTests, 17 production-node phases,
failed/stuck first-camera checks, and real XBee/ROS framing tests. Real-camera
browser tests exercised 32/600/1500 kbps, HIGH selection in reverse JSON order,
restoration of all five feeds, and disable/enable. Native H.264 AU framing also
removed the extra next-frame wait at low FPS. See
[`adaptive-sequential-2026-10-29/README.md`](validation/adaptive-sequential-2026-10-29/README.md)
for evidence, measured timing boundaries, deployed addresses and limitations.
