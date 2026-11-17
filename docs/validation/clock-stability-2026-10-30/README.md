# Clock stability implementation and first five-minute check

The deployed implementation passed its initial **300-second live check**:
all **300 / 300** one-second dashboard samples displayed verified absolute
latency, with **0 Clock sync samples**. This is a short operational check on
the development network, not long-duration or field RF qualification.

## Implemented behavior

- Both hosts mark NTP with **DSCP 46**. Captured requests and responses had
  TOS `0xb8`. Both Rocket M2 units reported WMM enabled. The airOS 6 guide
  maps DSCP 46–47 to its Voice queue; this verifies the configuration and
  packet marking, not a guaranteed RF airtime reservation.
- Local chrony sources use `minpoll -3 maxpoll -1 xleave extfield F323
  filter 4 maxdelay 0.25 maxdelayratio 2`, with `maxclockerror 10`.
  Both computers run chrony 4.2. Relative delay rejection replaces the
  previous experimental fixed 3.6 ms cutoff, which starved the estimator.
- Transient peer/lease connectivity changes use chronyc online/offline and
  preserve the running estimator. A failed renewal may retain the last
  acknowledged lease only until its existing 20-second safety margin.
  Switching between idle and locked constraints still restarts chrony.
- A good tracking fit establishes an uncertainty anchor. Thereafter, the
  estimate is the larger of chrony's current bound and the anchor bound
  plus elapsed monotonic time multiplied by frequency uncertainty. The
  frequency uncertainty is at least 10 ppm and grows with reported skew
  and residual frequency. Thus a missing fresh packet does not immediately
  discard a usable clock, and holdover cannot continue indefinitely.
- The bound includes **abs(system correction) + root dispersion + half root
  delay**. Tracking already includes sample age; that age is not added twice.
  The existing **2 ms** measurement threshold remains unchanged. Three policy
  observations are required before readiness is asserted.
- Base clock epochs accompany lease acknowledgements. A changed base epoch,
  a detected wall-clock jump, an expired lease, or excessive uncertainty
  invalidates absolute measurements until qualified again. The browser also
  checks the base epoch and ages the uncertainty of received diagnostics.
- Before the first real launch, a qualified correction of at most 50 ms may
  be stepped **before ROS nodes start**. Renewals and overlapping launches do
  not step time. Then the launch can wait up to **90 seconds** without camera
  load for additional uncertainty headroom (1.2 ms and drift at most 15 ppm).
  Included launches share this wait. If headroom is not reached, startup
  proceeds and normal 2 ms measurement gating continues. Initial acquisition
  can add approximately another 10 seconds. There is no new launch flag.
- The Live Feed failure text and tooltip use only **Clock sync**. Internal
  fresh/holdover/invalid states add no new dashboard indicators. Clock
  readiness does not stop video or GCC, which uses monotonic delay changes.

Field defaults are unchanged: base `192.168.1.101`, rover `192.168.1.102`,
Rocket management `192.168.1.100` / `192.168.1.106`. Development IPs below
were supplied as launch arguments. No IP addresses, routes, firewall rules,
radio configuration, or Internet connectivity were changed.

## Actual live test

The final observation ran **300.677 seconds**, from **2026-10-30
00:14:11 to 00:19:12 KST** (2026-10-29 15:14:11–15:19:12 UTC).
It started after five consecutive numeric UI checks. The startup wait reached
its 90-second headroom deadline; the ordinary 2 ms criterion was satisfied
when observation began. Both real XBee links and five physical cameras were
active. A browser on base loaded the actual deployed HTTPS dashboard and
decoded the feeds, avoiding a second video copy across the test link.

| Check | Result |
| --- | --- |
| Numeric absolute-latency UI samples | 300 / 300 |
| Clock sync UI samples | 0 / 300 |
| Chrony estimated absolute system correction | 0–0.106 ms; median 0.021 ms |
| Reported uncertainty bound | 1.361–1.896 ms; median 1.613 ms |
| One-second packet-delay medians displayed | 3.0–13.4 ms; median of those values 5.5 ms |
| One-second P95 values displayed | 10.0–79.8 ms; median of those values 19.3 ms |
| Min since start at the end | 0.4 ms |
| Clock diagnostic states, internal only | 106 fresh, 44 holdover; 0 invalid |
| Browser exceptions / failed layout checks | 0 / 0 |

