# Flat motor through the existing drive controls

This optional rover-side node maps one existing Twist component to the flat
rover's AK motor 104. **No base-station dashboard, gateway, nginx, or protocol
change is required.** Original rover control and Arm Servo code are unchanged.

The existing path is:

```
Base dashboard Manual drive / gamepad
  -> existing gateway CMD_DRIVE -> XBee (or direct-mode PTY)
  -> rover xbee_bridge -> /base/cmd_vel.linear.x
  -> flat_motor -> CAN motor 104
```

The dashboard's displayed Y is forward/backward, which it serializes as ROS
`linear.x`, not `linear.y`. RT is positive; LT is negative. The right stick's
forward/backward axis and any other forward drive input share this component.
The node cannot distinguish the input device or reconstruct individual triggers
from a Twist. Keep sticks centered, use **Manual**, and operate only the triggers.
Unequal simultaneous trigger presses produce their net difference, just as on
the original rover; equal presses cancel. Do not use Orbit for this motor test.

## Build and launch on the rover

```bash
cd /home/mr2/mr2-stack
source /opt/ros/humble/setup.bash
cd rover/ros2_ws
colcon build --symlink-install --packages-select mr2_flat_motor mr2_launch
cd ../..
source rover/ros2_ws/install/setup.bash
./scripts/rover.bash start flat
ros2 launch mr2_launch rover_real.launch.py video_base_host:=BASE_STATION_IP
```

Replace `BASE_STATION_IP` with the existing base address. Keep the base's normal
launch, dashboard and nginx setup. The flat profile now enables the rover's
existing XBee command receiver (`/dev/ttyXBEE`, overridable by `xbee_device`).
The physical XBee link must be connected; changing video_base_host does not
transport motor commands. The existing `rover_direct.launch.py` also works via
its local gateway/PTY, without changing the control semantics.

`can0` must already be up at 500 kbit/s. Use `scripts/can0.bash` only while control
processes are stopped. Append `enable_gnss:=false` if GPS is absent.
`enable_flat_motor:=false` disables this node. Original profile defaults leave it
off. Enabling it together with original rover hardware or the manipulator module
is rejected to avoid two controllers commanding the same motor.

## Operate the unchanged dashboard

1. Select the gamepad in the **drive panel**, not Arm Servo. Select **Manual** if
   the installed version offers Manual/Orbit. Choose **Low** sensitivity first.
2. Center sticks and release both triggers for at least half a second after
   feedback/link recovery. No initial rotation or zero-position command is sent.
3. RT rotates clockwise; LT counterclockwise. Release to command zero rotational speed.
   Select **No control** to stop sending gamepad commands.
   The existing **E-Stop** button latches a stop in this node; restart the rover
   launch to reset it. Neutral commands alone cannot clear that stop.
4. If direction is reversed, stop the rover launch and append
   `flat_motor_clockwise_sign:=1` when restarting (default `-1`). Clockwise is
   viewed facing the output shaft; the physical sign still needs visual checking.

| Existing drive sensitivity | Full-trigger input | Motor speed |
| --- | --- | --- |
| Low | 0.2 | 45 degrees/s |
| Medium | 0.4 | 90 degrees/s |
| High | 0.8 | 180 degrees/s |

Speed is proportional to trigger pressure and capped at 180 degrees/s by default. The flat
profile alone disables the XBee bridge's drive smoothing so releasing a trigger
does not leave its original rover deceleration tail. The original profile keeps
smoothing enabled. Inputs within +/-0.02 of zero are neutral.

There is no software angle/travel clamp. Continuous rotation uses the motor's
velocity loop, rather than accumulating position targets. The previous +/-200
degree clamp and 8-degree position-tracking gate have been removed. Feedback
angle is telemetry only, so its limited range/wrap cannot stop rotation. No
origin, flash configuration or calibration is changed.

Override maximum speed at launch without changing the base (applies to either
rover launch):

```bash
ros2 launch mr2_launch rover_direct.launch.py flat_motor_max_speed_deg_s:=360.0
```

