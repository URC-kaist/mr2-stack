# Fixed-resolution streaming latency review — 2026-10-30

Implemented and measured: [live validation and limits](validation/video-latency-implementation-2026-10-30/README.md).

The focus here is latency **during ordinary streaming without resolution/FPS
changes**, rather than transition pauses. Product code, application settings,
clock policy, camera controls and host networking were not changed.

Isolated generated-video probes found two significant normal-stream costs:
one-frame browser decoder buffering and sender pacing/backpressure. Lowering
the receiver's nominal 40 ms jitter setting did not remove 30 ms of ordinary
latency in the tested current-style path; the earlier proposal is corrected.

## 1. Browser low-delay mode: a measured one-frame improvement

Current `VideoStreamCard.tsx` omits `optimizeForLatency`, whose default is false.
Generated Jetson H.264 at 640×360, constant 30 FPS and 1.8 Mbps was played on the
actual base in Chromium 147.0.7727.15. The benchmark used AVCC conversion and
avcC configuration like the dashboard, `hardwareAcceleration: no-preference`,
and canvas drawing in the decoder callback. Headless flags matched the earlier
live observer, including `--disable-gpu`. Codec configuration completed before
timed streaming; there were no reconfiguration or loss events.

| Fixed input | Setting | Decode-call → output p50 | p95 | Later inputs required before output |
| --- | --- | --- | --- | --- |
| 30 FPS, one decoder | Current/default | 33.7 ms | 33.8 ms | 1 |
| 30 FPS, one decoder | `optimizeForLatency: true` | 2.4 ms | 2.9 ms | 0 |
| 30 FPS, five decoders | Current/default | 40.4 ms | 44.9 ms | 1 |
| 30 FPS, five decoders | `optimizeForLatency: true` | 7.9 ms | 13.1 ms | 0 |
| 30 FPS, five decoders, repeated control | Current/default | 40.4 ms | 44.9 ms | 1 |
| 5 FPS, one decoder | Current/default | 200.8 ms | 201.0 ms | 1 |
| 5 FPS, one decoder | `optimizeForLatency: true` | 4.0 ms | 4.1 ms | 0 |

The 30 FPS steady samples excluded the first 12 and last 5 frames: 77 output
samples per decoder, or 385 for five decoders. The 5 FPS comparison used
30 generated frames and 13 steady samples per setting. No decoder errors were
reported. Output during a final cleanup flush was excluded. Queue size was at
most one even in the slow configuration: decodeQueueSize alone misses this
internal hold. Both fixtures used zero B-frames; changing encoder POC type from
0 to 2 did not remove the wait. The explicit decoder flag did.

At 30 FPS this saved approximately **32 ms** in this isolated five-decoder
test without changing encoded video, quality, frame rate or radio use. Low-FPS
streams can benefit even more because a one-frame wait lasts longer.
This is not a new measurement of full live camera-to-screen latency. A real
operator's browser/GPU/OS may select another decoder and needs live validation.

Recommended first change: request low-delay decoding, verify support, and keep
the existing fallback. Test both full-rate and low-FPS profiles, plus the
separate config/IDR ordering fix described in the earlier proposal.

## 2. Normal sender pacing causes substantial pre-network delay

The actual installed `video_sender` binary ran generated fixed 640×360/30 FPS
video into loopback UDP. Its existing frame-timing records were matched to the
last RTP packet of each frame. All clocks were on the same host. Receiver
timestamps were taken by a small Python UDP observer; no camera, radio path,
clock lock or mission application was started.

| Scene / pacing condition | Source output → final packet send p50 | p95 |
| --- | --- | --- |
| Simple moving ball, encoder 1.8 Mbps / pacer 2.1 Mbps | 4.9 ms | 5.5 ms |
| Snow texture, encoder 1.8 Mbps / pacer 2.1 Mbps | 38.2 ms | 77.4 ms |
| Same snow configuration, loopback diagnostic pacer 8.4 Mbps | 11.3 ms | 17.4 ms |
| Snow, repeated original 2.1 Mbps control | 39.3 ms | 68.8 ms |

Each case ran 96 source frames; 93–94 frames matched and 83–84 remained after
discarding the first 10 matched frames. P50 final-packet loopback transit was
0.07–0.20 ms, so the large difference was before network transport.

Without pacing, encoder residence was about 3.1 ms. With the ordinary-rate
snow probe it had a long tail of 35–46 ms at p95, consistent with backpressure
propagating from sleeping downstream probes. Periodic IDRs still occur during
ordinary fixed-profile streaming and contribute bursts. These samples are
not exclusively non-IDR P-frames.

The 8.4 Mbps case was **a loopback diagnostic only**, not an approved transmit
rate or a claimed field-link improvement. Sender pacing must respect actual
aggregate link capacity. Increasing every camera's pacing rate independently
would move waiting into the Rocket queue and could make latency worse.

Recommended design:

- Keep source/encoder processing separate from packet-timer sleeps using a
  bounded sender worker. Moving the same wait to another thread alone does not
  eliminate frame age; an unbounded extra queue would defeat the purpose.
- Apply one aggregate wire budget with class-aware, deadline-aware scheduling,
  while retaining 80:20 allocation, borrowing rules and JSON prefix admission.
  Use bounded bursts to finish fresh frames quickly when capacity is available.
