# Two-host latency acceptance — 2026-09-18

The integrated flat-rover diagnostics were deployed and exercised on the actual
Jetson rover and base station over their Ethernet router network. All four USB
cameras were real. Uplink trials completed through the deployed HTTPS dashboard,
including browser rendering, both packet captures, rover artifact upload, and
report generation. Manual and Orbit downlink trials traversed the physical XBee
serial devices and reached the rover command-publication trace.

## Tested arrangement

| Role | Host | Interface | Checkout / runtime |
| --- | --- | --- | --- |
| Rover | `rover`, `192.168.0.60`, aarch64 | `enP8p1s0` | `/home/mr2/mr2-stack`, ROS Humble, domain 87 |
| Base | `base`, `192.168.0.62`, x86_64 | `enp2s0` | `/home/mr2/mr2-stack`, branch `deploy/flat-latency`, Node 24.15.0 |
| Browser | Headless Chromium on **base** | HTTPS nginx on base | Playwright, actual WebCodecs software decoding |

The base previously ran checkout `a690d89`; integration `be332c8` was installed
first, followed by the deployment fixes accompanying this report. Both checkouts
are synchronized through a Git bundle. The final full revision and running
source revision are recorded in the generated preflight reports, not hard-coded
into this self-referencing document. No branch was pushed to a remote.

The rover's `flat` profile sends `flat_cam_1..4` on UDP 5010/5012/5014/5016.
Each camera captures 640×360 at 30 fps and encodes 320×180 H.264. The gateway
loads `video_streams_flat.json`; its four encoded dimensions were verified live.

Chrony on base is the local reference (stratum 8); rover synchronizes to
`192.168.0.62` (stratum 9). The observed rover error bound during the accepted
trials was about 0.05–0.07 ms. This is relative synchronization, not proof of UTC
accuracy. The browser/base clock offset was checked before and after trials.

Motor and GNSS nodes were disabled throughout. Live ROS discovery showed only
`latency_uplink_agent`, `rosbridge_websocket`, `system_status`, `video_streaming`,
and `xbee_bridge`. CAN was down. Controller inputs below were synthesized in the
browser; no physical gamepad or motor response was tested.

## Accepted live results

Each accepted uplink trial requested a five-second capture. These short runs
validate the workflow, not sustained throughput or a load-scaling benchmark.
Values below are milliseconds; report JSON distributions use microseconds.

| Feeds | Matched rendered frames | RTP rover→base p50 | RTP rover→render p50 | RTP rover→render p95 | Warnings |
| --- | ---: | ---: | ---: | ---: | --- |
| 1 | 122 | 0.099 | 71.915 | 74.046 | none |
| 2 | 244 | 0.098 | 71.488 | 73.499 | none |
| 4 | 485 (121/121/122/121) | 0.101 | 71.920 | 75.466 | none |

All selected streams reported 0% matched-window packet loss, both tcpdump
processes exited successfully with zero kernel drops, and rover metadata reported
`stream_lease_acquired: true`. Unselected cameras therefore stopped transmitting
during the selected-load measurements. The four-feed per-camera total p50s were
72.226, 71.762, 71.984, and 71.357 ms. These distributions measure the same
rendered frames in each segment, verified against their actual VCL contents.

Trial IDs and artifact subdirectories:

- 1: `100dc208-5893-4f5c-821d-13370566d3b6`, `uplink-g9Xkze`
- 2: `f3a01ab7-d908-4bbf-ad19-325e7b9efc3e`, `uplink-i5nv3l`
- 4: `fcb3fc5f-36e8-4ca6-913b-8a9d875c1f70`, `uplink-p0M1Ix`

Cancel during capture restored all four feeds; another one-feed trial completed
with matched rendered frames (see `cancel-results.json`). Earlier browser-warmup and capture-file failures also
returned the camera streams to their previous selection. During extended testing one camera process remained alive but stopped sending
UDP; restarting the camera launch restored it. This was not an automatic
stalled-camera recovery test. No deliberate long-duration timeout or reboot
acceptance test was performed.

Downlink final accepted trials:

| Mode | Input→gateway | Gateway→rover XBee receive | Receive→publish | Total | Browser clock drift |
| --- | ---: | ---: | ---: | ---: | ---: |
| Manual | 5.2 | 158.8 | 0.1 | 164.0 | +0.65 |
| Orbit | 22.1 | 216.6 | 0.1 | 238.8 | −0.17 |

Both reached `Measurement complete` without a metrics warning. Mode switching
and simulated controller disconnection each cancelled an armed measurement.
Downlink JSONL export contains the trace events and the completed measurements.
These are individual input trials, not latency percentiles.

## Problems found and fixed

1. The RTP tee's two `fdsink` branches could stall waiting for preroll when
   joining ongoing camera traffic. Both now use `async=false`. A synthetic
   four-stream late-join check waits 1.5 seconds before starting receivers,
   deliberately misses the startup keyframe, and passes with correlated chunks
   on all four streams.