Default acceleration/reversal slew is 720 degrees/s², adjustable with
`flat_motor_max_accel_deg_s2`. Trigger release, timeout and E-Stop bypass the
slew limiter and command zero speed immediately. Zero speed is velocity-loop
stopping, not an absolute-position hold; small stopping travel is possible.

Startup needs 20 stationary feedback frames over at least 0.4 s, followed by a
continuous neutral command interval of 0.3 s before nonzero commands can move.
The node checks command age (250 ms), feedback age (150 ms), motor faults, and a
conservative test current limit (3 A). After any motion, it actively sends zero
speed on command/feedback timeout or fault and during normal shutdown; simply
ceasing to send a velocity command would leave the motor's last speed active.
Fault recovery requires healthy stationary feedback and neutral commands again.
A severed CAN link or killed/unpowered host cannot deliver a stop packet; a
motor-side watchdog/hardware stop is a separate requirement and is not configured
by this node. This software is not a hardware emergency stop.
A per-interface/motor lock prevents duplicate instances, and detecting another
CAN sender commanding the same motor disarms this controller.

## ROS interface and diagnostics

- Input: `geometry_msgs/msg/Twist`, `/base/cmd_vel`, component `linear.x`.
- Status: `/flat_motor/status`, `std_msgs/msg/String`, JSON at 10 Hz with readiness,
  arming/fault, raw angle, requested/commanded/measured speed in degrees/s, current,
  and speed/acceleration settings.
- CAN feedback: extended `0x2968`, DLC 8. Velocity commands: extended `0x0368`,
  DLC 4, signed big-endian electrical RPM. AK60-6 V3 has 14 pole pairs and 6:1
  reduction, so output degrees/s times 14 gives ERPM (180 degrees/s = 2520 ERPM).
  The feedback speed is in 10 ERPM units and is converted using the same factor.
  Sources: [AK V3 manual, sections 4.1.4 and 4.3.1](https://www.cubemars.com/data/cms/202602/ak-series-prodcut-manual-v3-2-0-for-ak-3-0-robotic-actuator.pdf),
  [AK60-6 V3 specifications](https://www.cubemars.com/product/ak60-6-v3-0-kv80-robotic-actuator.html).

For a different existing command source, override without changing the base:

```bash
ros2 launch mr2_launch rover_real.launch.py \
  flat_motor_command_topic:=/cmd_vel flat_motor_command_component:=linear.y
```

Use this only if that source actually publishes the intended input on that axis;
with the current dashboard, the default `/base/cmd_vel.linear.x` is correct.
Do not subscribe to the autonomous `/cmd_vel` by accident.

```bash
ros2 topic echo /base/cmd_vel
ros2 topic echo /flat_motor/status
PYTHONPATH=rover/ros2_ws/src/mr2_flat_motor python3 -m pytest -q \
  rover/ros2_ws/src/mr2_flat_motor/test
```

## Verification on 2026-09-18

The continuous-speed version was checked through the unchanged gateway JSON,
direct-launch serial PTY, existing XBee bridge and `/base/cmd_vel` using the real
104 motor. A high-sensitivity input rotated 451.9 degrees, from 149.2 to -302.7
degrees, crossing the old -200-degree bound. Peak measured speed was 180.7
degrees/s. Reversal worked beyond the old bound. Release, a missing-command
interval and the existing E-Stop each reduced measured speed to zero. Motor
faults and CAN bus-off remained zero. Logs: `log/flat-motor-velocity-validation/`.

Controller/profile tests cover angle-boundary crossings, raw angle wrap, speed
units/scaling, acceleration, neutral gating, active zero-speed stops after RX
loss, E-Stop latching, shutdown and launch overrides. Both packages build.
Earlier position-loop test logs remain under `log/flat-motor-cmdvel-validation/`
and describe the superseded implementation.

The test used a local serial PTY, not a physical radio link or operator gamepad.
Physical CW/CCW orientation still needs visual checking; use the sign option
above if reversed. Test launches were stopped afterward. Restart the operator's
launch to load the new code and defaults; no dashboard rebuild is needed.
