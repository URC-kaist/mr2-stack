# Video latency improvement proposal — 2026-10-30

Implementation follow-up: [changes, live before/after results and USB interruption](validation/video-latency-implementation-2026-10-30/README.md).

This is a review and proposal. Product code and runtime settings were not
changed. Findings combine current source, installed Jetson properties, two
isolated encoder probes and the actual 15-minute base/rover observation.

Follow-up: [fixed-resolution steady-stream probes](steady-stream-latency-review-2026-10-30.md)
confirmed a one-frame decoder wait and sender pacing backpressure. They also
correct the earlier assumption that the receiver's 40 ms setting is always a
40 ms residence time. For ordinary streaming latency, prioritize the decoder's
low-delay option and bounded sender scheduling.

## What the current latency number covers

```text
Sensor / USB → capture → scaling / encode → pacing → kernel / Rocket link
                                                       ↓
canvas ← browser decode ← WebSocket / gateway ← depay / jitter buffer
```

The Live Feed link statistic starts **after sender pacing**, immediately before
udpsink, and ends at base udpsrc **before jitter buffering**. It therefore does
not measure most application waiting or full camera-to-screen latency.

The 15-minute test showed a median of the displayed one-second medians of
4.4 ms, but displayed one-second P95 values peaked at 183.3 ms. Seven of 900
UI samples had P95 above 100 ms, 13 above 80 ms and 63 above 40 ms. These are
counts of UI/bin values, not packet-loss or pooled packet-percentile estimates.
Full current source-to-render latency was not measured in that run.

The older September source-to-render measurements in `video-latency-timing.md`
describe an earlier pipeline and cannot be used as the current baseline.

## Main findings and proposed changes

### 1. Preserve the first IDR while configuring the browser decoder

`dashboard/src/components/VideoStreamCard.tsx:330` clears the decoder config,
awaits `VideoDecoder.isConfigSupported()`, and drops chunks while configuration
is unavailable. The gateway sends config then IDR immediately. The shared
client also replays config then cached IDR synchronously to a later local
subscriber. That replay deterministically reaches the IDR handler before the
asynchronous config completes. The ordinary WebSocket path has a similar race,
whose occurrence depends on scheduling and support-check completion time.

Retain the newest IDR **and a short contiguous sequence of dependent chunks**
for that config generation, then decode as soon as configure completes. Bound
this startup queue by age and size; on overflow discard the entire sequence
and request a new IDR. Cache supported configurations to avoid repeated checks.
Do not decode a P-frame sequence with missing references. Also try the supported
`optimizeForLatency: true` hint, retaining software decode fallback.

This is the first implementation priority: small scope and a direct path to
removing an avoidable wait for the next IDR. It is not yet proof that the race
caused every observed pause.

### 2. Keep capture alive across routine adaptation

`video_streaming_node.cpp:225` restarts on an allocation reduction exceeding
30%, even if dimensions/FPS need not change. Changes in dimensions or FPS also
call shutdown. Shutdown synchronously waits up to 500 ms for each child.
Multiple cameras can therefore delay the same ROS callback in sequence.

The accepted test log shows LOW cameras RGB-D, Left and Right restarting
within roughly 6 ms of one another at 21.325–21.330 s and 634.419–634.425 s.
The sampled render gaps follow these transitions. This establishes a close
temporal association, not a complete breakdown of the delay within each stage.

Use live bitrate changes as the first response. Keep camera capture and its
native format/30 FPS open; drop raw frames for lower output FPS without
reopening the device. Reconfigure only the encoder/scaler branch when dimensions
must change. Hardware caps changes may still require a branch state transition;
do not assume seamless PLAYING renegotiation. Start its new generation with
SPS/PPS and an immediate IDR, and switch atomically at a valid frame boundary.
Coalesce commands and make child teardown asynchronous with a bounded deadline.

Keep asymmetric upgrade/downgrade thresholds, but add actual encoder-output and
pacer-load headroom to profile decisions. Avoid frequent upgrades followed by
immediate reversals. Emergency reductions must still work promptly. Preserve
80:20 class allocation and JSON ordering when only a prefix can fit.

### 3. Control sender frame age and serialization time

`rtp_send_time.hpp:38` currently admits delta frames against a 150–300 ms
window and IDRs against 300 ms. These are **allowed completion windows**, not
a fixed delay added to every frame. The clock stamp is taken after the pacing
wait, so sender waiting does not appear in the link plot.

