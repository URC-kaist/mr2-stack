# AK servo startup feedback validation

The driver receives raw CAN status immediately, but keeps control feedback
(position, velocity, current, and their accepted timestamps) unavailable until
startup validation passes. Both control backends use this feedback to capture
their boot reference. While it is unavailable, the driver sends no position
commands, including its automatic hold fallback.

Readiness requires a minimum elapsed time, a full window of consecutive
error-free status packets, and a bounded position range over that entire window.
Only received packets count; repeatedly reading feedback does not count.
The latest accepted sample is exposed when validation passes. Readiness is
latched, so subsequent commanded motion does not trigger startup validation.

Optional hardware component parameters (integer values):

| Parameter | Default | Meaning |
| --- | ---: | --- |
| `startup_min_duration_ms` | 200 | Minimum time since the first packet in the uninterrupted startup stream; 0–60000 ms. |
| `startup_stable_samples` | 5 | Required window size; 2–1000 packets. |
| `startup_position_tolerance_tenths_deg` | 1 | Maximum minus minimum raw motor position in 0.1-degree units; 0–65535. A value of 0 requires exact equality. |
| `startup_max_gap_ms` | 100 | Maximum inter-packet gap during startup; 1–500 ms. |

These defaults are provisional engineering settings based on passive startup
observations, not a guarantee that a stable encoder reading is correct. At the
observed 20 ms status interval, the time condition normally dominates: readiness
occurs at or after 200 ms, provided the last five samples meet the conditions.

A nonzero error clears the sample window. A gap exceeding the configured limit
(or a non-increasing receive timestamp) restarts both the window and minimum
duration. Temperature telemetry and raw reception tracking continue while
control feedback is withheld. All timing uses the monotonic steady clock.

Starting one second after the first valid status frame, an unready motor logs
`AK servo <id> feedback has not stabilized.` at WARN level once per second.
The per-motor cadence follows mr2_control's 1 Hz diagnostic update interval and
stops when ready. With no status frames, the existing missing-feedback timeout
message applies instead.

This change validates initial startup only. It preserves the existing timeout
and automatic recovery behavior after readiness; it does not reset an existing
boot reference after a motor power cycle or discard upstream targets. A ready
motor reports subsequent position changes normally.

Regression tests live in `mr2_control/test/test_drivers.cpp` and
`mr2_control/test/test_converter.cpp`. They inject time and CAN frames and capture
transmissions in memory, without opening a real CAN interface. They cover the
recorded initial position jumps, duration/sample boundaries, drift and tolerance,
errors, gaps, warning cadence, normal movement, and boot-reference capture.
