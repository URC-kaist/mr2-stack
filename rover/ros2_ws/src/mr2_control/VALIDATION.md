# Software validation — 2026-09-11

Environment: aarch64 Linux `5.15.185-tegra`, ROS 2 Humble, GCC 11.4.0,
C++17. CMake build type was unset (no release optimization requested). Builds
and installs used `/tmp/mr2-control-build` and `/tmp/mr2-control-install`.
The source base was `2026-nepo-demo` at `3b24685`; changes remain uncommitted.
No physical CAN interface was opened and no physical actuator commands,
deployment, remote push, or host CAN/scheduling changes were performed.

## Executed checks

| Check | Result |
| --- | --- |
| Build core, messages, AK driver, OutputActuator driver, legacy CanHW, new runtime | 6 packages built successfully |
| Build modified rover-description and launch Python packages using the existing workspace underlay | 2 packages built successfully |
| CTest/colcon suite | **20 tests, 0 errors, 0 failures, 0 skipped** across 5 test targets |
| Actual legacy drive controller vs new processor | 100 deterministic varied command/feedback cases matched all eight targets to 1e-12; legacy timeout zero checked |
| Installed joint-state broadcaster contract | Default `base_link` frame, measured position, and NaN missing velocity/effort verified against the installed plugin |
| Converter | Independent expected Simple/FourBar values, signs, velocity scaling, current-like effort, boot capture/reset, finite-state retention, clamps, NaN-before-reference, invalid velocity fallback |
| Driver protocol/lifecycle | Positive/negative angle bytes, velocity bytes, invalid target hold/zero, no-feedback/timeout/recovery, malformed AK feedback rejection, optional absent actuator, confirmed arming with concurrent feedback, deactivation, ROS-context cancellation |
| Feedback and input concurrency | 20,000 iterations of AK and OutputActuator RX while control copies/processes; 100,000 protected input updates checked for matching payload/timestamps |
| Full ROS-to-vcan comparison | Eight scenarios × 15 motor IDs matched the original HEAD backend byte-for-byte at settled samples; terminal frames and clean shutdown passed (details below) |
| Driver encoding fixture | Per-device send capture; no private access to CanBusManager. The previous two socketpair tests were removed when restoring the shared manager. |
| Scheduling | Injected 25 ms work at 100 Hz; skipped expired deadlines; clean join; exception reporting |
| Launch inspection | Both backend selections across arm enabled/disabled and active/inactive; no legacy spawners with new runtime; standalone exclusion; unsupported simulation rejection. No nodes executed by these tests. |
| Full-rover mock ROS integration | Passed; 583 joint-state messages and 175 odometry messages observed |
| Base-only mock ROS integration | Passed; 435 joint-state messages and 130 odometry messages observed |
| Standalone-arm mock ROS integration | Passed; 393 joint-state messages; no drive odometry publisher output |
| Final whitespace check | `git diff --check` passed |

The three integration runs assert first-snapshot readiness/stamps/frame, measured
state shape, startup origins, inactive position processors, queued activation,
fresh command requirement, full/standalone order, malformed arrays, deactivation
hold, gripper commands, manual/autonomous selection, timeout wheel zero,
odometry frames/covariance, diagnostics, and clean SIGINT exit. Each process
forces synthetic devices and remaps ROS endpoints to a unique test name.
Synthetic device mode exercises the runtime, not firmware or physical dynamics.

## Historical ThreadSanitizer scope (before the shared-manager restoration)

The earlier six targeted driver/transport tests passed with
`-fsanitize=thread`, `TSAN_OPTIONS=halt_on_error=1`, and no sanitizer diagnostic:

```bash
ROS_DOMAIN_ID=188 ROS_LOCALHOST_ONLY=1 TSAN_OPTIONS=halt_on_error=1 \
  /tmp/mr2-control-tsan-build/test_drivers \
  --gtest_filter=-Drivers.CancelActivationWhenRosContextStops
```

An unfiltered run including the ROS-context cancellation test reported
`ThreadSanitizer: double lock of a mutex` in the installed
`libfastrtps.so.2.6`, at `ResourceEvent::register_timer`, while constructing the
test ROS node. This result remains unresolved; it is not counted as a clean
full-suite sanitizer run. The cancellation test passes in the normal suite.
The targeted run deliberately excludes that ROS-context construction case.
ROS middleware libraries were not rebuilt with instrumentation. These results
cover the exercised driver and inline bus accesses, not the whole ROS stack or
every possible interleaving. RX-to-executor telemetry buffers were reviewed for
bounded locking but were not exercised with a live hardware driver ROS graph.

