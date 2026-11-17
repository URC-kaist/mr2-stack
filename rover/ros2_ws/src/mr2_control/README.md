# MR2 Control

`mr2_control` is an opt-in actuator runtime for the real rover. It replaces
Controller Manager execution and command/state routing for drive, steering, arm,
and gripper. The legacy `ros2_control` backend remains the launch default.

The implementation starts from `2026-nepo-demo` commit
`3b24685c0d7153888f7bcbcf22cca5ea5ea85cb5`. [BEHAVIOR.md](BEHAVIOR.md) records the
source inventory and differences from the specification's `74422f3` reference.
Software validation uses synthetic devices and local UNIX sockets; no physical
motor commands or rover deployment were performed.

## Execution and ownership

| Context | Owns / performs | Cross-thread boundary |
| --- | --- | --- |
| Startup/shutdown caller | Parse configuration; construct components; activate devices before starting control; join control before deactivation | Activation waits leave CAN I/O running; shutdown stops I/O before destroying devices |
| Dedicated control thread | Input snapshot; device snapshots; motor-to-joint conversion; selector; drive, arm, gripper processing; joint-to-motor conversion; command assignment; driver `process()`; prepared telemetry | Short locks copy inputs, feedback, and telemetry; commands stay on this thread |
| Existing CAN I/O thread per manager/interface | Drain FIFO TX queue, write socket, receive frames, directly invoke decoding callbacks | Commit coherent driver feedback under a mutex; no raw-CAN receive queue |
| Single-threaded ROS executor | Receive commands; queue management events; publish joint states, odometry, diagnostics and prepared driver telemetry | Latest-value buffers; bounded 16-event management queue; no publication while holding feedback/control locks |

Driver objects are ordinary C++ objects. Calling `process()` does not start a
thread. There is no device-processing thread or cross-thread motor-command
mailbox. Driver plugins and their encoders are reused. The converter uses the
existing SimpleTransmission and FourBarLinkageTransmission implementations.
`hardware_interface` is retained for its URDF parser/configuration structs, and
`transmission_interface` for those transformations; the new execution path does
not instantiate a SystemInterface or ControllerInterface.

The control loop uses `std::chrono::steady_clock`, successive absolute deadlines,
and measured elapsed `dt`. After an overrun it skips expired slots to the next
future deadline, without a burst of catch-up updates. ROS stamps use the node's
clock. `use_sim_time:=true` is rejected by this hardware runtime. Gazebo continues
using the legacy backend.

Command subscriptions and processor services are created only after device
activation succeeds. Commands published only during activation are not queued
and replayed as fresh input afterward. A continuing command stream can still
move the rover as soon as startup finishes; there is no separate operator-enable
interlock. Default idle drive output centers steering and commands zero wheel
velocity. Inactive arm/gripper processors do not prevent device arming.

Buffers and joint slots are sized before periodic operation. Existing CAN FIFO
allocation/enqueue behavior is retained; this is not a hard real-time claim.
Timing diagnostics describe previous completed cycles and include duration
buckets (<=100, <=500, <=1000, <=5000, <=10000, >10000 microseconds), maxima,
scheduling lateness, and skipped deadlines. Device diagnostics identify missing
or aged feedback; they do not impose a new global stop policy.

## Configuration and ROS contracts

The existing robot-description xacro and controller YAML remain authoritative.
There is no second copy of CAN IDs, gearing, geometry, joint limits, or origins.
The node receives `robot_description` and the existing YAML filename through
`controller_config`.

| Existing YAML section | Use in new runtime |
| --- | --- |
| `controller_manager.ros__parameters.update_rate` | Control and measured joint-state publication rate (100 Hz in the rover file) |
| `rover_controller.ros__parameters` | Drive joint order, geometry, limits, slew, timeout, source topics, odometry masks/topic/frames/rate |
| `manipulator_controller.ros__parameters.joints` | Exact positional array order |
| `gripper_controller` or standalone `gripper_position_controller` | Gripper joint and corresponding command topic |
| `joint_state_broadcaster.ros__parameters.frame_id` when supplied | Joint-state frame; installed broadcaster default is `base_link` |

Manual `/base/cmd_vel` and autonomous `/cmd_vel` accept `geometry_msgs/msg/Twist`
with SystemDefaultsQoS. `/mission_status` uses
`mr2_action_interface/msg/MissionStatus`, depth 10. State 1 selects autonomous;
other states select manual. Both sources retain their own receipt times. A
source switch selects that source's buffered value at its original age.

Arm `/manipulator_controller/commands` and gripper
`/gripper_controller/commands` accept `std_msgs/msg/Float64MultiArray` with
SystemDefaultsQoS. Full-rover arm order is `arm_j1` through `arm_j6`. Standalone
arm order is `arm_j2` through `arm_j6`, and its gripper input is
`/gripper_position_controller/commands`. Malformed length or nonfinite values
reject the whole array and retain the last target. There is no new position
command timeout or trajectory interpolation.

