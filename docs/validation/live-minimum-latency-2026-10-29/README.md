# Running minimum and synchronized live measurement — 2026-10-29

Live Feed now shows **Min since start**, the lowest observed Rover → Base
packet delay since the dashboard page started. Only samples observed while
base/rover clock readiness is valid can lower the minimum. The always-mounted
diagnostics panel keeps tracking across mission tabs. Loss of traffic or clock
readiness preserves the previous minimum; reloading the page resets it.
The gateway supplies each second's minimum as input, while the displayed value
accumulates across reports. It is distinct from GCC's packet-size queue floor.

The updated UI and gateway were built, tested and published on the actual base
at 192.168.0.55. All 123 gateway tests, dashboard latency tests, typecheck,
ESLint and production build passed on base. The rover checkout's dashboard
tests and typecheck also passed. Running-minimum coverage checks slower later
values, valid decreases, unsynchronized/invalid/idle samples, retained history
and valid zero. The XBee protocol and allocation controller were unchanged.

## Live measurement

Both real XBee links and five physical camera feeds were used through the
normal rover launch and base gateway. Session arguments selected development
IPs (base .0.55, rover .0.57). Actuator hardware, GNSS, localization and antenna
actuation were disabled for the video/clock test. No camera ON/OFF or priority
commands were sent. A real browser on base loaded the actual deployed HTTPS
dashboard and decoded all five available camera feeds.

The final observation ran **120.687 seconds**, from 2026-10-29 13:51:55.869 UTC
to 13:53:56.558 UTC (22:51:55–22:53:56 KST). It began after five consecutive
numeric UI checks. Of 120 sampled UI states, **96 showed verified absolute
latency and 24 showed Clock sync** due to latest peer-sample freshness/error.
Thus this is not a claim of uninterrupted clock-qualified measurements.

The final UI timeline was 0–63 seconds numeric, 64–87 seconds `Clock sync`,
and 88–119 seconds numeric again. The 96/120 count is a fraction of samples,
not a failure starting after 96 seconds. During the hidden interval, recorded
tracking offsets were approximately 0.004–0.122 ms and estimated error bounds
1.512–1.691 ms; the independent latest-peer-sample gate caused the suppression.

Subsequent inspection of native Chrony logs found a tracking update at
13:52:38 UTC followed by the next update at 13:53:21 UTC, a 43-second gap.
NTP traffic continued in that gap: 518 received packet records had RTTs from
1.760 to 98.680 ms, with only 31 within the session's 3.6 ms delay filter.
Meeting that RTT filter alone is not sufficient for all Chrony validity/filter
tests. The guard rejects a selected peer sample older than 20 seconds; this
matches the UI's delayed suppression and recovery. Logs are preserved in
`clock-interruption/`. The tracking model remaining within its error bound
does not independently prove true clock error; clock/readiness interruptions
still need work before treating continuous absolute latency as reliable.

Ranges below use only those 96 clock-qualified UI samples. Each displayed
P50/P95 describes that report's preceding second; these are not one combined
packet distribution over the entire two-minute run.

| Displayed metric | Range | Median of the displayed second-by-second values |
| --- | --- | --- |
| Rover → Base P50 | 2.5–14.1 ms | 7.25 ms |
| Link P95 | 8.7–72.2 ms | 24.3 ms |
| Extra queue | 0.9–13.0 ms | 6.1 ms |

The accumulated minimum ended at **0.3 ms as displayed**. The clock gate allows
up to 2 ms uncertainty, so this small value is not a guarantee of physical
sub-millisecond transit accuracy. Recorded rover tracking offset magnitude
was at most 0.162376 ms and reported error bound 1.087932–1.690665 ms; latest
sample freshness/error is an additional gate independent of that tracking fit.

The browser confirmed that navigating Status → Live Feed preserved 0.3 ms.
At 1,100 and 1,440 px viewport widths, all four latency fields were visible
and contained within the Network tile without horizontal overflow. There were
no runtime exceptions. The five camera render counts at the end were 3,348
to 3,625 frames each. These browser-render counts are not source FPS limits.

## Clock preparation and cleanup

Before video startup, a temporary clock-policy lease locked both hosts to base
and selected 192.168.0.55 for this session only. Chrony's runtime source options
were adjusted to interleaved mode, kernel timestamps, a four-sample filter and
more frequent local polling. The final settings used minpoll/maxpoll -4 and
maxdelay 0.0036 s, rejecting late NTP samples rather than loosening the existing
2 ms UI accuracy gates. No manual time step was issued. See the
[Chrony source options](https://chrony-project.org/doc/4.2/chrony.conf.html).

An earlier browser on rover added return video traffic; the final browser ran
on base to avoid that extra radio/network load. One previous complete run with
slower NTP sampling qualified only 38/120 UI samples, recorded separately in
`first-complete-result.json`. No claim is made that the final tuning eliminates
all freshness failures; 24 remained in the final run.

The tuning was **runtime-only**. No field-default addresses, manager config,
Internet routes, interfaces, firewall or persistent clock profile was changed.
After the run, both test-launched applications, receivers and browser stopped,
ports 8081 and 18497 were released, and clock leases cleared on both hosts.
The clock manager restored its normal idle profile, including the original
field server and public sources. Subsequent normal launches use the existing
persistent clock policy, not these temporary polling options.

Evidence: `result.json`, `summary.json`, `clock-samples.json`, preparation log,
test/build logs, native rover/base logs and real dashboard screenshots.