The source-including driver test build emits two `-Wsubobject-linkage` warnings
for the driver's existing anonymous-namespace enum types. The sanitizer process
also emits a class-loader factory warning from the installed hardware-interface
libraries. Neither is treated as a sanitizer-clean result for those external
libraries; targeted tests still returned zero.

## Historical observed timing (before the shared-manager restoration)

The three final mock runtimes ran concurrently at 100 Hz, with ROS subscriptions,
publication and service calls, alongside the targeted sanitizer run. This is a
short software workload measurement, not a worst-case bound. Values below come
from the latest one-second diagnostics snapshot before each test ended; they do
not include later cycles after that snapshot.

| Runtime | Completed cycles in snapshot | Maximum cycle work (us) | Maximum scheduling lateness (us) | Skipped deadlines | Largest device feedback age in final snapshot (ms) |
| --- | ---: | ---: | ---: | ---: | ---: |
| Full rover | 499 | 123.939 | 124.133 | 0 | 9.981 |
| Base only | 399 | 98.179 | 94.529 | 0 | 9.988 |
| Standalone arm | 299 | 75.522 | 128.919 | 0 | 9.961 |

Full-rover cycle duration buckets recorded 497 cycles <=100 us and two <=500 us.
All sampled base-only and standalone cycles were <=100 us. These coarse buckets
support only bucket-level tail statements, not an exact cycle-duration p99.

ROS subscriber arrival intervals include executor/DDS/test scheduling and are
separate from control scheduling:

| Runtime | Joint-state arrival p99 (ms) | Maximum arrival interval (ms) |
| --- | ---: | ---: |
| Full rover | 10.501 | 10.678 |
| Base only | 10.425 | 49.828 |
| Standalone arm | 10.396 | 10.546 |

The base-only arrival outlier occurred without a reported missed control
deadline; publication/receipt timing must not be inferred from average control
work alone.

Other observed measurements:

- Concurrent RX stress, normal build: 1,743 pairs of driver feedback copies;
  pair latency p99 **9.024 us**, maximum **35.425 us**. This includes lock waiting,
  copying and scheduling, not isolated mutex acquisition time. Under TSan:
  5,224 pairs, p99 **22.369 us**, maximum **71.874 us**.
- AK `process()` entry to receiving its encoded command at the UNIX socket peer,
  200 samples, normal build: p50 **19.040 us**, p99 **71.778 us**, maximum
  **79.330 us**. Under TSan: p50 **39.265 us**, p99 **72.066 us**, maximum
  **146.947 us**. This includes peer wake/read overhead and is not a measurement
  of SocketCAN, CAN arbitration or motor application latency.
- Injecting 25 ms work every tenth cycle during a 550 ms scheduler test produced
  45 observed completed cycles, 10 skipped deadlines, maximum work
  **25,082.7 us**, maximum scheduling lateness **83.702 us**.

## Remaining validation

Physical device arming/configuration, actual bus utilization and delays, motor
response/stopping behavior, external MoveIt/Nav2 operation with real feedback,
and load-dependent scheduling limits remain hardware follow-ups. The software
checks do not justify changing the legacy launch default. Existing omissions
and deliberate migration changes are listed in [README.md](README.md).

Reproduction commands are in [README.md](README.md). Session logs are under
`/tmp/mr2-control-build.log`, `/tmp/mr2-control-tests.log`,
`/tmp/mr2-test-result.log`, `/tmp/mr2-launch-build.log`,
`/tmp/mr2-integration-{full,base,arm}.log`, `/tmp/mr2-tsan-targeted.log`, and
`/tmp/mr2-tsan.log` (the unfiltered sanitizer diagnostic).

## Virtual CAN comparison and shared-manager restoration

**Final result: PASS.** A clean rebuild of all six packages succeeded. The current
20-test suite passed with zero errors, failures or skips. The virtual comparison
matched eight scenarios × 15 motor IDs (120 settled ID/payload comparisons),
including timeout zeros and malformed-array holds. Captures contain 34,651
baseline outgoing frames and 33,286 new-runtime outgoing frames. All nine terminal
disarms and four terminal wheel zeros were received, with no later target after
each disarm; the new runtime exited with code 0 while feedback was still arriving.
Final run log: `/tmp/mr2-vcan-clean-final.log`; detailed differences (all empty):
`/tmp/mr2-vcan-results/comparison.json`.