Check source-frame age using the sender's monotonic clock before expensive
work and again before admission. For 30 FPS, evaluate initial completion
limits around 50–70 ms for delta frames and 100–150 ms for IDRs. These are
experimental starting points, not approved defaults. Low-FPS/very-low-budget
operation needs a separate feasible envelope so a small IDR can still start.
Do not simply shrink every deadline and recreate the former IDR starvation.

An oversize frame should trigger smaller encoder output and a coalesced recovery
request, rather than repeated unfittable IDRs. Account for measured output bytes,
wire overhead and actual sender waiting. Coordinate IDR/probe bursts between
cameras. If measured aggregate bursts remain problematic, evaluate one aggregate
wire shaper with HIGH/LOW scheduling while preserving the allocation policy.

### 4. Tune receiver waiting for missing/reordered packets

`base/gateway/src/config.js:19` sets jitter buffering to 40 ms. Camera-to-screen
latency includes any actual waiting here, but the configured value is not a
fixed residence time in the current `sync=false` delivery path. Follow-up
ordered-loopback probes measured approximately 0.06–0.07 ms marker residence
with 40, 20 and 10 ms settings, not a 30 ms normal-stream reduction. The earlier
expectation of an automatic 30 ms saving is withdrawn. Compare these settings
under actual reordering and late-packet loss, then consider adaptation.

Lower buffering also discards more late packets, which can cause a much longer
IDR recovery wait. Therefore improve loss/recovery before selecting the smaller
default; evaluate end-to-end tails and freezes rather than the configured
buffer size alone. Check the real deployed browser and link, not just localhost.

### 5. Remove optional metadata from the delivery dependency

`base/gateway/src/video/receiver.js:83` permits up to 64 unmatched access units
and uses a 100 ms correlation timeout. Its timer can keep unmatched units
waiting longer than one timeout tick. Two default GStreamer tee queues and
blocking AU/stdout and RTP/fd3 writes can also stall under Node/IPC pressure.
Default queue limits are 200 buffers, 10 MB or one second, whichever comes first;
those are capacity limits, not a mandatory one-second delay.

Carry verified RTP identity with the native AU record on the same pipe. If
identity cannot be verified, emit video immediately without the optional
correlation. Keep the existing nonblocking pre-jitter GCC observations separate.
Remove the duplicate whole-RTP metadata branch when no longer needed. Bound
delivery queues by age at complete-AU boundaries, not arbitrary RTP fragments.

The accepted 15-minute log did not show correlation-timeout warnings. This is
a structural latency/backpressure risk, not a demonstrated 100 ms cost on every
frame in that test.

### 6. Bound gateway/browser backlog by time and preserve dependencies

The gateway allows 1 MiB of queued WebSocket data and multiplexes all feeds on
one TCP connection. At 7 Mbps this amount is about 1.2 s of video; it only
becomes delay if it actually accumulates. Once bytes enter TCP they cannot be
withdrawn by dropping a later frame. A stalled connection affects every feed.

Bound application queues by age as well as bytes before ws.send. On overflow,
resynchronize to a fresh IDR instead of replaying an old IDR and resuming an
incomplete reference chain. The browser currently skips delta chunks at
decodeQueueSize > 3; replace arbitrary reference-frame skipping with explicit
recovery when necessary. A queue count does not measure decoder-internal delay.

Keep the main thread light; consider decoding/rendering in a worker only if
profiling identifies main-thread contention. Separate HIGH/LOW transport
connections can be evaluated for slow remote browsers, but do not guarantee
bandwidth priority. Replacing base-to-browser transport with WebRTC is a later
option if TCP blocking is demonstrated, not the first change for a browser
running locally on base. Rover congestion feedback remains on XBee.

### 7. Make GCC react to sustained tail delay and local backlog

`congestion.js:160` uses median extra queue and a 40 ms threshold in addition
to trend/loss detection. That can leave a small delayed fraction untreated:
near the 183.3 ms P95 peak, the median queue was approximately 6.5 ms.
Rate estimation uses a one-second window and feedback is sent every 200 ms.
`configurationChanged()` resets the shared estimator and suppresses normal
overuse decisions for 1.5 s after changes from any camera; loss backoff still
operates. Repeated profile changes can overlap this protection period.

Keep stable throughput estimation but add a faster persistent-tail/backlog
path. Start experiments with a lower median-queue target around 10–20 ms
and a sustained high-percentile queue trigger; do not cut quality for a single
outlier. Local sender waiting must cause local action without waiting for a
base → XBee round trip. Distinguish one stream's epoch/reconfiguration from
real aggregate network congestion, so transitions do not mask genuine overload.
Keep sparse bounded probes and recovery headroom. Extra radio retry delay
also requires airtime/loss evidence before attributing every tail to overload.

