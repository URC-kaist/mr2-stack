# MR2 Control: Implementation Specification for Codex

Date: 2026-09-11\
Repository: `URC-kaist/mr2-stack`\
Reference inspected during design: branch `2026`, commit `74422f3`\
Status: implementation handoff; no replacement has been implemented by this document.

## 1. Your task

Implement `mr2_control`, a project-specific replacement for the ros2_control runtime used to operate the rover's drive wheels, steering joints, arm, and gripper. Inspect the actual checkout, make the changes, build what the environment supports, and validate the replacement without commanding physical hardware. Do not stop after producing a plan or scaffolding.

This document is self-contained and records the final architecture agreed with the user. Earlier alternatives involving a separate device-processing thread and a shared motor-command mailbox have been superseded.

The objective is explicit execution order and data ownership, reduced framework indirection, and easier debugging. It is not a claim that removing ros2_control automatically improves latency or real-time performance.

Preserve existing behavior wherever possible. Separate structural migration from behavior changes. Choose routine implementation details autonomously; investigate unresolved behavior in the code before asking questions. Report genuine blockers and finish independent authorized work.

## 2. Scope and constraints

### Replace or adapt

- Controller Manager's periodic execution and controller orchestration for these actuators.
- ros2_control controller interfaces and the command/state handles between controllers and CanHW.
- CanHW's configuration, joint/actuator mapping, transformations, and driver orchestration, as ordinary C++ components.
- Joint-state and wheel-odometry output integration.
- Launch and configuration needed to select the replacement runtime.
- Synchronization of shared ROS inputs and driver feedback used by the new runtime.

### Preserve

- ROS2 and the existing deployment's distribution and build conventions.
- Dashboard, XBee transport, gateway, and ROS bridge behavior.
- Nav2 and MoveIt Servo.
- Robot geometry, TF conventions, joint names, joint order, and kinematic configuration.
- Existing device protocols, CAN IDs, units, bus configuration, and device-specific activation procedures.
- CanBusManager's normal responsibilities: SocketCAN I/O, mutex-protected TX queue, wake notifications, and direct dispatch of receive callbacks.
- Unrelated users of the CAN library, including battery, LED, camera-turret, and science components.

Do not introduce a separate device-processing thread, a new raw-CAN receive queue, or a cross-thread motor-command mailbox. Do not redesign unrelated packages or implement a general replacement for every ros2_control feature.

This migration does not require implementing MoveIt IK, trajectory planning, or a FollowJointTrajectory server. Reassess only if the actual target checkout has added a concrete dependency on such functionality.

Work within the repository and existing development environment. Preserve unrelated changes. Do not send physical motor commands, change host system configuration, deploy to the rover, or push/merge remotely as part of this implementation handoff.

## 3. Final execution model

Use one dedicated control thread plus the existing CanBusManager I/O thread(s). ROS subscription callbacks and telemetry publication run through an executor outside the periodic control path.

There is not necessarily only one CAN thread for the entire system: retain existing per-interface/per-process ownership. Sharing a CanBusManager class or registry does not imply sharing an object between processes.

| Execution context | Responsibilities |
| --- | --- |
| Control thread | Input snapshots; feedback snapshots; motor-to-joint state conversion; command selection; drive/arm/gripper target processing; joint-to-motor conversion; driver command updates; driver `process()` calls; CAN frame creation and enqueueing |
| CanBusManager I/O thread | Drain the existing TX queue; write to SocketCAN; receive frames; invoke the registered driver callbacks, including frame decoding and driver feedback updates |
| ROS executor | Receive upper-level commands; update input buffers; publish prepared joint states, odometry, and diagnostics; accept supported management requests |

`AkServoDevice` and `CustomActuatorDevice` are C++ objects, not independent processes or threads. A normal call to `device.process()` executes on its caller's control thread. A receive callback invoked by CanBusManager executes on the CAN I/O thread.

The existing architecture already executes controller update, CanHW write, and driver transmit processing sequentially on the control thread. Preserve that placement.

## 4. Control components: 1 + 3 + 1

Keep these roles as ordinary C++ components. They do not each need a ROS node, thread, plugin, or generic interface hierarchy.

