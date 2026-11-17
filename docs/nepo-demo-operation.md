# NEPO demo operation

Use branch `2026-nepo-demo` and `mr2_launch/rover_direct.launch.py` for
manual and circular driving without GPS. At commit `3b24685`, this branch
adds dashboard Orbit controls to `feature/rover-direct`; the gateway,
ROS drive controller, firmware, and launch defaults are unchanged.

## Start on the Jetson

The dashboard is served at `https://10.42.0.1` on the `MR2-Rover` access
point. Build and deployment instructions are in
[rover-direct operation](./rover-direct-operation.md).

Before starting the rover, install the camera rules and configure CAN.
These commands require the Jetson user's sudo password. Configure CAN
while the rover stack is stopped, because the script resets the interface.

```bash
cd /home/mr2/mr2-stack
./scripts/install_udev_serial_rules.bash
./scripts/can0.bash
```

Start the existing direct-control wrapper in a terminal:

```bash
cd /home/mr2/mr2-stack
./scripts/run_rover_direct.bash
```

The arm is enabled by default. If it is absent or unpowered, use this command
instead:

```bash
MR2_ENABLE_MANIPULATOR_MODULE=false ./scripts/run_rover_direct.bash
```

The wrapper starts the gateway, cameras, and real rover controllers. It uses
the pinned Node runtime and installed ROS workspace automatically. Do not
also start `mr2-rover-direct.service` while this terminal launch is running.

In a second terminal, confirm controller activation:

```bash
source /opt/ros/humble/setup.bash
source /home/mr2/mr2-stack/rover/ros2_ws/install/setup.bash
ros2 control list_controllers
```

`joint_state_broadcaster` and `rover_controller` should be active. With the
arm enabled, also check `manipulator_controller`; gripper activation depends
on its hardware being present. Missing camera devices cannot provide video
even when the drive controllers are active.

## Circular driving

1. Open the dashboard and select the connected gamepad in the drive panel.
2. Select **Orbit**, or hold **L1 + R1** for one second to toggle modes.
   Entering Orbit resets its center to `(0, 0)` and requires the left stick
   to return to neutral before rotation starts.
3. Set the center in meters: **X is right**, **Y is forward**, relative to
   the rover. Set coordinates while stopped, or move the center with the
   right stick. `(0, 0)` rotates in place; `(1, 0)` puts the center one meter
   to the right.
4. Select low rotation sensitivity for the initial check. Move the left
   stick horizontally to rotate; release it to command a stop. Left commands
   clockwise yaw, right counterclockwise yaw in the command frame. This reverses
   the initial demo's left-stick mapping. Triggers are unused in Orbit.
5. Select **Manual** to return to normal translation controls. Center sticks
   and release triggers before switching back; manual inputs take effect
   immediately.

Rotation sensitivity controls angular speed; center sensitivity controls how
quickly the right stick moves the center. The dashboard limits angular speed
using the farthest wheel from the center, keeping requested wheel speeds at
or below `0.8 m/s` with the standard rover geometry. Existing acceleration
limits and steering alignment can alter the path during transitions; this
is a commanded circular motion, not GPS-based path tracking.

## Deployment check

The repository's validation entry point is:

```bash
./scripts/check_rover_direct.bash
```

After deploying a new dashboard build, reload the browser to load the new
assets. Confirm the **Manual / Orbit** buttons are visible. Build checks do
not establish motor feedback, physical steering calibration, or camera
stream availability; verify these on the connected rover.