- Track source-frame age and queued bytes locally. Feed those into input-frame
  selection and actual encoder bitrate control without waiting for XBee feedback.
  Preserve reference dependencies or explicitly recover at an IDR after a skip.
- Coordinate periodic IDR phases and short capacity probes across cameras.
  Avoid a repeated synchronized burst even when no profile changes occur.
- Retain feasible low-rate IDR startup. Deadline tightening must not recreate
  the earlier tiny-budget no-start condition.

## 3. Correction: a 40 ms jitter setting is not 40 ms steady residence

Receiver probes reproduced `udpsrc → rtpjitterbuffer → rtph264depay → h264parse
→ sync=false` with generated H.264 UDP on loopback. Input and post-jitter
markers were matched, and a subset of access units was also matched through
PTS. Source/pay markers and receiver observations used the same monotonic clock.

| Configured latency | Marker residence p50 | Pay → post-jitter marker p50 |
| --- | --- | --- |
| 40 ms | 0.071 ms | 0.975 ms |
| 20 ms | 0.065 ms | 1.001 ms |
| 10 ms | 0.064 ms | 0.949 ms |
| 40 ms repeated | 0.064 ms | 0.933 ms |

Each setting matched 96 marker packets and excluded the first 10 for steady
statistics. The matching downstream AU subset also showed approximately
1.1 ms pay → parsed AU, not 40 ms. AU matching coverage varies with PTS changes
and is included in the raw result; marker matching was complete.

The element can wait at startup or for missing/reordered packets. It also
reports latency for sink clock synchronization. In this ordinary ordered
`sync=false` delivery test, it did not hold each frame for the configured
duration. Therefore **40 → 10 ms cannot be promised to save 30 ms of normal
latency**. Tune this setting as a loss/reordering/recovery tradeoff, with a
different experiment that introduces those conditions. No field RF loss or
reordering was injected in these probes.

## 4. Further steady-stream candidates, not yet quantified improvements

- **Rendering and compositing:** five decoders draw serially on the main
  thread. Canvas draw calls were typically around 1.8–2.5 ms each, with several
  millisecond tails. After fixing decoder buffering, compare worker decoding /
  OffscreenCanvas and GPU rendering. Try supported opaque/desynchronized canvas
  contexts only with actual display checks; desynchronization is a hint and can
  introduce tearing. A draw callback time is not physical screen presentation.
- **Capture freshness and exposure:** inspect exposure/readout and original
  V4L2 capture timestamps versus dequeue time. Keep the newest raw frame and
  prevent accumulated device/driver age. Shorter exposure can help in bright
  conditions but has noise/brightness tradeoffs. A DMA buffer count by itself
  does not prove that many frames of delay. Do not confuse `num-buffers` (total
  output limit) with capture pool depth. No camera controls were changed here.
- **Stable quality adaptation:** maintain useful output FPS while changing
  bitrate based on scene complexity, local age and actual output bytes. Preserve
  the low-budget fallback rather than forcing 30 FPS at an infeasible rate.
- **Gateway/browser queues:** queue capacity is not current queue occupancy.
  Byte/age caps and worker scheduling help under sustained load, but do not
  create a demonstrated fixed saving while queues are empty. Keep optional
  correlation metadata off the delivery critical path.

Measured low-priority changes: `videorate drop-only=true` added only about
0.14–0.17 ms source → rate processing in the 30 FPS probe, not an extra frame.
POC type 2 did not improve the approximately 3.1 ms hardware encoder residence
or remove browser hold. The installed ws library already calls setNoDelay();
adding the same TCP setting is not a new remedy.

## Evidence, limits and next step

Results and scripts are in
[`validation/steady-stream-latency-2026-10-30/`](validation/steady-stream-latency-2026-10-30/).
The browser uses the actual base binary and product-like decode/canvas code,
but a separate page without dashboard plots/ROS traffic. Sender/receiver probes
use loopback, not the field radio link. They exclude physical sensor/USB and
monitor scanout. Separate-stage percentile improvements must not be added into
a claimed total live P95 reduction.

Implement low-delay decoding first; it is the smallest confirmed steady-state
win. Then evaluate aggregate bounded pacing and main-thread/rendering work.
Use fixed-profile real-camera runs and existing matched source/RTP/render
diagnostics for before/after validation, alongside image age and freeze duration.
Keep dashboard status as Clock sync, ordinary launch commands, field IP defaults,
XBee feedback and offline operation. No public service or new dashboard panel
is needed. All temporary probe processes were stopped after testing.

References: [WebCodecs configuration](https://www.w3.org/TR/webcodecs/#videodecoderconfig),
[Chromium low-delay preference](https://chromium.googlesource.com/chromium/src/+/main/third_party/blink/renderer/modules/webcodecs/video_decoder.cc),
[GStreamer 1.20.3 jitter-buffer forwarding logic](https://raw.githubusercontent.com/GStreamer/gstreamer/1.20.3/subprojects/gst-plugins-good/gst/rtpmanager/gstrtpjitterbuffer.c),
[HTML canvas latency hints](https://html.spec.whatwg.org/multipage/canvas.html).
