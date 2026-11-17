# Live Feed latency telemetry — 2026-10-29

The Network / Rocket M2 tile now displays median Rover → Base packet delay,
Link P95 packet delay, and median Extra queue in milliseconds over the previous
second. All video streams contribute observed media packets; duplicate and
probe packets are excluded. Network delay uses real sender/receiver epoch
timestamps already present in RTP observations. No new radio messages, capture
policy, congestion decisions or packet timestamp boundaries are introduced.

Absolute values are gated by the existing base/rover Chrony readiness checks.
Missing/stale/unlocked/unsynchronized clocks display `Clock sync` while traffic
is present. Extra queue uses monotonic differences against the existing size
bucket baseline and remains valid without epoch synchronization. Invalid,
missing, negative or expired delay samples display `—`. Idle or disconnected
telemetry also displays dashes. The packet-send-to-packet-receive scope is named
in the UI and explained in its tooltip; this is not camera-to-screen latency.

## Validation

- All **123 gateway tests** passed, including new delay distribution,
  duplicate/probe exclusion, legacy/invalid timestamp, expiration and unchanged
  XBee feedback-size coverage.
- Dashboard latency tests passed, including readiness gating, invalid numeric
  data, missing metrics, idle traffic and independent relative-queue display.
- Dashboard TypeScript, ESLint and production build passed. Vite retains its
  existing large-bundle advisory.
- The actual installed C++ sender/receiver and production JS receiver/control
  ran a local UDP test with **75 ms intentionally added transit**, 2,176 packet
  observations and 240 H.264 access units. Across eight reports, median delay
  was 75.273–75.627 ms and P95 75.959–79.165 ms. The extra-queue median stayed
  around 0.64–0.84 ms, demonstrating the distinction between constant absolute
  transit and additional queueing. This is a controlled loopback test, not a
  Rocket M2 range/clock acceptance measurement.
- A real ARM64 headless Chromium loaded the production dashboard bundle with
  isolated local fixture HTTP/WebSocket/ROS endpoints. Ready, unready,
  recovered, idle, resumed and stale telemetry states all passed. Eight layout
  combinations (1,100/1,280/1,440/1,920 px widths and 16/20 px fonts) showed no
  overflow or overlap with the adjacent RGBD tile. Screenshots are fixtures,
  not field latency measurements. No runtime exceptions were reported.

The test servers and native children were stopped and their ports released.
All traffic stayed on loopback. No host network, radio, clock policy or live
vehicle launch was changed.

## Deployment status

The initial implementation could not reach the base, so its source and public
dashboard remained on the previous version. After the operator reported the
missing labels, base `192.168.0.55` became reachable again with the pinned SSH
host identity. Comparing its sources and served HTML confirmed that deployment
had been pending; the absence of labels was not a clock-readiness condition.

The latency UI, gateway metrics, tests and documentation were copied to base.
The subsequent `wait-clock` -> `clock` CLI rename was also synchronized. Existing
files and the previously served index were backed up under base's
`~/.local/state/mr2-latency-ui-deploy.q46y11/`. All 123 gateway tests, dashboard
latency tests, dashboard typecheck/lint/build and five field-setup tests passed
on base. The production assets were published to `/var/www/mr2-dashboard`, with
the index replaced after asset copying and old hashed assets preserved.

A fresh real Chromium loaded **the actual base HTTPS page**, not the earlier
fixture server. It fetched `/assets/index-DyvzSrJk.js` and displayed all three
labels: `Rover → Base`, `Link P95`, `Extra queue`. The served JS matched the
built artifact's SHA-256. At 1,100 and 1,440 px viewport widths, the new row was
visible, contained in its tile and had no horizontal overflow. There were no
runtime exceptions. Evidence is in the `base-deploy/` subdirectory.

The base gateway was not listening on port 8081 during this check; the displayed
values were consequently `—`, not new radio latency measurements. No gateway
or vehicle launch was started by the deployment. The next normal `npm start`
loads the updated metrics code; the browser needs a refresh to load the updated
UI. Field-network latency validation remains separate. No network, Nginx or
clock-policy configuration was changed.

Included evidence: gateway/dashboard test logs, browser state/layout result,
controlled UDP results, browser fixture runner and UDP runner, and screenshots.

Subsequent work added a clock-qualified page-lifetime **Min since start** and
ran both actual systems for a synchronized live check. See
[running minimum and live measurement](../live-minimum-latency-2026-10-29/README.md)
for the final measurements and the remaining intermittent clock-readiness limit.