The added final queue flush and test-only friendship were removed from
`can_bus_manager.hpp`; its contents match HEAD `3b24685` exactly. Shutdown is
owned by `mr2_control`: join control, stop each device's actual receive/transmit
worker, then write the existing deactivation frames through runtime-owned
SocketCAN sockets. The optional per-device send override defaults to the original
queue path. No new CAN IDs or protocol payloads were introduced.

The first virtual test exposed a teardown use-after-free: calling the executable's
header-only registry did not stop the bus held inside a dynamically loaded driver.
GDB showed an OutputActuator RX callback accessing a destroyed device. Devices now
retain their registered buses and expose an owner-thread stop operation; both the
new adapter and legacy CanHW use it before destroying callback owners. The shared
manager and registry implementations remain unchanged.

`test/vcan_comparison.py` runs the original baseline drivers, CanHW and rover
controller built from `git archive HEAD` in `/tmp/mr2-baseline-install`, then the
new runtime and rebuilt drivers. Each gets the same real-hardware URDF with only
`can_iface` changed to `vcan0`. The test uses actual SocketCAN, the unmodified
manager, simulated actuator diagnostics/arming and continuous fixed-zero measured
feedback. It publishes Twist messages at the XBee ROS output boundary, arm joint
arrays at the Servo output boundary, and gripper arrays. XBee serial decoding,
radio transport and MoveIt Servo are outside this test.

Scenarios: idle, forward, reverse, strafe, combined turn, speed saturation,
autonomous input selection, and command timeout. Arm/gripper targets vary across
scenarios; wrong-length arrays must retain their previous targets. Each settled
sample compares all 15 outgoing motor CAN IDs, DLCs and payload bytes exactly.
Transient timing and frame counts are not equated between independently scheduled
processes. The intentional buffered-source switch difference is not claimed to
be equivalent. Shutdown additionally requires nine disarm frames, four zero-wheel
frames, no subsequent targets after each disarm, and a clean new-runtime exit.

Build the baseline without modifying the working tree:

```bash
mkdir -p /tmp/mr2-baseline-src
git archive 3b24685 rover/ros2_ws/src | tar -x -C /tmp/mr2-baseline-src
source /opt/ros/humble/setup.bash
colcon --log-base /tmp/mr2-baseline-log build \
  --base-paths /tmp/mr2-baseline-src/rover/ros2_ws/src \
  --build-base /tmp/mr2-baseline-build --install-base /tmp/mr2-baseline-install \
  --packages-up-to mr2_can_hardware_interface mr2_devices_ak_servo \
    mr2_devices_output_actuator mr2_rover_control --parallel-workers 3
```

Run after building the current overlay and baseline:

```bash
source /opt/ros/humble/setup.bash
source rover/ros2_ws/install/setup.bash
source /tmp/mr2-control-install/local_setup.bash
unshare -Urn bash -c 'ip link add vcan0 type vcan && ip link set vcan0 up && ip link set lo up && ROS_DOMAIN_ID=191 ROS_LOCALHOST_ONLY=1 python3 rover/ros2_ws/src/mr2_control/test/vcan_comparison.py'
```

The namespace is temporary; the test verifies `info_kind == vcan` before opening
its socket. Host CAN interfaces are untouched. Captures, process logs and the
comparison JSON are written to `/tmp/mr2-vcan-results`.

## Adversarial startup and fault tests — 2026-09-11

**15 virtual-CAN edge-case scenarios passed**, after fixing one newly reproduced
startup bug. The 20 unit/launch tests passed again, and the original baseline
comparison still matched all 15 motor IDs/payloads across eight scenarios.
The repeated comparison captured 34,501 baseline and 33,196 new-runtime outgoing
frames; its shutdown assertions also passed.

The startup regression initially FAILED: a drive command published only while
activation waited for feedback was buffered by DDS. When the executor started,
its callback assigned the old command a fresh monotonic receipt timestamp and
produced nonzero wheel commands. The runtime now constructs RosIO only after
successful device activation. The regression verifies that startup-only drive,
arm and gripper commands are ignored, while fresh post-start drive commands work.
This intentionally changes startup acceptance, not steady-state conversion.