| Component | Inputs | Processing | Outputs |
| --- | --- | --- | --- |
| Drive command selector (1) | Manual command, autonomous command, mission state, timestamps | Existing source-selection rules | Selected chassis velocity command with its original timing metadata |
| Drive target processor (1 of 3) | Selected chassis velocity, joint feedback, actual dt | Existing slew limits, rover kinematics, steering solution selection, speed limits and steering-error gating; wheel odometry | Wheel angular-velocity and steering-position targets; odometry result |
| Arm target processor (2 of 3) | Servo joint-position array, feedback, activation state | Validate length/order/values; preserve relevant command and activation behavior | Arm joint-position targets |
| Gripper target processor (3 of 3) | Gripper position command, feedback, activation state | Validate and apply existing target-handling behavior | Gripper joint-position target |
| Unified joint/motor converter (1) | Motor feedback or joint targets; limits; boot origins; transmission configuration | Bidirectional state/command conversion, joint position limits, origin corrections, gearing and coupling | Joint feedback or motor targets |

The converter is called in both directions: feedback conversion before target processing, command conversion afterward. The arm processor receives joint positions already calculated by MoveIt Servo; it does not repeat Cartesian-to-joint calculations.

A thin driver collection/adapter may own driver instances and expose lifecycle, snapshot, command, and process operations. It is a convenience within the control runtime, not an additional execution layer or thread.

### Illustrative cycle

Names are illustrative, not a mandatory API:

```cpp
void ControlLoop::step(TimePoint now, Duration dt) {
    inputs_.copyLatestInto(input_snapshot_);
    devices_.copyFeedbackInto(motor_feedback_);
    converter_.motorToJoint(motor_feedback_, joint_feedback_);

    selector_.select(input_snapshot_, selected_drive_);
    drive_.update(selected_drive_, joint_feedback_, now, dt, joint_targets_);
    arm_.update(input_snapshot_.arm, joint_feedback_, now, dt, joint_targets_);
    gripper_.update(input_snapshot_.gripper, joint_feedback_, now, dt, joint_targets_);

    converter_.jointToMotor(joint_targets_, joint_feedback_, motor_targets_);
    devices_.applyCommands(motor_targets_);
    devices_.process(now);  // Same thread: encode frames and enqueue CAN TX.
    telemetry_.submit(joint_feedback_, drive_.odometry(), cycle_diagnostics_);
}
```

Use preallocated storage and clearly owned target slots. Define inactive/invalid output handling so a skipped processor cannot accidentally expose uninitialized or unintended old targets.

## 5. External ROS contract

Preserve these inspected interfaces, verifying message types, remappings, QoS, parameters, frames, and rates in the actual checkout:

| Direction | Topic | Meaning |
| --- | --- | --- |
| Input | `/base/cmd_vel` | Manual chassis velocity (`geometry_msgs/msg/Twist` in the inspected configuration) |
| Input | `/cmd_vel` | Autonomous chassis velocity |
| Input | `/mission_status` | Mission state used for source selection; retain its existing message type |
| Input | `/manipulator_controller/commands` | Servo joint positions (`std_msgs/msg/Float64MultiArray`) |
| Input | `/gripper_controller/commands` | Gripper position (`std_msgs/msg/Float64MultiArray`) |
| Output | `/joint_states` | Measured, transformed joint feedback |
| Output | `/wheel_encoder/odometry` | Existing wheel-feedback odometry |

Retain joint order explicitly. Float64MultiArray carries no joint names; never assume automatic name-based reordering. Check the full-rover and standalone-arm configurations separately.

For command callbacks, copy payload, receipt timestamp, and validity metadata together into a protected latest-value buffer. The control thread reads a consistent copy and processes it after releasing the lock. Re-reading a command does not refresh its receipt timestamp. Preserve source timestamps separately where relevant.

Keep manual and autonomous inputs separate until selection. Preserve the selected source's age. Latest-value semantics suit continuous commands; initialization, reset, and activation requests require explicit management/event semantics if supported.

Prepare telemetry in the control thread and publish through the ROS side without holding control/driver locks. `/joint_states` must represent feedback, not commanded positions.

## 6. Driver boundary and synchronization

### Command path: retain same-thread processing

The converter produces targets in the axis coordinates expected by each device. The runtime writes those targets to drivers and calls `process()` on the same control thread. Driver code performs protocol scaling, frame encoding, and existing TX enqueueing.

No mutex or shared mailbox is required merely to pass a target between two sequential functions on this thread. Audit any command-related variable also accessed by a receive callback; such a variable is genuinely shared and needs synchronization.

An explicit command setter and state-copy API is preferred where it clarifies ownership. Existing command pointers may remain behind a compatible adapter if accesses are proven to be confined appropriately. Avoid gratuitously rewriting protocol code or breaking the legacy backend.

