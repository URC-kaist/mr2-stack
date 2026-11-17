# Offline clock and ROS launch policy

Latest stability update (2026-10-30): bounded holdover, NTP packet priority,
clock epoch checks and launch-time preparation are deployed on both hosts.
The initial five-minute live check showed verified latency in all 300 sampled
UI states. A subsequent continuous [15-minute check](validation/clock-stability-15m-2026-10-30/README.md)
showed verified latency in all 900 samples, also without a Clock sync sample;
short video rendering gaps and delay spikes are recorded separately.
See [implementation, results and limits](validation/clock-stability-2026-10-30/README.md). This does not yet
qualify long-duration or cold-boot stability.

Earlier runtime audit (2026-10-29): the Internet-free local NTP/video/control
paths passed isolated tests. Actual base/rover synchronization worked without
public clock sources while locked, but continuous precision within the existing
2 ms measurement criteria did **not** pass on the current development LAN.
See [the audit and evidence](validation/offline-runtime-2026-10-29/README.md).
Allow idle synchronization before starting real ROS; synchronized status alone
is insufficient for a precise one-way latency claim.

## Agreed behavior

The field network is normally offline. Base is `192.168.1.101`, rover is
`192.168.1.102`; `192.168.1.100` is the base Rocket management address, not an
assumed gateway. Internet availability normally applies to the whole network.
Development addresses are additional, explicitly configured candidates.

Field source selection (2026-09-22): rover configuration now includes
`field_network` (`interface`, `local_address`, `base_host`). While that local
Ethernet address is present and ROS is idle, rover uses only the field base,
with pre-launch stepping allowed. This prevents Internet NTP on a second Wi-Fi
interface from rejecting an offline base as a falseticker. The base remains
responsible for Internet UTC recovery when available. Removing the field address
automatically restores the rover's normal idle Internet/base sources. Locked
profiles and the UTC hold during ROS are unchanged.

Before/after a real ROS launch, chrony may recover UTC from Internet NTP.
For the entire lifetime of the ROS launch (including stops), external clock
correction is disabled on both hosts. The first guard may apply a bounded,
qualified correction from the locked base before any ROS node starts. After
that preparation, rover may only slew gently toward the locked base.
There is no motor-command/first-motion trigger. Multiple launches
hold independent locks; the last exit releases them. Simulation is excluded.

## Implementation plan

1. Install a small root-owned clock policy service and validated chrony profiles
   on both hosts. Keep chrony as the only clock-adjustment daemon. Back up host
   configuration before installation; do not change IP addresses or routing.
2. Idle profiles use Internet pools and (on rover) preferred base candidates.
   Base retains a local stratum-8 reference offline. Locked profiles have no
   Internet sources, no stepping, a 50 ms maximum accepted correction, and a
   100 ppm maximum slew rate. Large errors block measurements, not robot use.
3. Acquire the rover lock synchronously before real launch actions start. Track
   the launch PID and its process start time, persist locks across manager
   restarts, and clean up after normal exit or process death. Preserve locks
   until launch children also exit after an abnormal parent termination.
4. Coordinate the base lock with a restricted SSH key, pinned host identity,
   and renewable monotonic leases. Try field address before configured
   development candidates. Never store account passwords. A missing base does
   not prevent standalone operation. Never enable a rover NTP source until that
   base acknowledges its lock. Transient renewal failures retain only the last
   acknowledged lease; disable the source before its 20-second expiry margin.
5. Keep boot-time disk clock recovery. Probe Internet time without setting the
   clock while locked; expose probe availability/offset separately from UTC
   validity and relative synchronization. Offline UTC accuracy is not promised.
6. Add policy status to both existing clock diagnostics and the dashboard.
   Readiness requires fresh policy, both locks, the selected base identity, and
   the existing 2 ms offset/error limits. Never label a local reference UTC-valid.
7. Test profile generation, PID reuse/death, overlapping launches, lease expiry,
   partial connectivity, manager restart, source selection, and readiness.
   Deploy base first, then rover; verify actual SSH lease and chrony selection
   with hardware-free launch checks. Do not start motors or reboot for testing.

## Acceptance and rollback

Verify idle Internet recovery where UDP NTP is reachable; locked profiles must
contain no public sources and no step directive. Test normal/abnormal ROS exit,
base loss/reconnection, and expiry with short-lived diagnostic launch processes.
Run existing gateway/dashboard/rover tests and build updated artifacts.
Record live evidence and limitations below. Physical cold-boot and Rocket-radio
tests require a later operational window.