2. `prefer-hardware` caused WebCodecs to reject an otherwise supported H.264
   stream on the base's headless browser. `no-preference` allows the browser to
   select software decoding when hardware is unavailable. Real rendering and
   the accepted measurements above validate this fallback.
3. `rover_real.launch.py` owned GNSS processes outside the nested stack and did
   not honor `enable_gnss:=false` for those processes. It now gates both receivers
   and relays, and scopes the nested launch's GNSS override. Profile composition
   tests (24 cases) and the live node list validate the change.
4. Ubuntu base's tcpdump AppArmor profile denied capture writes under
   `~/.local/state`. Its artifact directory was moved to
   `/home/mr2/latency-router-20260918/artifacts`; AppArmor remains enabled.
5. The Python chrony parser used by preflight rejected the base local reference's
   empty hostname (`7F7F0101 ()`). It now accepts the same format as the gateway's
   parser; a regression test covers local references with and without parentheses.

6. A late-join regression proved that FIFO pairing of the tee's two outputs
   could assign an earlier RTP frame to a later rendered access unit. Timing now
   requires matching SHA-256 fingerprints of the unchanged H.264 VCL bytes,
   reconstructed from single NAL/STAP-A/FU-A packets. Packet loss, unsupported
   packetization, and incomplete fragments receive no correlation. Unmatched
   frames do not block later verified frames, and stopping a receiver cancels
   pending correlation work. The packet reconstruction follows
   [RFC 6184 sections 5.6–5.8](https://www.rfc-editor.org/rfc/rfc6184.html).
   Initial FIFO-based uplink measurements are **superseded**, including the first
   488-frame result. The accepted table uses the repeated content-matched trials.

Gateway unit tests: 81 passed. Dashboard latency tests, TypeScript, lint, and
production build passed on the x86 base. Rover's six changed ROS packages built
successfully. Synthetic normal and late-join video checks passed. Final
point-in-time deployment agreement is checked separately with `preflight.py`
and its base/rover report comparison.

## Installed state and operating the test setup

The deployed dashboard is at `https://192.168.0.62` (existing self-signed TLS).
Nginx serves `/var/www/mr2-dashboard`, proxies rosbridge to `.60:9090`, and
accepts artifact uploads on HTTP `/latency/uplink/`. The gateway is on the
existing loopback port 8081. The base's ignored `.env.local` now selects flat
video, enables diagnostics, and points captures/uploads to the actual router
interfaces and IPs. Unrelated environment settings and the base's pre-existing
untracked file were preserved.

The test processes are running as **transient user services**, not new boot
services. Chrony and the nginx configuration are persistent. On the corresponding
host, stop a test service with:

```bash
# Rover (keeps the motor disabled)
systemctl --user stop mr2-latency-router-rover
# Base
systemctl --user stop mr2-latency-router-gateway
```

The exact launch scripts are retained on each host under
`/home/mr2/.local/state/mr2/latency-router-20260918/`: `start-rover.bash` on rover
and `start-gateway.bash` on base. After stopping any conflicting normal launch,
a transient service can be recreated after reboot with:

```bash
# On the rover
systemd-run --user --unit=mr2-latency-router-rover \
  /home/mr2/.local/state/mr2/latency-router-20260918/start-rover.bash
# On the base
systemd-run --user --unit=mr2-latency-router-gateway \
  /home/mr2/.local/state/mr2/latency-router-20260918/start-gateway.bash
```

Do not use this test launcher as a driving configuration: it intentionally keeps
the motor disabled. For normal operation, merge the documented settings into
the normal launcher and choose the required motor/GNSS flags explicitly.

Base backups: `/var/backups/mr2-latency-router-20260918/` contains previous nginx
and dashboard files; the state directory above contains ignored environment
backups and `previous-commit`. Chrony backups are
`/etc/chrony/chrony.conf.mr2-backup-20260918T044637Z` on base and
`/etc/chrony/chrony.conf.mr2-backup-20260918T044753Z` on rover. Installing chrony
replaced systemd-timesyncd. A rollback must restore configuration and rebuild
for the previous checkout, rather than only changing Git HEAD.

Evidence on rover: `log/latency-router-20260918/` (Git-ignored). The collected
archive contains accepted reports, packet captures and metadata, browser test
scripts, screenshots, JSONL, and final preflight reports. Failed early trials
remain in the base's old state artifact directory for diagnosis. Credentials and
environment files are excluded from the evidence package.

## What this proves

The two-host implementation and deployment work over the actual router path.
It does **not** establish the latency, loss, or jitter of a Rocket M2 antenna
link. The stored report field `rocket_m2` names the rover/base RTP segment; in
these runs it measures Ethernet/router transport. Its roughly 0.1 ms median is
close to the observed clock uncertainty and should not be treated as a precise
radio result.

The browser ran on base, so the base→browser segment excludes an external
operator laptop's network path and uses software decoding. Uplink starts at
rover RTP capture after camera capture/encoding and ends at canvas draw in the
browser, not physical screen presentation. Downlink ends at ROS command
publication, before the motor controller. Repeat these same trials with the
actual antenna link and operator browser before drawing field-performance
conclusions.