### Feedback path: correct cross-thread access

Receive callbacks continue to decode frames on the CanBusManager thread. They update driver feedback. The control thread reads that feedback both during state conversion and inside some drivers' `process()` methods.

- Group related feedback fields and timing/validity metadata into a snapshot structure.
- Protect updates and copies with short, appropriately scoped mutex critical sections as the initial implementation.
- Decode into local values where practical; lock to commit the consistent feedback update.
- Copy under lock, then perform transformations, command generation, logging, and CAN enqueueing after unlocking.
- Audit every reader, including `process()`, activation checks, timeout checks, and diagnostics. Protecting only the converter's reads is insufficient.
- An atomic timestamp does not synchronize unrelated plain position/velocity fields by itself.
- Avoid nested locks and waits while holding a feedback mutex. Verify interactions with existing listener, TX, lifecycle, and condition-variable locks.
- Copying different devices' states does not make their measurements simultaneous; retain per-device receive times.
- Do not mislabel current feedback as physical joint torque. Preserve documented units and limitations.

A mutex-based implementation improves correctness but is not proof of hard real-time behavior. Keep critical sections bounded in work and measure contention. Do not introduce custom lock-free mechanisms without a concrete need.

### CanBusManager compatibility

Preserve the existing mutex-protected FIFO transmit queue. `enqueue_tx()` copies a frame and writes to a wake pipe; the I/O thread is not merely waiting for the next control tick. `flush_tx_()` removes frames and writes them to SocketCAN.

Do not change FIFO behavior into latest-command replacement during this migration. Preserve special shutdown/final-transmit behavior if present in the checkout. Successful enqueue or socket write is not acknowledgement that a motor applied the target.

## 7. Behavior to preserve and verify

Build a concise behavior inventory from the source before implementation. Record relevant file locations and distinguish confirmed behavior from assumptions.

### Drive

Preserve source selection, command timeout, acceleration/slew limiting, wheel/steering mapping, geometry, angle wrapping and alternate steering solutions, speed saturation, steering-error gating, odometry calculation, masks, frames, and covariance settings.

In the inspected code, mission RUNNING selects autonomous input and other states select manual input. Timeout zeros the desired chassis twist before slew limiting. A helper named `publishZeros()` writes zero to steering targets as well as wheel velocities. Verify these details; do not silently reinterpret them as immediate braking or steering hold.

### Arm and gripper

Preserve the actual ordering, activation defaults, last-target behavior, and startup behavior. The inspected arm/gripper controllers are position forwarding controllers, not trajectory interpolators or PID implementations.

Inspect how controllers become active in real deployments. The inspected full-rover launch spawned arm and gripper inactive; standalone launches differ. Identify scripts or operator workflows that activate them. Provide a documented equivalent in the replacement where necessary; never silently activate them at startup just because the old spawner is removed.

Do not invent a new arm/gripper command timeout or global stop policy solely because the runtime is changing. Document existing omissions separately from required migration fixes.

### CanHW conversion and initialization

Preserve or explicitly replace all relevant responsibilities:

- Validate supported command/state modes and configuration.
- Build joint/actuator name mappings and avoid duplicate device initialization.
- Instantiate/configure device drivers and connect the selected transmission implementations.
- Preserve position, velocity, and available effort/current semantics.
- Preserve activation resets, initial command values, and device activation/deactivation calls.
- Preserve position clamping, invalid-value handling, and startup fallback behavior.
- Preserve SimpleTransmission and any coupled/FourBar transformation used by the target configuration, in both directions.

For a joint using `origin_offset`, the inspected code stores the first finite transmission-converted position after activation as its boot reference:

```text
reported_joint_position = transformed_position - boot_reference + origin_offset
transmission_position_target = joint_target - origin_offset + boot_reference
```

Without `origin_offset`, do not apply this boot-reference correction. This is an assumed startup reference, not physical homing or proof of persistent absolute joint position. Preserve sign, ratio, units, reference-reset timing, and command behavior before a valid reference exists.

### Device-specific behavior

Reuse AK protocol handling and OutputActuator configuration/arming logic. Verify feedback timeout, first-feedback hold, invalid-command handling, recovery, and shutdown behavior against the current code.

Skipping transmissions is not necessarily a physical motor stop. Holding a position, zeroing velocity, and disabling output are different actions. Preserve their actual meanings.