The largest reported chrony tracking update interval was **30.4 seconds**;
qualified holdover kept measurements available through those gaps.

Each of the five feeds advanced its decoded frame counter at every sampled
second. Mean decoded rates over the observation were 30.00 FPS Front Left,
30.00 Front Right, 29.62 RGB-D, 29.73 Left and 29.79 Right. Both 1100-pixel
and 1440-pixel layouts contained the latency row without overflow.

Latency here means **rover RTP send after pacing → base receive before jitter
buffering**, not camera-to-screen latency. The statistics above summarize
displayed one-second bins; 19.3 ms is not a pooled five-minute packet P95.
The 0.4 ms minimum is an observation under the same clock qualification,
not a claim of submillisecond clock accuracy. No independent reference clock
was used to validate chrony's estimates or link asymmetry.

Normal entry points were used, with development addresses and actuator
features disabled for the test:

```bash
# Base: same wrapper used by the usual npm start path
bash scripts/start_chrony_base.sh \
  --rover-host 192.168.0.57 --antenna-enable false

# Rover: from the sourced ROS workspace
ros2 launch mr2_launch rover_real.launch.py \
  enable_manipulator_module:=false enable_xbee_sim:=false \
  video_base_host:=192.168.0.55 \
  enable_rover_hardware:=false enable_gnss:=false \
  enable_localization:=false enable_led:=false enable_camera_turret:=false
```

No feed ON/OFF, priority, motion, or antenna commands were sent. The locked
clock profiles had no public time sources; the Internet connection remained
available for the agent. Read-only Internet time probes never set either
locked clock. This does not constitute a physical Internet-disconnection test.

## Deployment and verification

Policy source was installed in `/usr/local/lib/mr2-clock/` on both computers;
installed policy/service hashes match the checkouts. The actual base gateway
and published dashboard include the new epoch/uncertainty handling. Rover
packages `mr2_launch` and `mr2_system_status` were rebuilt with symlink
installation, and Python's resolved imports were checked against source.

During investigation, an older copied `clock_guard.py` in the ROS installation
prevented the new startup preparation from running. This was corrected before
the accepted run. Four preliminary five-minute runs displayed numeric latency
only 19, 93, 4 and 104 times out of 300. Those runs used intermediate policies
and lacked the new startup guard; they were not accepted as validation of the
complete change. Their counts are retained in `intermediate-runs.json`.

Checks passed: **25 clock-policy tests**, **11 ROS launch/status tests**
(including one-time warmup and timeout behavior), **11 field/preflight tests**,
**123 gateway tests on base**, dashboard latency tests, TypeScript, ESLint and
production builds. After the live run, only the warmup timeout log text was
clarified; behavior is unchanged and launch/status tests passed again.

Runtime rollback backups for this revision are
`/var/backups/mr2-clock/stability-20261029T145506` on rover and
`/var/backups/mr2-clock/stability-20261029T145508` on base. They preserve the
previous installed policy/service and host clock configuration. Host config
and SSH credentials were not rewritten. Stop all ROS launches and wait for
leases to release before changing installed clock policy.

After the test, the launched rover/base/receiver/browser processes were stopped.
Both clock policies returned to `locked: false` without errors. Gateway port
8081, browser debugging port 18501 and rover rosbridge port 9090 were free.
The normal clock services and dashboard web server remain running.

Evidence: [summary.json](summary.json), [live/result.json](live/result.json),
[live/rover.log](live/rover.log), [live/base.log](live/base.log),
[live/live-end.png](live/live-end.png), and the `checks/` logs. The live JSON
retains the actual UI values and clock diagnostics, including qualified
holdover observations. The observational scripts are under `harness/`.
Packet marking evidence is in `checks/ntp-dscp.log`, and the read-only radio
settings are in `checks/radio-read-current.log`.

Long-duration thermal drift, cold boot, and field RF degradation/recovery have
not been qualified by this five-minute test. Both network interfaces expose
software timestamping and no PTP hardware clock; this implementation does not
add GNSS PPS or hardware timestamp support.

References: [chrony 4.2 configuration](https://chrony-project.org/doc/4.2/chrony.conf.html),
[chronyc tracking interpretation](https://chrony-project.org/doc/4.2/chronyc.html),
[airOS 6 guide](https://dl.ui.com/guides/airOS/airOS_6_UG.pdf).