`/joint_states` contains transformed feedback, not targets. Publication begins
only after the control thread has prepared its first complete snapshot. Missing velocity or
effort interfaces are represented as NaN. AK effort originates as motor current
in amperes, then follows the legacy transmission scaling; it is not a measured
physical joint torque. Existing boot-origin fallback and stale finite state
retention are preserved, so a reported finite position alone does not prove
fresh feedback. Check per-device diagnostics as well.

`/wheel_encoder/odometry` remains feedback twist with no integrated pose or TF;
its configured rate is 30 Hz, frames are `odom`/`base_link`, covariance diagonals
are 1e6 for pose and 0.05 for twist. Both wheel masks are preserved, including the
rover YAML's excluded FR drive wheel. The real launch retains passive rocker
joint-state publication for MoveIt.

The standalone xacro currently includes the full rover CAN block, despite its
five-joint arm controller configuration. This migration preserves that mapping:
other joints retain their initialized NaN position / zero velocity targets and
use existing driver hold behavior. It does not silently shrink the hardware
configuration or add `arm_j1` to the incoming array.

## Build and software tests

For normal rover operation, install into the workspace used by your launch shell:

```bash
cd /home/mr2/mr2-stack/rover/ros2_ws
source /opt/ros/humble/setup.bash
colcon build --symlink-install --cmake-clean-first --parallel-workers 2 \
  --packages-up-to mr2_control mr2_can_hardware_interface
source install/setup.bash
ros2 pkg prefix mr2_control
```

After building, source `install/setup.bash` in every terminal used to launch the
rover. The package prefix must point to this workspace's `install/mr2_control`.
The isolated test build below installs under `/tmp`; it does not install the
package into the normal workspace, and its artifacts may disappear after reboot.


From the repository root, with the existing supported workspace available:

```bash
source /opt/ros/humble/setup.bash
colcon --log-base /tmp/mr2-control-log build \
  --base-paths rover/ros2_ws/src \
  --build-base /tmp/mr2-control-build \
  --install-base /tmp/mr2-control-install \
  --packages-up-to mr2_control mr2_can_hardware_interface \
  --parallel-workers 3
source /tmp/mr2-control-install/setup.bash
ROS_DOMAIN_ID=187 ROS_LOCALHOST_ONLY=1 colcon test \
  --build-base /tmp/mr2-control-build --packages-select mr2_control
colcon test-result --test-result-base /tmp/mr2-control-build/mr2_control/test_results
```

The changed CanDevice vtable requires rebuilding both device plugins and CanHW
together. The command above does so. Do not mix old plugin binaries with the new
base header. Unrelated CAN clients retain the same public bus API.

To install the modified Python launch packages into the same temporary overlay,
use the existing workspace as their underlay (they also depend on unrelated
rover subsystems):

```bash
source rover/ros2_ws/install/setup.bash
source /tmp/mr2-control-install/local_setup.bash
colcon --log-base /tmp/mr2-launch-build-log build \
  --base-paths rover/ros2_ws/src/mr2_rover_description rover/ros2_ws/src/mr2_launch \
  --build-base /tmp/mr2-control-build \
  --install-base /tmp/mr2-control-install
source /tmp/mr2-control-install/local_setup.bash
```

Run the ROS integration checks, which force `mock_devices=true` and remap their
ROS endpoints to unique test names:

```bash
ROS_DOMAIN_ID=187 ROS_LOCALHOST_ONLY=1 python3 rover/ros2_ws/src/mr2_control/test/mock_integration.py
ROS_DOMAIN_ID=187 ROS_LOCALHOST_ONLY=1 python3 rover/ros2_ws/src/mr2_control/test/mock_integration.py --base-only
ROS_DOMAIN_ID=187 ROS_LOCALHOST_ONLY=1 python3 rover/ros2_ws/src/mr2_control/test/mock_integration.py --standalone
```

For an interactive hardware-free runtime:

```bash
ROS_DOMAIN_ID=187 ROS_LOCALHOST_ONLY=1 ros2 launch mr2_control mock.launch.py
```

This mock launch requires `mr2_rover_description` installed. It launches only the
new runtime with synthetic devices. It opens no SocketCAN interface. Synthetic
positions follow finite targets, and velocity modes integrate velocity; this is
an I/O/lifecycle fixture, not a physical rover dynamics model. The existing
`use_mock_servos` launch option is separate and still uses CAN; it is not the
hardware-free mode above.

Optional driver race instrumentation (after sourcing the built overlay):

```bash
cmake -S rover/ros2_ws/src/mr2_control -B /tmp/mr2-control-tsan-build -DMR2_ENABLE_TSAN=ON
cmake --build /tmp/mr2-control-tsan-build --target test_drivers -j2
ROS_DOMAIN_ID=187 ROS_LOCALHOST_ONLY=1 TSAN_OPTIONS=halt_on_error=1 /tmp/mr2-control-tsan-build/test_drivers \
  --gtest_filter=-Drivers.CancelActivationWhenRosContextStops
```