Allow activation to wait for incoming feedback only where the CAN I/O thread remains able to run and update it. Do not hold feedback locks across such waits or execute long initialization inside the steady-state periodic loop.

## 8. Error handling and lifecycle

Keep handling near the component that understands the fault. Do not add a mandatory centralized output-state supervisor.

| Concern | Primary owner |
| --- | --- |
| Command source selection | Drive selector |
| Command validity and existing timeout behavior | Corresponding target processor |
| Limits, boot reference, transformation validity | Joint/motor converter |
| Device feedback health, protocol errors, arming | Device driver |
| Socket/CAN transport errors | CanBusManager |
| Control deadlines and runtime start/stop | Control loop/runtime |

Provide identifiable diagnostics: component/device, condition, and action. Coordinate multi-joint responses only where existing behavior requires them or a concrete migration issue demands it; document deviations.

Initialize/configure before periodic operation. Serialize management changes that affect control-owned state, preferably at a defined control-cycle boundary. Define clean shutdown ordering and driver lifetimes so no callback uses destroyed objects.

If a serious pre-existing defect prevents a coherent implementation, make a narrow, justified correction with focused validation and report the behavior change. Do not automatically expand the project into a safety-system redesign.

## 9. Timing and memory

- Use the configured existing control rate, initially 100 Hz for the inspected rover configuration.
- Use a monotonic clock for real-hardware scheduling and local elapsed-time checks. Keep ROS message/simulation time semantics explicit.
- Schedule against successive absolute deadlines, not a fixed sleep after computation.
- Pass actual elapsed dt to calculations that need it.
- Detect overruns and avoid an unbounded burst of catch-up iterations; document the chosen missed-deadline policy.
- Allocate and size normal-cycle buffers during initialization where practical.
- Keep ROS publication, long logging operations, initialization waits, and configuration parsing out of the periodic calculation path.
- Retain existing protocol enqueue behavior unless a measured issue requires a narrow change.
- Do not claim hard real-time guarantees or change host scheduling settings automatically.

Measure control timing and, where practical, target-to-socket delay and feedback age. Average loop time alone is insufficient; report observed maxima/tail behavior and the test conditions. Measurement under a test load is not a worst-case guarantee.

## 10. Suggested package layout

Create `rover/ros2_ws/src/mr2_control/`. Exact filenames are flexible; this table illustrates the intended boundaries.

| Path within package | Purpose |
| --- | --- |
| `package.xml`, `CMakeLists.txt` | Build and dependency declarations |
| `src/mr2_control_node.cpp` | Node startup and component ownership |
| `include/mr2_control/types.hpp` | Command, feedback, target, and configuration structures |
| `include/mr2_control/control_loop.hpp`, `src/control_loop.cpp` | Explicit scheduling and cycle orchestration |
| `include/mr2_control/drive_selector.hpp`, `src/drive_selector.cpp` | Manual/autonomous selection |
| `include/mr2_control/drive_processor.hpp`, `src/drive_processor.cpp` | Drive targets and odometry |
| `include/mr2_control/arm_processor.hpp`, `src/arm_processor.cpp` | Arm command handling |
| `include/mr2_control/gripper_processor.hpp`, `src/gripper_processor.cpp` | Gripper command handling |
| `include/mr2_control/joint_motor_converter.hpp`, `src/joint_motor_converter.cpp` | Bidirectional transformations and origin/limit handling |
| `include/mr2_control/device_adapter.hpp`, `src/device_adapter.cpp` | Driver collection, lifecycle, snapshots, command application |
| `include/mr2_control/ros_io.hpp`, `src/ros_io.cpp` | Topic callbacks, input buffers, telemetry |
| `config/`, `launch/` | Configuration and launch integration where not owned by existing bringup packages |
| `test/`, `README.md` | Focused validation and operator/developer documentation |

Reuse existing driver and bus packages rather than copying them into this package. Shared pure computation can be extracted when that reduces duplication and preserves legacy behavior.

The new runtime must not rely on Controller Manager, ControllerInterface, or SystemInterface for periodic execution or command routing. Remove unnecessary ros2_control coupling; a narrowly justified parser/transmission dependency is acceptable when it avoids a risky rewrite. Document retained dependencies instead of deleting every library associated with ros2_control indiscriminately.

Use one authoritative source for joint/device/transmission parameters where possible. If configuration must be migrated, document field mappings and avoid contradictory URDF/YAML copies. Keep robot-model content needed by MoveIt, TF, and visualization.

## 11. Repository investigation guide

