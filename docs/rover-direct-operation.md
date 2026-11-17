# Rover-direct operation

This mode removes the separate base-station computer. The Jetson Orin Nano
runs the rover ROS 2 stack, gateway, video relay, dashboard, nginx, and Wi-Fi
access point as native host processes. Development, builds, tests, and runtime
all run natively on the Jetson over SSH.

## Topology

```text
Laptop browser
  |
  | HTTPS / WSS over the Jetson AP (10.42.0.1)
  v
Jetson nginx :443
  |-- /xbee-ws -----------------> gateway 127.0.0.1:8081
  |-- /video/streams,/video-ws -> gateway 127.0.0.1:8081
  `-- /rosbridge-ws -----------> rosbridge 127.0.0.1:9090

gateway /tmp/mr2_xbee_gateway
  <---- socat PTY pair ---->
rover bridge /tmp/mr2_xbee_rover

rover cameras -- RTP/H.264 --> 127.0.0.1 UDP ports --> gateway --> browser
```

The direct ROS launch owns the local XBEE PTY pair and Node.js gateway, then
starts real ros2_control, drive/steering, battery monitoring, MoveIt Servo, the
XBEE bridge, rosbridge, and rover video encoding. It does not start dual GNSS,
NTRIP, localization, Nav2/autonomy, science, panorama, or autonomous
perception.

The gateway `rover-direct` profile retains XBEE control/telemetry and video
relay. It disables MAVProxy, base antenna tracking, Rocket M2 polling, and the
base GNSS/RTCM ROS relay.

The dashboard `rover-direct` profile exposes Status, Live Feed, and Arm views.
The persistent drive/steering controls remain available. Multiple browsers may
connect and send commands; this mode does not add an exclusive controller
lease.

## Jetson prerequisites

- ROS 2 Humble and the repository's normal rover hardware dependencies
- Node.js 24 (the repository pins `24.15.0` in `.nvmrc`)
- nginx, OpenSSL, rsync, socat, and NetworkManager (`nmcli`)
- GStreamer plus Jetson `nvv4l2h264enc`/`nvvidconv`
- a built rover workspace at `rover/ros2_ws/install/setup.bash`
- configured `can0` and camera udev aliases from the existing rover setup

With NVM installed, select the repository version once and make it the shell
default:

```bash
nvm install
nvm alias default 24.15.0
nvm use
```

Build the ROS workspace natively on the Jetson:

```bash
cd rover/ros2_ws
source /opt/ros/humble/setup.bash
rosdep install --from-paths src -y --ignore-src --rosdistro humble
colcon build --symlink-install
```

Run the complete native gateway, dashboard, ROS build, and test sequence from
the repository root when needed:

```bash
./scripts/check_rover_direct.bash
```

## Configure the Jetson AP

This deployment uses `10.42.0.1/24`. The script still defaults to
`192.168.2.102/24`, so set the address explicitly to match the dashboard
installation below. Select the actual Jetson Wi-Fi interface (`wlP1p1s0` on
this rover) and enter the WPA2 password at the hidden prompt:

```bash
sudo MR2_AP_INTERFACE=wlP1p1s0 \
  MR2_DIRECT_AP_CIDR=10.42.0.1/24 \
  ./scripts/configure_rover_ap.bash
```

For noninteractive use, supply `MR2_AP_PASSWORD` through the environment and
preserve it through sudo. NetworkManager stores the resulting connection as
`MR2-Rover-AP` and brings it up with IPv4 shared mode. Configure the AP once,
or when changing its settings; doing so can disconnect SSH over that Wi-Fi link.

## Install the native dashboard for manual launch

From the repository root on the Jetson:

```bash
MR2_DIRECT_AP_IP=10.42.0.1 \
  ./scripts/install_rover_direct.bash --manual
```

This builds and deploys the direct dashboard, installs the nginx
configuration/certificate, and starts/enables and reloads nginx. Run it during
initial setup or to deploy a dashboard update; reload the browser afterward.
It requires an already built ROS workspace and does not rebuild ROS packages.
Manual mode also stops and disables `mr2-rover-direct.service`, preventing it
from racing a directly executed `rover_direct.launch.py`. Review CAN and camera
readiness before launching.

The generated self-signed certificate includes both rover addresses
`192.168.0.62` and `10.42.0.1` (plus `rover` and `rover.local`). Re-running the
installer replaces an older rover-direct certificate when either IP SAN is
missing.

Systemd-managed operation remains available as an alternative. Enable at boot
without starting now:

```bash
MR2_DIRECT_AP_IP=10.42.0.1 \
  ./scripts/install_rover_direct.bash --skip-build --enable
```

Enable and start:

```bash
MR2_DIRECT_AP_IP=10.42.0.1 \
  ./scripts/install_rover_direct.bash --skip-build --start
```

The unit is `mr2-rover-direct.service`. Its launch owns the PTY pair, gateway,
video relay, and rover ROS processes as one lifecycle.

Inspect them with:

```bash
systemctl status mr2-rover-direct
journalctl -u mr2-rover-direct -f
```

After joining the `MR2-Rover` Wi-Fi network, open:

```text
https://10.42.0.1
```

The certificate is self-signed. Install/trust it on the operator laptop if
browser warnings are unacceptable.

## Direct native start (primary mode)

Routine startup uses the installed dashboard and AP configuration. nginx
does not need restarting when already active. With any existing terminal
rover launch stopped, prepare the hardware from the repository root:

```bash
cd /home/mr2/mr2-stack
sudo systemctl start nginx
sudo systemctl stop mr2-rover-direct

