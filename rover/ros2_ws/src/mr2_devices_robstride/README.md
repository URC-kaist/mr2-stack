# RobstrideDevice

`CanDevice` plugin for RS-06 private CAN velocity mode. It runs inside CanHW
or the existing mr2_control device adapter; it is not an independent ROS node.
Command units are output-shaft rad/s, state units rad, rad/s and Nm. The
existing transmissions apply wheel mounting signs; no extra 9:1 internal
gear reduction is applied to the motor's output-shaft quantities.

```bash
ros2 launch mr2_launch rover_real.launch.py actuator:=rs
ros2 launch mr2_launch rover_direct.launch.py actuator:=rs
```

`actuator:=custom` is the default. Only the four drive wheels switch plugins.
Steering and the gripper always use `CustomActuatorDevice` in the existing
`mr2_devices_output_actuator` package. Its message package and topic API stay
unchanged. The old OutputActuatorDevice plugin identifier is replaced.

## Protocol and settings

Reference: [RS06User Manual251112](https://files.seeedstudio.com/products/RobStride/Product%20Literature/RS06/RS06User%20Manual251112.pdf),
private CAN protocol and sample constants (PDF pages 19–30).
This implementation requires firmware with Type 17 run_mode readback,
Type 18 status responses, canTimeout (0x7028), and wrapping Type 2 feedback.
It neither changes baud rate nor saves parameters to flash.

| Joint parameter | Default | Meaning |
| --- | --- | --- |
| motor_id | required, 1–127 | Motor CAN ID |
| host_id | 253 | Reply destination; must differ from motor ID |
| can_iface | required | Shared SocketCAN interface |
| feedback_position_max_rad | 12.57 | Symmetric Type 2 angle range and half wrap period |
| feedback_velocity_max_rad_s | 50 | Symmetric Type 2 velocity range |
| feedback_torque_max_nm | 36 | Symmetric Type 2 torque range |
| max_velocity_rad_s | 20 | Output-shaft velocity clamp |
| current_limit_a | 14.3 | Velocity-mode phase current limit |
| acceleration_rad_s2 | 20 | Velocity-mode acceleration |
| feedback_timeout_ms | 200 | Host feedback deadline |
| can_timeout_ms | 200 | Firmware command watchdog once the write loop starts |
| activation_timeout_ms | 1500 | Deadline for each startup response |

These scale defaults come from the cited RS-06 manual, not model detection.
Confirm the installed firmware's scales and wrap convention, and adjust
the joint parameters in `rover_can.ros2_control.xacro` before physical use.
The firmware speed range limits command configuration to at most 50 rad/s.
Limits here are initial software settings, not a validated vehicle tuning.

Activation confirms stop, writes velocity mode, reads it back, writes current
and acceleration limits, zero target, and confirms enabled status. Other
motors can take seconds to activate, so watchdog activation and a fresh
zero-speed feedback handshake occur in the first owner write cycles before
any nonzero target. The initial zero-target idle phase has watchdog disabled.

Nonfinite commands send zero. Status faults, fault/warning frames, unexpected
reset and feedback timeout latch a stop; stop is retried every 50 ms during
process(). New feedback does not automatically re-enable the motor. Explicit
deactivation/activation is required. Driver faults are never cleared
automatically. Deactivation queues zero and Type 4 stop. Firmware watchdog
also covers loss of subsequent host commands.

Position unwraps over the configured symmetric feedback interval; it is not
reduced modulo 2π. Runtime feedback gaps that could hide half an interval
invalidate position and latch stop. The timeout must satisfy
`timeout_seconds * feedback_velocity_max_rad_s < feedback_position_max_rad`.
Position is rebased on the first fresh sample of a new activation/write
session; turns across shutdowns or telemetry outages cannot be recovered.

The rover uses IDs FL=7, FR=5, RL=8, RR=6, with FL/RL transmission −1 and
FR/RR +1. Verify signs against actual installation and output shaft direction.
The shared bus remains at the configured rate (currently 500 kbps); provision
all RS-06 units to the same rate separately before connecting. Selecting rs
does not alter the CAN interface or any other bus device.

## Software validation

`test_robstride` uses an in-memory CAN motor endpoint and deterministic
timestamps. `test_plugin_loading` loads the installed shared libraries through
pluginlib without opening a CAN interface. Launch/xacro tests validate both
options and ensure steering, gripper and transmissions are identical.
These checks do not establish physical motor operation or firmware compatibility.

## Single-motor hardware smoke test

With the CAN interface already active at the motor's configured bitrate, first
run `python3 scripts/robstride_probe.py --iface can0` from the repository root.
This only sends identity and parameter-read requests. Check the detected ID,
bus voltage and stopped state before the motion test. The flat AK motor node
does not implement the RS-06 protocol; leave other motor command owners stopped.

After sourcing the workspace, explicitly run the installed plugin test:

```bash
ros2 run mr2_devices_robstride robstride_smoke_test --run can0 MOTOR_ID /tmp/rs06.csv
```

It loads `RobstrideDevice` through pluginlib, uses its normal CAN transport,
and applies a 1 A current limit, 0.5 rad/s² acceleration, and ±0.2 rad/s for
one second each with one-second zero-speed intervals. It checks measured velocity and
position, then confirms stopped/reset feedback. Faults, excessive speed,
temperature above 60°C, or travel above 0.5 rad abort the test. State CSV and
raw CAN frames are recorded. The test changes volatile motor mode/limits and
watchdog settings, leaves a zero target and disables the motor; it does not
save to flash or restore the previous volatile settings. It does not change
the rover profile or launch the four-wheel system.
