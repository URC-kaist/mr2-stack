# Weak-link video freshness control

The objective is to preserve useful, recent rover video as RocketM2 capacity
falls, rather than replaying seconds of buffered video. The existing
delay-based GCC estimator remains the capacity controller. A separate
freshness guard bounds the age of packets admitted to the native receiver and
can pause media to let downstream queues drain.

## Why bitrate adaptation alone was insufficient

The encoder's rate is not the radio's instantaneous capacity. After a sharp
capacity collapse, several feedback intervals of high-rate video can already
be inside the Ethernet interface or RocketM2. Lowering the next encoder's
bitrate cannot remove those packets. A large FIFO then delivers old video at
the reduced rate.

Previously, a large rate cut could also clear the controller's transit floor,
allowing a standing queue to become the new normal. Ordinary rate cuts and
camera/encoder restarts now retain that evidence. Only a controlled media
pause followed by proof of a fresh path permits a new floor.

## Control sequence

1. **Bootstrap:** the rover starts at zero media budget. The base requests a
   small probe using the existing XBee VIDEO_FEEDBACK fields. The rover returns
   it on the video UDP path. A timely reply opens a conservative 32 kbps total
   media budget, retaining JSON-order admission and HIGH/LOW allocation.
2. **Normal operation:** packet grouping, delay trend, a 20 ms median extra
   queue target, persistent queue tail and received throughput drive GCC.
   A short-window throughput collapse with growing queue gives an additional
   fast cut. Low-rate passive growth is allowed only below a measured
   conservative capacity; otherwise short probes must prove headroom.
3. **Drain:** persistent excessive age/queue, media silence, or an expired
   freshness bound closes the receive gate and sends zero media budget.
   The rover keeps V4L2 capture open but closes the encoder branch. Only an
   8 kbps, 100 ms probe approximately once per second remains. Feedback loss
   for two seconds also pauses rover media instead of falling back to 500 kbps.
4. **Recovery:** a new, timely probe issued after the drain begins authorizes
   conservative resumption. Capture uses current images; the encoder restarts
   with a new SSRC/IDR. Capacity discovery then raises quality as headroom is
   measured. An obsolete reply from before the pause cannot reopen the gate.

All camera allocations still use 80:20 HIGH/LOW with unused-budget borrowing
and admission in the order of
`rover/ros2_ws/src/mr2_launch/config/video_streams.json`. An ON feed may have
zero temporary allocation; its requested enable state is preserved.

## Freshness without Internet or synchronized wall clocks

Let `B0` be the base's monotonic time when it issues a probe request, `R1` the
rover's monotonic transmit time in the reply, and `B2` the base's native arrival
time. The rover cannot transmit that reply before the request was issued.
Therefore `B0 - R1` is a lower bound on the base-minus-rover clock offset.
Subtract the allowed relative drift over the request/reply interval as well.
For a media packet stamped `R`, `B - R - lower_offset` is a conservative upper
bound on its transmit-to-base age.

Only replies arriving within 350 ms of issue establish a bound. It expires
after 15 seconds, is refreshed during normal operation, and decays at a
250 ppm relative drift allowance. The deployed chrony hold policy limits each
host to 100 ppm while streaming. Linux Node `hrtime` and native
`steady_clock` supply the same host monotonic time domain. Neither Internet
NTP nor the two computers' boot epochs are needed for this gate.

Probe tokens include the existing XBee session nonce and probe ID so delayed
replies from a previous gateway session cannot authorize a new session.
No XBee wire-format change or additional telemetry message is required.
The native receiver receives its policy on a private 32-byte stdin pipe.

This bound is a safety decision, not a replacement for the dashboard's
measured one-way delay. That existing display still requires valid rover/base
clock synchronization and retains its single `Clock sync` status.

## Local limits and adaptation

- The native receiver records every packet for congestion estimation, then
  discards media whose age upper bound exceeds **500 ms**, before RTP jitter
  buffering and again before depayloading. Paused, missing-timestamp and
  expired-policy media also fail closed. Loss recovery waits for a fresh IDR.