The excluded ROS-context cancellation test passes normally; its unfiltered
sanitizer run reports a mutex diagnostic inside the installed Fast DDS library
during ROS node construction. See [VALIDATION.md](VALIDATION.md) for the observed
result and the limits of the targeted sanitizer run.

## Launch, activation, and rollback

For a later supervised hardware validation, select the backend explicitly:

```bash
ros2 launch mr2_rover_description real.launch.py control_backend:=mr2_control can_iface:=can0
```

The wrappers `mr2_launch rover.launch.py` (with `mode:=real`),
`rover_real.launch.py`, and `rover_direct.launch.py` forward the same
`control_backend` argument. Each launch selects either Controller Manager and
its spawners, or the new node, never both. Simulation rejects an explicitly
requested new backend. Stop the previous launch before starting another;
selection inside one launch does not coordinate independently started processes.

The underlying real launch starts arm and gripper processors inactive. Its
`start_manipulator_controllers_active:=true` option applies to both backends.
The demo direct wrapper retains its existing **true** default; use
`start_manipulator_controllers_active:=false` there to start them inactive.

Standalone selection preserves inactive arm and active gripper defaults:

```bash
ros2 launch mr2_rover_description manipulator_ak_can.launch.py control_backend:=mr2_control
```

Replace Controller Manager's arm/gripper switch requests with:

```bash
ros2 service call /mr2_control/set_arm_active std_srvs/srv/SetBool '{data: true}'
ros2 service call /mr2_control/set_gripper_active std_srvs/srv/SetBool '{data: true}'
ros2 topic echo /mr2_control/diagnostics
```

A successful service response means the event was **queued**. Diagnostics report
the state after the next control boundary. Send fresh position commands after
activation: samples present at an activation boundary are discarded. Use
`{data: false}` to deactivate a processor. Deactivation retains its last target;
it does not disable the motor or promise a physical stop. Device activation and
processor activation are distinct. No hardware reset/rearm service is added;
restart the runtime for hardware reinitialization and a new boot reference.

Shutdown joins the control thread and stops each device’s actual bus worker first.
The runtime then routes the existing driver deactivation frames through its own
nonblocking SocketCAN sockets, opened during configuration. Pending normal targets
are discarded by the unchanged manager; they cannot follow the terminal writes.
Failed terminal writes are logged. Kernel acceptance is not a motor acknowledgement.
The per-device send override is unused by legacy CanHW and other bus clients.
Bus ownership is tracked by the device, avoiding separate header-only registry
instances across dynamically loaded libraries. Legacy CanHW also joins those
actual device buses before destroying callback owners. Rebuild both driver
plugins and CanHW after the CanDevice ABI change.

Rollback: stop the new launch and repeat it with `control_backend:=ros2_control`
(or omit that argument). The URDF and controller YAML are shared, and the legacy
backend remains available. No deployment, remote push, or host scheduling/CAN
configuration changes are needed to review this migration.

## Deliberate changes and retained limitations

- Separate source buffers implement the specification. Legacy rejected callbacks
  from the unselected source and kept the last accepted command across a switch;
  the replacement immediately selects the newly chosen buffered source.
- Preserve the actual final timeout zero of **all eight drive targets**, and
  zero steering at near-zero wheel speed, despite misleading legacy comments.
- Invalid position arrays are rejected as a unit. AK nonfinite commands use hold,
  and malformed/non-extended AK status frames are rejected before decoding.
- OutputActuator publishers are constructed before listener registration.
  Driver feedback, limits and diagnostics now use grouped snapshots, including
  process/lifecycle readers; hold state stays control-owned.
- Driver ROS telemetry is prepared by RX and published by executor timers at up
  to 100 Hz per topic, coalescing multiple updates between timer firings. AK
  timeout notices also leave the periodic path. CanHW has a telemetry-only ROS
  executor thread to preserve those topics for the legacy backend.
- Activation waits now notice ROS-context shutdown; partial activation is
  cleaned up instead of starting the periodic loop after cancellation.
- Shutdown writes terminal frames from runtime-owned sockets after stopping normal
  output. `CanBusManager` is byte-for-byte unchanged from the branch baseline.
- Existing OutputActuator lack of a feedback timeout, position holds, optional
  gripper behavior, boot-reference assumptions and current/effort limitations
  remain. There is no new centralized output supervisor, trajectory action
  server, physical homing, or global emergency-stop policy.
- Tests do not establish physical arming, motor response, bus load margins,
  stopping performance, sensor simultaneity, or hard real-time bounds. Kernel
  SocketCAN and physical CAN delay remain unmeasured; UNIX-socket measurements
  exercise the queue/wake/I/O path only.

## Validation results

See [VALIDATION.md](VALIDATION.md) for executed checks, measurements and limits.
