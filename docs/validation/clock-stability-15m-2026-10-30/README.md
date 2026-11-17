# Fifteen-minute clock and live-video check — 2026-10-30

The unchanged implementation passed a **continuous 900-second clock check**:
all **900 / 900** one-second dashboard samples displayed qualified absolute
latency, with **0 Clock sync samples**. Short video rendering gaps and latency
spikes were also observed and are recorded below; this is not a claim that
video delivery was interruption-free.

This is a separate 15-minute run following the
[five-minute implementation check](../clock-stability-2026-10-30/README.md).
No product code, clock threshold, network configuration, or radio settings
were changed for this extension. Only the observational duration and harness
timeouts were extended. The existing **2 ms** measurement criterion was kept.

## Results

Observation lasted **900.727 seconds**, from **2026-10-30 00:28:49 to
00:43:49 KST** (2026-10-29 15:28:49–15:43:49 UTC). It began after five
consecutive numeric UI checks. The actual base HTTPS dashboard decoded five
physical camera feeds while both real XBee links and the normal base/rover
entry points ran. Development addresses were supplied as session arguments:
base `192.168.0.55`, rover `192.168.0.57`. Actuator/GNSS/localization/antenna
features were disabled as in the preceding five-minute check. No feed or
motion commands were sent, and Internet connectivity remained intact.

| Check | Result |
| --- | --- |
| Qualified absolute-latency UI samples | 900 / 900 |
| Clock sync UI samples | 0 / 900 |
| Reported clock uncertainty | 1.185–1.855 ms; median 1.421 ms |
| Chrony estimated absolute system correction | 0–0.140 ms; median 0.011 ms |
| Clock diagnostic states, internal only | 339 fresh, 111 holdover; 0 invalid |
| Largest reported tracking update interval | 45.0 seconds |
| Base / rover clock epochs | One unchanged epoch each during observation |
| One-second packet-delay medians displayed | 2.3–42.7 ms; median of values 4.4 ms |
| One-second P95 values displayed | 7.2–183.3 ms; median of values 16.05 ms |
| Min since start at the end | 0.3 ms |
| Actual sample spacing | 0.971–1.028 s; median 1.000 s |
| Browser exceptions / layout failures | 0 / 0 |

Qualified holdover sustained measurement through tracking-update gaps as
long as 45 seconds, exceeding the former fixed 20-second freshness cutoff.
The bound remained below 2 ms and the UI continued to display measurements.
These are chrony's estimates, without an independent reference-clock check.

| Feed | Mean decoded FPS | Samples with no frame-counter increase |
| --- | --- | --- |
| Front Left | 30.00 | 0 |
| Front Right | 30.00 | 0 |
| Left | 29.73 | 2, at approximately 22 s and 636 s |
| RGB-D | 29.70 | 2, at approximately 22 s and 635 s |
| Right | 29.82 | 0 |

Each no-progress event spans approximately one second between observations;
the affected feed resumed by the following sample. The early events coincide
with a resolution increase, and the later events with a resolution reduction
around a delay/queue burst. This is evidence of correlation, not proof of
the exact cause of each pause. There was no sustained feed outage, gateway
restart, or browser exception during observation.

The largest displayed median, **42.7 ms**, occurred near 637 s, with P95
139.3 ms in that bin. The largest displayed P95, **183.3 ms**, occurred near
853 s, with median 7.3 ms in that bin. Thus clock stability passed, while
transient video/queue behavior remains a separate limitation.

Delay covers **rover RTP send after pacing → base receive before jitter
buffering**. It excludes capture, encoding, sender pacing and browser display.
The statistics summarize displayed one-second bins; 16.05 ms is not a pooled
15-minute packet P95, and the 0.3 ms minimum is not a submillisecond clock
accuracy claim.

## Verification and cleanup

Both layouts (1100 and 1440 pixels) contained the latency row without overflow.
Source hashes were checked against the preceding five-minute run. Runtime
code was unchanged, so unit/build checks were not repeated for this extension.

The supervisor completed normally and stopped its rover, gateway, receiver
and headless-browser processes. Both clock policies returned to `locked:
false` with no policy error. Ports 8081, 18501 and 9090 were free. Shutdown
SIGINT/exit `-2` messages in the logs are from that controlled cleanup.
The unconnected drone Rocket management address was unreachable; base/rover
radio polling remained available.

Evidence: [summary.json](summary.json), [details.json](details.json),
[live/result.json](live/result.json), [live/rover.log](live/rover.log),
[live/base.log](live/base.log), [checks/cleanup.json](checks/cleanup.json),
and the observational scripts in `harness/`. Raw data preserves the short
rendering gaps and latency peaks as well as the passing clock observations.

This establishes the measured 15-minute behavior on the current development
network. Cold boot, longer thermal cycles and degraded field RF conditions
remain outside this run's scope.