- Sender admission now considers **existing frame age plus remaining pacing
  and serialization time together**, within its 150–300 ms frame deadline.
  Zero aggregate budget also cancels queued pacing reservations.
- A profile downgrade of more than 30% is measured against the allocation at
  the last applied profile. Several consecutive small cuts therefore trigger
  immediate downgrade too. Normal downward hysteresis is one second plus
  a three-second minimum profile interval; upward stability remains five
  seconds. V4L2 capture is retained across profile changes and temporary pause.
  Resuming after a deliberate pause also renews the encoder-progress watchdog
  grace period, so the paused interval is not treated as a failed encoder.
- The gateway rejects native complete frames retained in its local pipe for
  more than 150 ms. Existing byte/age WebSocket limits and IDR recovery remain.
- A canvas with no rendered frame for one second is hidden and shows
  `Waiting for fresh video...`, so an old still image is not labeled Live.
- RF random loss uses five seconds of evidence, with at least 20 packets,
  rather than repeatedly interpreting sparse one-second loss ratios as link
  congestion. Delay evidence remains immediate. Probes measure media and
  padding bytes over the **same** arrival window; adding an unrelated prior
  one-second media average previously overstated capacity.

For a 640×360/30 FPS input, the normal profile ladder is below. The threshold
uses **that camera's wire allocation**, with encoder target
`floor(0.85 × allocation)`, not the aggregate link budget.

| Per-camera wire allocation | Output resolution / FPS |
| --- | --- |
| ≥ 1412 kbps | 640×360 / 30 |
| 765–1411 kbps | 480×270 / 30 |
| 353–764 kbps | 320×180 / 30 |
| 212–352 kbps | 320×180 / 15 |
| 118–211 kbps | 240×134 / 10 |
| 71–117 kbps | 160×90 / 5 |
| 32–70 kbps | 80×44 / 3 |

The RGBD input remains direct V4L2 at its configured 30 FPS; its 4:3 profiles
are proportionally 640×480 through 80×60. Source capture/exposure settings and
the autonomous ROS configuration are unchanged. Separate encoder-overload
recovery can select a smaller profile. Sensor readout, USB transport and
display scanout are outside the packet-age boundary.

## Limits and operations

These limits are **not a guaranteed 500 ms camera-to-screen latency**.
Application code cannot flush packets already inside a RocketM2 FIFO or make
an outage carry video. A severe collapse can cause a visible pause while that
queue drains; the policy prevents replay of that old backlog. Continuous
video also needs enough bandwidth for the minimum useful encoded frame.
Radio-side queue/AQM support would be needed to bound that drain time itself;
no radio firmware, interface, route, address or Internet setting was changed.

The actual gateway enables the guard with the default compiled
`video_receiver`. Explicit legacy `gst-launch-1.0` receiver mode retains its
previous behavior. Update rover and base together, restart their usual launch
commands, and reload the dashboard to load its new assets. Field address
defaults and command-line options are unchanged.

Validation and the distinction between model, real codec and physical radio
tests are in [the validation report](validation/weak-link-freshness-2026-10-30/README.md).

The design follows the low-delay and circuit-breaker requirements in
[RFC 8836](https://www.rfc-editor.org/rfc/rfc8836.html). It is a custom
GCC-inspired controller, not a complete libwebrtc GoogCC implementation.
WebRTC's [send-side estimator](https://webrtc.googlesource.com/src/+/ad10222289272cd8728c0ebb7cd80301a5a70937/modules/congestion_controller/goog_cc/send_side_bandwidth_estimation.cc)
also requires a minimum packet sample for loss decisions; its
[loss-based BWE v2](https://webrtc.googlesource.com/src/+/main/modules/congestion_controller/goog_cc/loss_based_bwe_v2.cc)
distinguishes inherent loss from congestion. Our simpler five-second evidence
window and bounded probes address that distinction without adopting the full
WebRTC estimator.