| Scenario | Observed/asserted result |
| --- | --- |
| Cold boot with steering at 30 degrees and AK motors at 30 degrees | Wheels receive zero velocity; AK motors retain measured motor position within one protocol unit. Steering receives zero-angle targets: startup can physically center steering. |
| Feedback delayed 0.6 seconds | No nonzero wheel targets before activation; normal idle output once feedback arrives. |
| Required steering actuator absent | Startup exits with error; no arm-power commands are sent. |
| Optional gripper absent, then powered later | Base/arm startup succeeds; absent gripper receives no angle targets. Late feedback does not silently arm it; restart is required. |
| Required wheel absent after steering has armed | Startup fails; every previously armed steering actuator is disarmed. No nonzero wheel target. |
| Firmware fault present at boot | Activation rejected; runtime exits with error. |
| Arm confirmation absent | Activation rejected; runtime exits with error. |
| All OutputActuators initially armed | Each receives disarm before rearm; idle wheels remain zero. |
| SIGINT during activation wait | Startup aborts promptly with error exit; no arm-power commands. |
| Commands sent only during activation | No replay after startup; fresh commands afterward work. |
| NaN/positive infinity/negative infinity commands; feedback loss and malformed AK frames | Invalid drive input zeros wheels; arm/gripper retain targets. AK output stops after its feedback timeout, rejects short frames, and resumes after valid feedback returns. OutputActuator continues transmitting under lost feedback and fresh drive input; this characterizes its inherited lack of an RX timeout. Losing drive input still produces timeout zeros. |
| Position processor activation/deactivation | Inactive commands ignored; activation requires fresh input; deactivation retains the last target. |
| Extreme positive/negative position requests | Requests of magnitude 1e6 and 1e308 produce identical clamped CAN payloads on all seven position motors. |
| Fresh/stale manual/autonomous source switching | Fresh selected source changes wheel direction; switching to stale manual data zeros wheels even while fresh autonomous messages continue. |
| Actuators disarm during driving; process restarts | No further wheel targets or automatic rearm while diagnostics report disarmed; restarted runtime stays at zero wheel velocity without new commands. |

A PASS in the table means the stated behavior was observed; it does not imply
that every inherited behavior is a fail-safe policy. In particular:

- Idle startup centers steering, and device activation can arm motors even when
  arm/gripper processors are inactive.
- OutputActuator feedback loss alone does not stop its command output. Firmware
  watchdogs or physical stopping under that condition were not tested.
- A continuing drive command stream after startup is accepted normally; the new
  startup fix does not add an operator-enable interlock.

Tests start the actual runtime and driver plugins, inject ROS messages and CAN
feedback, and capture outgoing frames through unchanged CanBusManager on private
`vcan0`. They simulate control-process startup, not Jetson OS boot, power rails,
brownouts, actual actuator firmware, mechanics, electrical bus-off or RF loss.
Fault tests use a 250 ms activation timeout where specified; the startup replay
case uses 2500 ms. Other cases use 1500 ms. The fixture sets one arm-confirmation
attempt to keep rejection tests bounded; default retry-count behavior is not
covered by this suite. No physical hardware was commanded.

Reproduce with the updated overlay sourced:

```bash
unshare -Urn bash -c 'ip link add vcan0 type vcan && ip link set vcan0 up && ip link set lo up && ROS_DOMAIN_ID=194 ROS_LOCALHOST_ONLY=1 python3 rover/ros2_ws/src/mr2_control/test/vcan_edge_cases.py'
```

Artifacts: `/tmp/mr2-vcan-edge-results/results.json`, per-scenario logs and frame
captures in that directory, `/tmp/mr2-edge-complete.log`, and the repeated
baseline comparison in `/tmp/mr2-edge-regression-comparison/comparison.json`.
The original failing regression report is `/tmp/mr2-edge-first-results.json`.
`CanBusManager` remains byte-for-byte identical to HEAD `3b24685`.

## Normal workspace installation — 2026-09-14

The first rover-direct attempt failed with `package 'mr2_control' not found`:
the earlier validation installed only into `/tmp`, while the user's terminal
sourced `rover/ros2_ws/install`. Rebuilt all six control packages together with
`--symlink-install --cmake-clean-first` into the normal workspace `build` and
`install` directories. Package discovery now resolves `mr2_control` to
`/home/mr2/mr2-stack/rover/ros2_ws/install/mr2_control`, and its executable is
registered. The rover-direct `--show-args` inspection succeeds and lists both
backend choices. All 20 unit/launch tests passed against this build (zero errors,
failures or skips). No physical-hardware launch was executed for this check.

Build log: `/tmp/mr2-workspace-build.log`; test log:
`/tmp/mr2-workspace-tests.log`. Source the normal workspace's `install/setup.bash`
in the launch terminal after installation.
