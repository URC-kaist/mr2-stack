# Latency diagnostics integration assessment — 2026-09-18

This is the historical pre-integration assessment. The implementation is now
integrated; use [the deployment handoff](flat-latency-handoff.md) for the resulting
behavior, verification, and remaining host-level acceptance.

Integration is feasible. A trial merge builds and passes the software checks
below after resolving two dashboard files. Deployment still needs topology-aware
configuration and live video/command verification.

## Compared versions

- Current implementation: `2214b55b339b7f7011fce727866bf405a2a4ec99`
  on `2026-nepo-demo`, including flat profiles, `mr2_flat_motor`, `mr2_control`,
  actuator feedback fixes, dashboard changes, and operating documentation.
- Diagnostics: `feature/chrony-latency-diagnostics` at
  `371b12f29a54e409251f897ac4d1380cc7fec695`; fetching origin confirmed the same tip.
- Common ancestor: `5f04032484cd0c5a37b27c039c6a425aaaf0fdf3`.
- Incoming commits: `e188908`, `dba44c6`, `a690d89`, `371b12f`.
  The feature changes 114 files relative to the common ancestor.

The trial used a detached temporary worktree. No diagnostics implementation was
merged into `2026-nepo-demo`, pushed, or deployed. No motor commands, live capture,
clock changes, nginx reloads, or service restarts were performed.

## Compatibility and necessary work

| Area | Finding and integration requirement |
| --- | --- |
| Git merge | Conflicts only in `dashboard/src/components/ControlPanel.tsx` and `dashboard/src/components/ControlPanel/ControlPanel.css`. Launch files and package dependencies merged automatically. |
| Manual/orbit controls | Preserve the current orbit logic, types, command refs, and scrolling styles. Add the diagnostics panel and trace propagation. The trial placed `observeControlInput(next)` in shared `updateCommand`, covering both modes; actual input, mode-switch, zero-command, and cancellation behavior still need verification. |
| `mr2_control` | Its manual topic remains `/base/cmd_vel`. The feature instruments the upstream XBee bridge and does not replace the actuator runtime. The control loop uses `steady_clock`; cross-host diagnostic timestamps use system time. Keep these clock domains separate. |
| Flat rover | `mr2_flat_motor` also consumes `/base/cmd_vel`. Keep hardware disabled, flat motor enabled, and bridge smoothing disabled as the profile specifies. Uplink stream IDs/ports are configuration-driven; both gateway and rover must receive `video_streams_flat.json`. |
| Base-station operation | Fits the branch's two-host model: chrony synchronization, capture on the two actual Rocket-facing interfaces, and a rover-reachable artifact upload URL. |
| Direct operation | Gateway and rover share the Jetson, video targets loopback, and commands use a PTY. The nested diagnostics flag reaches the rover launch in static composition checks. A direct measurement needs local capture configuration and labels; its host-internal interval must not be presented as Rocket radio latency. Do not apply the two-host chrony configuration to one host unchanged. |
| Runtime configuration | The incoming `base/gateway/env.local` contains a machine-specific NIC/IP, but the loader reads `.env` and `.env.local`, not `env.local`. Move appropriate values into supported local configuration or environment variables; do not assume that incoming file enables diagnostics. |
| Dependencies and routing | Build/install `mr2_latency_msgs`, `mr2_latency_diagnostics`, and the modified video/XBee/system-status packages. Configure chrony, tcpdump capture permission, gateway diagnostic enablement, and `/latency/*` nginx routes. The HTTP artifact path must avoid the existing HTTPS redirect/self-signed-certificate failure described by the branch. |
| Video regression scope | The gateway receiver's RTP marker branch and timed browser chunks affect normal playback even when an uplink trial is inactive. Verify all four flat cameras with diagnostics both off and on, including loss/restarts and stream-lease restoration. `rtpstreampay` is available on this Jetson. |

## What the measurements include

- Downlink: browser control input → gateway receipt → rover bridge receipt →
  `/base/cmd_vel` publication. This does **not** measure `mr2_control` processing,
  flat motor acceleration limiting, CAN transmission, or physical motor response.
  Those require additional correlated events beyond the imported feature.
- Uplink: rover RTP marker capture → base marker capture → browser receipt →
  canvas draw completion. It excludes camera exposure/encoding before the rover
  capture and physical display scanout after drawing.
- Two-host readiness requires fresh clock samples, rover offset within 2 ms,
  and the branch's chrony error bound within 2 ms. This is a readiness threshold,
  not a guarantee of sub-millisecond measurement accuracy.

## Checks performed

Before committing the current implementation:

- Flat motor, profile, and control-launch Python tests: **53 passed**.
- Dashboard `npm run check`: typecheck, lint, and production build passed.
- Staged whitespace check passed. Local logs, root install outputs, and Python
  caches were excluded from the commit and retained on disk.

On the trial merge after dashboard conflict resolution:

- Gateway `npm test`: **71 passed**.
- Dashboard `npm run test:latency`: all nine imported test modules passed.
- Dashboard `npm run check`: typecheck, lint, and production build passed.
- Python RTP/chrony/agent/profile/flat-motor/control-launch suites: **81 passed**.
  The temporary worktree lacks the ignored deployment `.env`; initial profile
  checks failed for missing `MR2_BASE_IP`. Providing the documentation address
  `192.0.2.1` for static tests resolved those failures without network access.
- Five ROS packages built successfully in a temporary overlay using the existing
  workspace underlay: `mr2_latency_msgs`, `mr2_latency_diagnostics`,
  `mr2_system_status`, `mr2_xbee_bridge`, `mr2_video_streaming`.
- Additional static flat-profile composition: diagnostics off/on across
  `rover.launch.py`, `rover_real.launch.py`, and `rover_direct.launch.py`
  (**six combinations**). Agent inclusion matched the flag; flat motor enablement,
  original hardware exclusion, and smoothing defaults were preserved.
- Incoming `scripts/start_chrony_base.sh` has an extra blank line at EOF flagged
  by `git diff --check`; normalize it during integration.

These checks do not constitute a full rover rebuild, browser/controller smoke
test, physical CAN test, or a measured two-host latency experiment. The current
`mr2_control` C++ suite was not rebuilt/rerun for this assessment. Both dashboard
builds retain the existing large-bundle warning.

## Recommended integration sequence

1. Create an integration branch from the committed current state and merge the
   complete feature tip, retaining all four dependent feature commits.
2. Resolve the two dashboard files as above; verify manual/orbit input tracing,
   neutral gating, mode switches, and cancellation.
3. Supply correct flat video configuration and supported local environment
   settings. Explicitly choose base-station or direct measurement semantics.
4. Repeat the software checks and perform video playback/lease/cancel smoke tests
   before a two-host synchronized capture. Extend instrumentation separately if
   the desired endpoint is CAN transmission or actual motor response.