All paths below are relative to `rover/ros2_ws/src/` and must be verified against the target checkout:

| Area | Starting points |
| --- | --- |
| Bringup | `mr2_launch/launch/rover_real.launch.py`, `mr2_launch/launch/rover.launch.py` |
| ros2_control startup | `mr2_rover_description/launch/real.launch.py`, `mr2_rover_description/mr2_rover_description/launch_common.py` |
| Controller configuration | `mr2_rover_description/config/controllers/rover_controllers.yaml`; standalone manipulator configuration and launches |
| Drive behavior | `mr2_rover_control/src/twist_to_commands_controller.cpp` |
| Hardware conversion | `mr2_can_hardware_interface/src/can_hw.cpp` and its transmission implementations/configuration |
| Device abstraction and bus | `mr2_can_bus_core/include/mr2_can_bus_core/can_device.hpp`, `can_bus_manager.hpp`, `can_bus_registry.hpp` |
| AK driver | `mr2_devices_ak_servo/src/ak_servo_device.cpp` |
| CustomActuator driver | `mr2_devices_output_actuator/src/custom_actuator_device.cpp` |
| Upper-level arm contract | `mr2_moveit/config/servo.yaml`, Servo launch, controller configuration |
| Manual command path | `mr2_xbee_bridge/src/xbee_bridge_node.cpp` |

Read applicable repository instructions and inspect the working tree before edits. Record the actual branch/commit used. Current source is authoritative about existing behavior; this document is authoritative about the intended replacement architecture. Explain material differences from the inspected reference.

## 12. Implementation and migration workflow

1. Trace the current launch, controller activation, command paths, state paths, mappings, and device lifecycle. Produce a compact inventory of behaviors to preserve and concrete gaps.
2. Implement the ordinary C++ components and explicit control loop. Reuse existing algorithms and configuration semantics.
3. Adapt driver access for coherent feedback snapshots, auditing all cross-thread readers and writers while preserving CanBusManager responsibilities.
4. Integrate ROS inputs/outputs and launch configuration. Keep the legacy runtime selectable during validation, with an explicit mutually exclusive backend option. Preserve the legacy default until hardware validation supports changing it.
5. Ensure only one backend controls the same actuators and publishes the corresponding authoritative feedback/odometry in a launch. Preserve simulation and optional-subsystem configurations; they may continue using the legacy backend unless the new backend is explicitly supported there.
6. Build and run focused tests in mock, replay, or virtual-CAN environments supported by the repository. Do not treat an arbitrary CAN interface as safe test hardware.
7. Document results, retained limitations, activation procedures, rollback, and any behavior changes. Finish with a concrete change summary and validation evidence.

## 13. Validation and acceptance criteria

Select meaningful tests for the remaining risks; avoid broad test scaffolding that merely mirrors implementation.

- Build the new package and affected dependencies in the supported environment. State missing dependencies or environment limitations accurately.
- Compare old and new drive calculations for representative manual/autonomous commands, source switches, timeout, steering wrap/reversal, limits, and odometry.
- Verify arm/gripper array size/order, startup state, activation, command forwarding, and malformed input behavior.
- Check forward/reverse conversions for signs, ratios, coupled transmissions, boot-origin initialization/reset, clamps, and invalid feedback. Use representative boundary values and expected results independent of the new implementation where possible.
- Confirm driver frame bytes for representative targets remain equivalent to the existing encoding.
- Exercise concurrent feedback updates and control reads, including driver `process()` and lifecycle readers. Use targeted stress/race instrumentation if supported; otherwise state the coverage limitation.
- Verify startup, shutdown, inactive controllers, missing feedback, and existing recovery behavior with mocks.
- Verify ROS contract compatibility, measured joint-state publication, odometry, and mutually exclusive backend selection.
- Measure scheduling and feedback-age behavior where the test environment permits. Do not require physical movement to complete software validation.

The deliverable is complete when the new backend implements the specified execution path, preserves the verified contracts, has no known unsynchronized accesses in its shared driver feedback path, and has documented build/test results and operational limitations. Physical validation may remain an explicitly documented follow-up; do not describe untested hardware behavior as verified.

## 14. Final report expected from Codex

Report what changed and why, the resulting execution/thread model, how existing drivers and configuration were reused, tests actually executed and their results, any behavior deviations or blockers, and exact launch/activation/rollback instructions. Include a concise thread/data-ownership table in the repository documentation so the distinction between objects and threads remains clear.