### 8. Keep the existing efficient path; review camera/USB only with evidence

Current direct V4L2, hardware MJPEG/H.264, NVMM conversion, one-buffer leaky raw
queue, 30 FPS capture, disabled exposure-driven FPS reduction and default zero
B-frames/UltraFast hardware preset already support the goal. The JSON x264
`tune` and `speed_preset` fields do not configure the hardware branch.
Making already-default settings explicit can improve clarity, but is not a
demonstrated speedup.

USB inspection shows four USB2 cameras behind a shared hub and RGB-D on USB3.
Check exposure, camera-internal MJPEG latency and USB saturation when frame-age
measurements point there. The simultaneous Left/RGB-D gaps cannot be assigned
to a shared USB2 link simply from topology. No ROS-topic/6 FPS override is
proposed, and autonomy/camera ownership defaults stay unchanged.

## Probe evidence

Installed platform: Jetson Linux R36.5, GStreamer 1.20.3. Despite the property's
conservative introspection mutability flag, a generated 640×360/30 FPS snow
stream showed effective live bitrate changes in the actual hardware encoder:
1.8 Mbps target → 0.3 Mbps → 1.8 Mbps produced approximately 1.764 → 0.481 →
1.764 Mbps in successive two-second measurement windows after settling.
This verifies live influence, not an exact output cap or image-quality result.
It also shows that a fixed 85% target margin cannot guarantee the bound for
all scene complexity. A five-second generated-ball probe with current interval
settings produced IDRs at approximately 1-second intervals after startup.
Neither probe opened a camera, sent network packets, changed a host clock,
or started the rover/base application.

Evidence: [15-minute observation](validation/clock-stability-15m-2026-10-30/README.md),
[encoder probe](validation/video-latency-review-2026-10-30/encoder_probe.json),
[IDR probe](validation/video-latency-review-2026-10-30/idr_probe.json).

## Implementation and validation order

1. Fix browser config/IDR ordering and explicit recovery first. Add decoder
   latency hint and bounded startup/backlog handling. Validate delayed config
   promises, immediate IDRs, stale generations, late subscriptions and slow
   browsers with deterministic tests.
2. Eliminate routine full capture restarts, remove metadata waiting, and bound
   sender frame age. Validate repeated bitrate steps, abrupt capacity drops,
   resolution/FPS changes, independent camera failures and tiny-budget startup.
3. Compare receiver latency settings and tune fast queue/tail control. Include
   noise/motion scenes, 1–5% loss, reordering, short bursts, low capacities,
   abrupt good → poor → good recovery and simultaneous IDRs/probes.
4. Re-run at least 15 minutes on unchanged scenes and budgets, then test the
   actual field radio path. Use existing Uplink source/encoder/RTP/render
   correlation and temporary logs; add no dashboard instrumentation panels.
   Also use a physical filmed timer or light transition when sensor-to-screen
   delay matters. Existing source probes do not include sensor exposure or
   camera-internal/earlier USB delivery; canvas time excludes display scanout.

Compare the same matched-frame P50/P95/P99, age of the image currently visible,
freeze duration/frequency, recovery time, FPS, quality and link utilization.
Counting only delivered frames can hide freezes by omitting the missing ones.
First acceptance goals are fewer/shorter transition pauses and a measurable
end-to-end P95 reduction without losing useful quality at equal budget;
absolute targets should be set from the new current baseline. Preserve
very-low-budget startup and the validated clock behavior.

Fault injection must be limited to video UDP destinations/ports or isolated
namespaces. Keep SSH, Internet, NTP and XBee control available; do not introduce
a public STUN/TURN or other Internet dependency. Field addresses and the usual
base npm / rover ROS launch commands remain the operational interface.

Primary references: [GStreamer jitter buffer](https://gstreamer.freedesktop.org/documentation/rtpmanager/rtpjitterbuffer.html),
[queue behavior](https://gstreamer.freedesktop.org/documentation/coreelements/queue.html),
[H.264 depayloader recovery](https://gstreamer.freedesktop.org/documentation/rtp/rtph264depay.html),
[WebCodecs decoder configuration](https://www.w3.org/TR/webcodecs/#videodecoderconfig),
[NVIDIA R36.5 accelerated GStreamer](https://docs.nvidia.com/jetson/archives/r36.5/DeveloperGuide/SD/Multimedia/AcceleratedGstreamer.html).