# Install once, and again when the rules change.
./scripts/install_udev_serial_rules.bash

# Configure CAN when down or unconfigured. This resets the CAN interface.
ip -details link show can0
./scripts/can0.bash

# Start the complete direct stack; keep this terminal open.
./scripts/run_rover_direct.bash
```

For drive-only operation with an absent or unpowered arm, replace the last
command with `MR2_ENABLE_MANIPULATOR_MODULE=false ./scripts/run_rover_direct.bash`.
Do not run CAN setup while the rover stack is operating. The wrapper sources
ROS and selects the pinned Node runtime automatically.

Alternatively, after the same hardware preparation, source the built
workspace and launch with explicit ROS arguments:

```bash
cd /home/mr2/mr2-stack/rover/ros2_ws
source /opt/ros/humble/setup.bash
source install/setup.bash
ros2 launch mr2_launch rover_direct.launch.py \
  enable_manipulator_module:=true \
  enable_video_streaming:=true \
  enable_autonomous_module:=false \
  enable_led:=false \
  enable_camera_turret:=false
```

The launch resolves Node.js directly from the repository `.nvmrc`, so no
separate `nvm use` step is required. nginx remains a privileged host service
installed separately by `install_rover_direct.bash`.

### Optional launch features

The manipulator is enabled by default. Its six AK arm motors must publish CAN
feedback before the driver will transmit position commands. If the arm is not
installed or powered, start a drive-only stack to avoid exposing inactive arm
controllers:

```bash
ros2 launch mr2_launch rover_direct.launch.py \
  enable_manipulator_module:=false
```

Gripper node 9 is optional at hardware activation. When it is absent or
offline, the gripper remains inactive and emits no actuator commands, while
the rover and six-axis manipulator controllers continue to start. A healthy
gripper still activates normally.

Real-hardware launch also publishes the fixed zero state of the passive left
rocker joint. MoveIt Servo requires that passive state in addition to the
actuated CAN joint states before it forwards dashboard arm commands.

If the arm controllers are active but the physical arm does not move, verify
feedback from motor IDs 101 through 106 without sending a motion command:

```bash
./scripts/check_ak_actuator_status.bash --iface can0 --duration 3
```

A zero-frame result means either the arm power/CAN wiring/termination,
motor-side bitrate/IDs, or the motor communication mode must be corrected. The
driver uses CubeMars Servo Direct Mode and expects periodic extended `0x29xx`
status frames. Motors configured for MIT mode or query-response feedback do not
satisfy that requirement even when they are powered and connected. Configure
IDs 101 through 106 for Servo Mode, the same bitrate as `can0`, and periodic CAN
status feedback. The driver intentionally does not send a blind position
command before it has captured a valid boot position.

Common feature arguments are:

| Argument | Default | Purpose |
| --- | --- | --- |
| `enable_manipulator_module` | `true` | Include arm/gripper hardware, controllers, and MoveIt Servo |
| `start_manipulator_controllers_active` | `true` | Start arm/gripper controllers active instead of inactive |
| `enable_video_streaming` | `true` | Stream configured cameras to the local gateway |
| `enable_autonomous_module` | `false` | Start RGB-D/front cameras and YOLO perception; not GNSS/localization/Nav2 |
| `enable_aruco` | `false` | Start ArUco tracking on `aruco_cam_topic` |
| `enable_led` | `false` | Start status and mission LED CAN nodes |
| `enable_camera_turret` | `false` | Start the camera-turret CAN node |
| `headless` | `true` | Disable RViz |
| `start_xbee_sim` | `true` | Own the local rover/gateway PTY pair |
| `start_gateway` | `true` | Start the local Node.js dashboard gateway |
| `ensure_nginx` | `true` | Require/start the nginx system service for HTTPS |
| `start_rover` | `true` | Start the hardware-facing ROS rover stack |

Run the following for every available argument and its description:

```bash
ros2 launch mr2_launch rover_direct.launch.py --show-args
```

The systemd service reads matching settings from the repository-root `.env`, such as
`MR2_ENABLE_MANIPULATOR_MODULE=false`, `MR2_ENABLE_VIDEO_STREAMING=false`,
`MR2_ENABLE_LED=true`, and `MR2_ENABLE_CAMERA_TURRET=true`.
When running `run_rover_direct.bash` directly, export or prefix these variables
in the shell; the script does not load `.env` itself.

nginx configuration is installed and reloaded by `install_rover_direct.bash`;
it does not need to be regenerated for each launch. The unified
`mr2-rover-direct.service` requires `nginx.service`, so starting the rover unit
starts nginx first. A manual `ros2 launch` checks nginx and makes a
non-interactive start attempt. If local policy does not permit that operation,
run `sudo systemctl start nginx` once or set `ensure_nginx:=false` when the
dashboard is intentionally unused.

## Scope and hardware validation

This mode uses the existing XBEE protocol, ROS drive controller, arm Servo
command path, software E-stop UI, and command deadman handling. On
`2026-nepo-demo`, the dashboard also provides
[Orbit controls](./nepo-demo-operation.md#circular-driving). Additional safety
interlocks and multi-browser command arbitration are separate work.

Before field operation, validate on the actual Jetson and rover:

- AP association and the configured `10.42.0.1` address
- browser connections through all four nginx paths
- every configured camera stream and browser H.264 decode
- controller states with `ros2 control list_controllers`
- zero-command CAN startup, then deliberate drive/steering and arm tests
- reconnect behavior after restarting the unified rover-direct service
