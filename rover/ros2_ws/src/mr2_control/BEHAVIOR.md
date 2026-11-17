# Migration inventory

Source: `2026-nepo-demo`, `3b24685c0d7153888f7bcbcf22cca5ea5ea85cb5`.
Specification reference: `74422f3`. Existing uncommitted user edits are outside
this migration. This inventory records source behavior, not hardware validation.

| Area | Confirmed source behavior | Source under `rover/ros2_ws/src` |
| --- | --- | --- |
| Drive selection | Mission state 1 accepts autonomous callbacks; other states accept manual. Legacy retains last accepted command across a source switch until a new accepted callback. New spec explicitly requires separate latest inputs and selection with original ages; switching therefore selects the buffered source. | `mr2_rover_control/src/twist_to_commands_controller.cpp` |
| Timeout | Desired twist becomes zero before slew, **and the end of update zeros all eight targets**. Preserve both. | same |
| Steering | FL/FR/RL/RR order; alternate angle/reversed speed chosen by wrapped distance; per-wheel saturation; global quadratic worst-steering-error gate. Near-zero wheel velocity leaves value-initialized steering at zero despite a hold comment. | same |
| Odometry | Feedback twist only; no pose integration or TF. Invalid wheel values treated as zero. Both masks required; FR drive masked in rover YAML. Pose covariance diagonal 1e6; twist diagonal .05. | same; `mr2_rover_description/config/controllers/rover_controllers.yaml` |
| Rates/QoS | 100 Hz control; odometry 30 Hz; Twist SystemDefaultsQoS; mission depth 10; odometry depth 10. Joint-state broadcaster executes each control update (its nested publish_rate setting is not a verified throttle). | controller and YAML |
| Arm | Full rover j1..j6; standalone j2..j6. Position array forwarding, no timeout/interpolation. Servo emits positions at 50 Hz. | controller YAMLs; `mr2_moveit/config/servo.yaml` |
| Activation | Real launch defaults arm/gripper inactive, explicit start-active option. Demo direct launch/script requests active. Standalone arm inactive, gripper_position_controller active. | `mr2_rover_description/launch/real.launch.py`, `manipulator_ak_can.launch.py`; `mr2_launch/launch/rover_direct.launch.py`; `scripts/run_rover_direct.bash` |
| Conversion | Position/velocity commands, position required; optional velocity/current-like effort. Simple and FourBar library transformations; first finite converted feedback captures optional boot reference. Invalid feedback retains previous finite reported value, initially origin/zero. NaN position command passes through; invalid motor velocity becomes zero. Position clamps precede inverse origin correction. | `mr2_can_hardware_interface/src/can_hw.cpp` |
| Mapping | Left wheel reduction -1; right +1. Arm j1 -2, coupled j2/j3 actuator reductions 50, joint reductions -1/+1; j4 +1, j5/j6 -1. Boot offsets j3 -pi/4, j5 3pi/4. URDF remains authoritative. | `mr2_rover_description/ros2_control/rover_can.ros2_control.xacro` |
| AK | 0.5 s feedback timeout suppresses TX, resumes after feedback recovery; NaN command holds measured position; big-endian signed angle at degrees*1e4, extended CAN 0x400+ID. Current feedback is amperes, not measured joint torque. | `mr2_devices_ak_servo/src/ak_servo_device.cpp` |
| OutputActuator | Profile/config/calibration/limits checks and confirmed arming before active. Invalid angle uses hold; invalid velocity zero. Velocity integrates position. No feedback timeout in process. Optional absent gripper remains inactive. Deactivate queues zero velocity/disarm. | `mr2_devices_output_actuator/src/output_actuator_device.cpp` |
| CAN | Existing FIFO and wake pipe; direct RX callback dispatch under listener lock. No raw receive queue or command mailbox. Stop I/O before callback owners die. | `mr2_can_bus_core/include/mr2_can_bus_core/` |

## Concrete migration corrections / differences

- Separate protected manual/autonomous buffers are required by the specification;
  source-switch behavior differs from legacy callback rejection as recorded above.
- Validate finite arm/gripper arrays as one unit; malformed input retains the last
  target. Do not add a timeout or activate inactive processors implicitly.
- Driver plain feedback fields and some hold/integration state are currently
  shared unsafely. Replace these accesses with coherent, short locked snapshots,
  including process and lifecycle readers. Output publishers must be initialized
  before registering callbacks.
- Preserve branch additions: optional gripper, passive rocker feedback, direct
  launch activation options, and stop-before-destruction behavior.

- The standalone xacro reuses the full rover CAN block. Its unclaimed joints
  retain initialized hold/zero targets; the five-element arm array is unchanged.
- Driver telemetry now publishes from 100 Hz executor timers, coalescing updates;
  legacy CanHW runs a telemetry executor so its device topics remain available.
- Shutdown stops the normal bus worker before sending existing deactivation frames
  through runtime-owned sockets. The shared manager retains its original behavior.
  Socket write failures are logged; no motor acknowledgement is implied.

## Implementation sequence

1. Ordinary converter and device collection using existing URDF parser and
   transmission library, without SystemInterface or Controller Manager execution.
2. Pure target processors, protected input and telemetry buffers, explicit
   monotonic control scheduling and serialized activation events.
3. Coherent driver access, ROS contracts and mutually exclusive launch backend.
4. Build affected packages in an isolated build/install tree; hardware-free
   numeric, lifecycle, concurrency, launch and scheduling validation. Record
   actual results and limitations in README.

- Command subscriptions start after successful device activation, preventing DDS
  from replaying commands sent only during the activation wait with fresh receipt
  timestamps. Continuing commands after startup are accepted normally.
