# Offline runtime and clock audit — 2026-10-29

## Verdict

The video transport, adaptive controller, local dashboard assets, and local NTP
authority have working paths without Internet access. **Continuous readiness
for precise one-way latency measurement did not pass on the current development
LAN.** These are separate acceptance criteria. Do not treat a synchronized flag
alone as proof of measurement accuracy.

The Internet connection used for development was preserved throughout. No host
interface, address, default route, DNS setting, firewall rule or radio setting
was changed. Test gateways were not started on 8081. No motor, camera or real
vehicle launch was started by this audit.

## Tests and evidence

| Check | Outcome |
| --- | --- |
| Internet-free network namespace | PASS: only loopback, no routes, external connection fails; distinct namespace inode checked before raising loopback |
| Local NTP with no public sources | PASS: real chronyd server and client, `namespace-runtime.json` |
| Offline startup with public pools unreachable | PASS: new local-stratum-8 chronyd still serves time, `cold-boot-ntp.json` |
| Actual H.264/RTP and live rate cut inside that namespace | PASS: x264, 377 packet observations, `namespace-check-0.log` |
| Control, congestion, clock parsing, startup and trial tests inside that namespace | PASS: 42/42, `namespace-check-1.log` |
| Clock policy/config/preflight/field-preparation tests | PASS: 32, `policy-tests.log` |
| Dashboard latency/readiness tests | PASS, including displaced browser/base clocks, `dashboard-tests.log` |
| Browser with all page requests outside localhost blocked | PASS: cold-cache scripts/CSS/fonts and Live Feed UI; HTTPS secure context and VideoDecoder available, `browser.json` |
| Saved map samples | PASS: KAIST, pump_track, NAIP PNGs served locally over HTTPS, `browser.json` |
| Installed prerequisites on both hosts | PASS: `prerequisites-base.log`, `prerequisites-rover.log` |
| Actual base/rover clock lock and local authority | PASS: base stratum 8 / rover stratum 9, source 192.168.0.55, no public NTP sources or stepping directives while locked |
| Continuous precise measurement readiness | **FAIL**: no uninterrupted 40-second ready window within the 160-second fixture; `clock-live.json` |

The isolated NTP servers used `chronyd -x` and clients used `chronyd -Q`.
Neither can set the host clock. They used temporary files and high UDP ports.
The namespace's absence of network access does not affect its parent or SSH.

For the real-device clock check, a hardware-free ROS LaunchService used the
same `acquire_clock` guard as a regular rover launch, with the per-session
development base override. Its sole launched child was `sleep`. This exercised
the installed clock managers, restricted pinned-key SSH lease, real chrony
profiles and NTP exchange. Both hosts continued to have Internet connectivity,
but their *clock correction profiles* used no public time sources while locked.
The optional Internet probe is read-only and not a synchronization dependency.

All fixture locks were released afterward, and the usual idle clock profiles
were restored. Before/after host routes and addresses were compared and matched.
The raw strict-readiness failure is retained; it has not been relabeled a pass.

## What the timestamps mean offline

* Pacing, packet grouping, delay trends, probe spans and controller timers use
  monotonic clocks. Constant differences in host clock origins cancel in the
  interval calculations and relative queue baselines. UTC synchronization is
  not required for video playback or GCC operation. The namespace tests also
  cover different origins and simulated drift in the controller.
* RTP carries both sender monotonic and epoch timestamps; the receiver records
  the corresponding arrival times before jitter buffering. An absolute
  rover-to-base duration calculated from epoch timestamps needs relative host
  synchronization. Ordinary timestamp availability does not prove accuracy.
* The base's `local stratum 8` supplies the reference without GNSS, DNS or public
  NTP. The rover follows that base. Both can agree even if their UTC date is
  wrong. Current GNSS receiver time is not configured as a chrony PPS/refclock.
* Browser/base time calibration uses the local `/latency/time` endpoint. No
  public browser time service is required. Source/tests use a base-domain
  freshness stamp and a measured browser-to-base offset, so a laptop whose
  clock differs from base is supported.