Installer backups live under `/var/backups/mr2-clock/`. To roll back, stop real
ROS launches, stop/disable `mr2-clock.service`, restore the backed-up chrony
configuration, and restart chrony. Remove only the installed clock-policy SSH
key line, not unrelated authorized keys. Restore the matching source/install
artifacts; real launch guards deliberately fail if policy service is missing.

## Progress

- Initial audit: both checkouts at `e57a3e5`; rover worktree clean. Base has an
  unrelated untracked file, which must be preserved.
- Base currently permits development rover `.60`, but actual rover is `.59`.
  Installation will allow the designated field rover and development LAN;
  UDP 123 only, with no remote chronyc command access.

## Implemented and deployed (2026-09-21)

- `scripts/clock_policy/` provides the installer, manager, restricted SSH client,
  and lifecycle tests. Both hosts have enabled `mr2-clock.service` and root-owned
  installed code. Rover uses field `.101` first and development `192.168.0.62`
  as fallback. Base permits field rover `.102` and development `192.168.0.0/24`.
  Neither IP addresses nor routes were changed. If the development base address
  changes, update `base_hosts` in `/etc/mr2-clock/config.json` while idle and
  restart the manager. SSH host-key pinning prevents an address reused by another
  computer from becoming the base lock endpoint.
- Real launch wrappers acquire the guard synchronously. Normal launches require
  no new clock flag. Latency diagnostics still require their existing enable
  options. Starting with no local manager fails clearly before hardware nodes.
- Policy is reported separately from chrony tracking in gateway/ROS diagnostics
  and the rebuilt base dashboard. Uplink capture rechecks the measurement clock
  each second and stops/discards the trial if clock readiness is lost.
- Public NTP recovery was observed on the actual rover (it corrected an existing
  approximately 132-second error before any test ROS launch). During a lock,
  base switched to local stratum 8 and rover selected base at stratum 9, with
  roughly 0.13–0.16 ms reported error bound on the development wired network.
- A hardware-free ROS LaunchService acquired both locks before starting a
  `sleep` child. Restarting the manager preserved the live launch lock. A
  controlled 18-second SSH credential unavailability test removed the rover's
  base time source while retaining its local lock; restoring the credential
  automatically restored the base lease and relative synchronization.
- SIGKILL of the test launch parent left the lock active while its marked child
  survived under PID 1. Terminating that child allowed cleanup. No motors,
  cameras, or vehicle command publishers were started by these tests.
- Python policy/generator/preflight tests: 22 passed. ROS package tests: 40
  passed. Gateway tests: 81 passed on both hosts. Dashboard latency tests,
  TypeScript, lint, and production build passed. Three affected ROS packages
  were rebuilt with symlink installation on rover.
- Normal ROS shutdown released both locks and restored Internet sources. Latest
  NTP sample age/offset are also checked, so a rejected large adjustment cannot
  leave an old tracking estimate looking measurement-ready. Browser freshness
  uses its receive clock and host monotonic policy age, allowing offline UTC to
  differ from the browser without incorrectly declaring fresh data stale.

Backups: original rover chrony configuration is under
`/var/backups/mr2-clock/20260921T131907Z-e1fe18/`; original base configuration is
under `/var/backups/mr2-clock/20260921T132257Z-0c3af5/`. The base dashboard backup
is `/home/mr2/clock-policy-backup/dashboard-before-clock-policy.tar.gz`.

### Operational limits

- UTC status is deliberately conservative: a local reference is never called
  Internet-verified. While locked, read-only Internet observations are stored in
  `/run/mr2-clock/status.json` and never applied to the system clock.
- During a prolonged partition the base lease expires after 60 seconds, allowing
  base to recover when the rover has disappeared. Rover disables the source
  before the acknowledged lease's 20-second expiry margin and rejects large corrections;
  measurement readiness is false until the base lock is re-established.
- Correct powered-off elapsed time requires RTC backup power or an external
  time source. Rover's previously installed disk clock recovery remains enabled.
- No physical cold boot, field Rocket link, loaded camera capture, or real
  driving test has been performed for this change. The field IP settings are
  generated/tested; live validation used the current development network.
- Source changes are deployed to both checkouts but not committed or pushed.
  Existing base untracked files are preserved. The gateway was not running at
  deployment; its next normal start loads the updated status provider.
