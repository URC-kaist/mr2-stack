# Flat bench with the production rover control stack

This package lives in a separate workspace and changes no production source,
launch file, profile, controller YAML, or installed production library.

The running control path is the existing command bridge → production
TwistToCommandsController → ros2_control controller_manager → production CanHW
→ CanDevice plugins. FL defaults to the installed RobstrideDevice, motor 127;
the remaining seven drive/steering actuators use this package's MockCanDevice.
The mock plugin exports the normal interfaces and has no CAN socket or traffic.
It is an ideal actuator fixture, not a motor firmware or dynamics simulator.

The robot model comes from the installed production xacro; joint interfaces,
transmissions and geometry are retained. Only hardware device selection and
the real fixture's ID/current/speed/acceleration limits are changed in memory.
The original rover_controllers.yaml is passed directly to controller_manager.
The arm and autonomous modules use the existing disabled-module options.

Build this workspace after sourcing the production workspace:

```bash
source /opt/ros/humble/setup.bash
source /home/mr2/mr2-stack/rover/ros2_ws/install/local_setup.bash
cd /home/mr2/mr2-stack/rover/flat_bench_ws
colcon build --symlink-install --cmake-args -DBUILD_TESTING=ON
source install/local_setup.bash
ros2 launch mr2_flat_bench rover_flat.launch.py
```

The default `connection:=direct` reuses the existing rover-local gateway/XBee
PTY/dashboard launch, with its hardware owners disabled, plus the production
control stack configured above. Use `connection:=base` for the existing remote
base gateway or `connection:=none` for control-stack-only testing. Production
profile files and the persisted profile selector are not changed.

`real_wheel:=fl|fr|rl|rr` selects the physical wheel; `real_wheel:=none` makes all
eight devices mocks for software tests. `motor_id:=127` and `can_iface:=can0`
are defaults. Configure CAN separately to the motor's bitrate (currently
500 kbps). The real test motor is capped at 0.2 rad/s, 1 A and 0.5 rad/s².
The production controller's normal input timeout and steering logic remain.
Its normal parameters are not retuned for the fixture.

On this machine the workspace is already built. To start it in a new terminal:

```bash
source /opt/ros/humble/setup.bash
source /home/mr2/mr2-stack/rover/ros2_ws/install/local_setup.bash
source /home/mr2/mr2-stack/rover/flat_bench_ws/install/local_setup.bash
ros2 launch mr2_flat_bench rover_flat.launch.py
```

Open the existing dashboard at `https://10.42.0.1/`, enable its controls and
select the gamepad. In manual mode RT drives forward and LT drives backward.
The physical motor represents FL and retains its production negative
transmission sign. Release the triggers and return sticks to neutral to send
zero; Ctrl-C in the launch terminal deactivates CanHW and stops the motor.
Stop this launch before starting another launch that owns the same CAN motor.

Observed existing-stack limitation: the dashboard E-Stop sends ABORT to
`/mission_control`, but continued manual `cmd_drive` input is not inhibited by
the production drive controller. This was reproduced with all devices mocked;
no production fix or extra command-interception node is included here.
Do not rely on that button to stop a held manual command in this configuration.

Validation includes the real motor's forward/reverse motion, zero commands,
the original controller's command timeout, and a synthetic client using the
dashboard's public WebSocket endpoint. Browser/gamepad hardware input itself
has not been exercised automatically. See `../../validation/2026-11-13/README.md`
for results and logs.

Unit checks (never enable hardware automatically):

```bash
cd /home/mr2/mr2-stack/rover/flat_bench_ws
colcon test --packages-select mr2_flat_bench
colcon test-result --verbose
```

The `test/check_stack.py` and `test/check_gateway.py` scripts are explicit
integration diagnostics. They default to eight mock devices; passing
`--real-wheel fl` deliberately enables the physical fixture. Both require
`--output DIRECTORY` for their evidence files.