The real clock check recorded 28 observations over approximately 158 seconds.
Nine observations passed `require_measurement_clock()`. The first passing
observation was around 41 seconds after acquisition. From about 35 seconds
onward, the observed chrony system residual was within approximately 0.31 ms,
but the reported error bound ranged approximately 1.27–2.74 ms. Latest raw NTP
sample age/offset/error is checked separately and can also prevent readiness.
The implementation requires all applicable limits to remain within 2 ms;
it correctly rejected measurements when they did not. These are chrony
estimates, not an independently measured physical clock offset.

This audit establishes the offline path, but does not establish a continuously
valid 2 ms error bound on a loaded Rocket radio link. The run recorded initial
settling and varying local NTP error estimates after the clock-profile change.
No threshold was relaxed to make the test pass.

## Startup and field conditions

1. Use the designated field addresses: base 192.168.1.101 and rover
   192.168.1.102. They must have local reachability for NTP UDP 123, restricted
   clock leases over SSH TCP 22, and the existing video/ROS/gateway paths.
   Public DNS and an Internet gateway are not required.
2. Enable/start the installed chrony and mr2-clock services on both machines.
   They are already enabled on these hosts. Start base first.
3. With the rover's field address present, its idle clock policy selects only
   base, avoiding public servers on another interface outvoting an offline base.
   **Allow synchronization before the real ROS launch.** On the field network,
   the existing read-only waiting command is:

   ```bash
   python3 scripts/field_setup.py rover clock
   ```

   It waits up to 120 seconds and requires a recent selected-base sample plus
   offset/error bounds within 2 ms. It does not change addresses or start ROS.
   A timeout means the precision criterion has not been met.
4. Then use the normal startup commands. The real launch acquires both clock
   locks automatically. During the launch there is no stepping, external NTP
   correction is disabled, and rover slewing is limited to 100 ppm. Adjustments
   above 50 ms are rejected. Starting with a large inter-host offset can
   therefore leave precise measurements unavailable; settle it while idle.
5. Use the Latency panel's readiness state before precise measurements. Rover
   capture rechecks the measurement clock during capture and rejects a trial
   if readiness is lost. Video and congestion control remain usable.

On a development LAN, retain field defaults and override the runtime IPs.
For latency artifact uploads on the current LAN, the base needs
`--latency-public-base-url http://192.168.0.55` in addition to its usual
development-address options. This is needed only when using that diagnostics
feature; video feedback continues over XBee.

An installed base `.env.local` still set that upload URL to the old development
host 192.168.0.62. The audit corrected **only**
`MR2_LATENCY_PUBLIC_BASE_URL` to `http://192.168.1.101`, retaining a private,
mode-0600 backup under `~/.local/state/mr2-clock-offline-audit/`. Parsing the
actual loaded environment now confirms both the field URL and explicit
development override. See `config-fix.log`. No credentials are included in the
evidence. There were no production source changes in this audit.

## Scope and remaining limits

* The deployed dashboard includes local fonts, JS, CSS, ROSLIB and plotting
  libraries. Local map tiles occupy about 2.7 GiB on base. Only saved coverage
  is available offline; the optional Esri imagery source needs Internet.
* NTRIP is disabled by default; RTCM defaults to `/base/rtcm`. Enabling an
  external NTRIP caster would add a separate network dependency.
* The HTTPS certificate is currently valid (2026-04-27 through 2027-04-27).
  The browser check used the existing local self-signed certificate. Local
  certificate trust must be arranged on the field laptop.
* RTC devices and disk-clock recovery are present. Powered-off elapsed time,
  RTC backup-battery health and a true offline cold boot were not tested.
  Absolute UTC correctness is not promised by a local reference.
* The browser check did not start a gateway or ROS; it validates frontend
  assets/UI/local maps with external page requests blocked. Native video was
  tested separately inside the Internet-free namespace. This is not a new
  real-camera-to-browser RF acceptance run, a driving test, or a reboot test.

The included runners are evidence/reproduction helpers for this bench. Read
their hardcoded development addresses before reuse. `clock_audit.py` temporarily
acquires real host clock locks and intentionally returns failure if continuous
precision is insufficient. The two namespace runners assert a distinct network
namespace before altering its loopback; invoke them via `unshare -Urn`, passing
the parent network namespace inode as their first argument. Never remove those
guards or run their network setup on the host.
