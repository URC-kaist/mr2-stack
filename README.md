### Quick memo on useful commands

### Docs

- [docs/README.md](docs/README.md): top-level documentation index
- [docs/video-pipeline.md](docs/video-pipeline.md): rover-to-base-to-browser
  video transport, framing, and bootstrap behavior
- [docs/rover-direct-operation.md](docs/rover-direct-operation.md): native
  Jetson direct-control deployment at `10.42.0.1`
- [docs/nepo-demo-operation.md](docs/nepo-demo-operation.md): gamepad Orbit
  controls and startup on `2026-nepo-demo`

```bash
ros2 run tf2_tools view_frames
ros2 run teleop_twist_keyboard teleop_twist_keyboard
ros2 doctor --report > doctor.log # detect QoS mode mismatch failure
# under ros2_ws:
rosdep install --from-paths src -y --ignore-src --rosdistro humble
# But this will not solve every dependency issue.
# ex: ros-humble-aruco-opencv, socat, ros-humble-v4l2-camera ...
ros2 pkg create --build-type ament_cmake <package_name>
```


Simulation and full rover/autonomy launch (for manual rover-direct operation,
use the section below):

```bash
# under rover/ros2_ws/
source install/setup.bash

# General entry
ros2 launch mr2_launch rover_sim.launch.py

ros2 launch mr2_launch rover_real.launch.py enable_manipulator_module:=false enable_xbee_sim:=false

# Autonomous mission entry
# You MUST ensure that EVERYTHING is brought up by rover.launch.py beforehand.
ros2 launch mr2_rover_auto navigation.launch.py mode:=sim 2>&1 | tee navigation.log

ros2 launch mr2_rover_auto navigation.launch.py mode:=sim \
  --ros-args --log-level controller_server:=debug \
  2>&1 | tee navigation.log

```

### Rover-direct operation (no base-station computer)

The Jetson can host the manual rover stack, gateway/video relay, dashboard,
nginx, and Wi-Fi AP natively. The direct mode keeps drive/steering, arm control,
video, batteries, and system status, while omitting GPS, autonomy, science,
Rocket M2, MAVProxy, and base antenna services.

Run the following from the repository root. First install the
[Jetson prerequisites](docs/rover-direct-operation.md#jetson-prerequisites),
including Node.js 24, nginx, and a built `rover/ros2_ws/install/setup.bash`.
The dashboard installer requires that ROS workspace to exist.

**Initial installation, or updating the dashboard after changing branches:**

```bash
# Build/deploy the direct dashboard and install service/nginx files.
MR2_DIRECT_AP_IP=10.42.0.1 \
  ./scripts/install_rover_direct.bash --manual

# Install camera aliases and USB power rules after setup or rule changes.
./scripts/install_udev_serial_rules.bash
```

The installer starts/enables and reloads nginx. `--manual` also stops and
disables the rover systemd service, so run it while the rover is stopped.
It does not configure the AP or CAN. A ROS source change also requires
rebuilding the workspace; a dashboard-only change does not.

**Configure the AP once, or when its settings change:**

```bash
# This Jetson uses wlP1p1s0; replace it with your Wi-Fi interface if different.
# The script prompts for the Wi-Fi password without echoing it.
sudo MR2_AP_INTERFACE=wlP1p1s0 MR2_DIRECT_AP_CIDR=10.42.0.1/24 \
  ./scripts/configure_rover_ap.bash
```

Reconfiguring the AP can disconnect an SSH session using that Wi-Fi link.
The explicit address matters: the script's default is `192.168.2.102/24`.

**Routine startup on an installed Jetson:**

```bash
# Start nginx if needed; this is harmless if it is already active.
sudo systemctl start nginx

# With the rover stack stopped, configure CAN if it is down or unconfigured.
ip -details link show can0
./scripts/can0.bash

# Start the gateway, video streaming, and rover controllers in this terminal.
./scripts/run_rover_direct.bash
```

`can0.bash` resets CAN and configures it at 500 kbit/s; do not run it while
the rover stack is operating. Keep the launch terminal open. The wrapper
selects the pinned Node runtime and sources ROS automatically. For an absent
or unpowered arm, replace the last command with:

```bash
MR2_ENABLE_MANIPULATOR_MODULE=false ./scripts/run_rover_direct.bash
```

Routine startup does not require reinstalling the dashboard, reconfiguring
the AP, or restarting nginx when it is already active. Do not run the terminal
launch alongside `mr2-rover-direct.service`.

Join the `MR2-Rover` AP and browse to `https://10.42.0.1`. See the
[rover-direct runbook](docs/rover-direct-operation.md) for prerequisites,
service inspection, manual startup with launch arguments, and hardware
validation. Reload the browser after deploying dashboard updates; see the
[NEPO demo guide](docs/nepo-demo-operation.md#circular-driving) for Orbit controls.

### Jetson native development

Development, builds, and tests run directly on the Jetson over SSH. Install
dependencies and build the workspace natively:

```bash
source /opt/ros/humble/setup.bash
rosdep install --from-paths rover/ros2_ws/src -y --ignore-src --rosdistro humble
npm --prefix base/gateway ci
npm --prefix dashboard ci --include=dev
cd rover/ros2_ws
colcon build --symlink-install
```

Run the native rover-direct gateway, dashboard, ROS build, and package tests
from the repository root with `./scripts/check_rover_direct.bash`.

Separate base-station dashboard deployment (requires Node.js and npm):

These commands configure the base-station network profile. Use
`install_rover_direct.bash` above for the Jetson-hosted direct profile.

``` bash
# Building dashboard:
# under scripts/ (requires sudo; installs nginx if missing)
# Refer to "Nginx host mapping"
. deploy_dashboard.bash 2> dashboard_err.log

# Base station XBEE interface:
# under base/gateway/
# run `npm install` to install dependencies
npm start -- --base-xbee-device /tmp/xbee_sim1 --gateway-port 8081

```

Nginx host mapping (for upstream like `mr2-ubuntu.local`):

```bash
# pick the correct IP for the upstream host (example: 192.168.1.50)
sudo sh -c 'printf "\n127.0.0.1 mr2-ubuntu.local\n" >> /etc/hosts'

# verify and reload nginx
sudo nginx -t
sudo systemctl restart nginx
sudo systemctl reload nginx
```

### WGS84 Shift Helper (East/North meters -> lat/lon)

For quick/rough coordinate offsets on the WGS84 ellipsoid:
Note: rover initial coordinate in sim is (38.406738, -110.791397)... I think?
```bash
./scripts/wgs84_shift.py 38.406738 --110.791397 100 100

# or
./scripts/wgs84_shift.py --wgs84 "38.4065,-110.7919" 100 100 --format json
```

### Mission Master Topics (Publish + Monitor)

Mission Master subscribes to:
- `mission_list` (`mr2_action_interface/msg/MissionList`)
- `mission_control` (`mr2_action_interface/msg/MissionControl`)

Mission Master publishes:
- `mission_status` (`mr2_action_interface/msg/MissionStatus`)

Enums:
- `mission_type`: `0=UNKNOWN`, `1=GNSS_ONLY`, `2=COVER_VISION`
- `detection_method`: `0=NONE`, `1=ARUCO`, `2=YOLO`
- `object_type` (YOLO): `0=MALLET`, `1=PICK`, `2=BOTTLE`
- `command`: `0=NOOP`, `1=PAUSE`, `2=RESUME`, `3=ABORT`

Publish a GNSS-only mission list (single mission):
```bash
ros2 topic pub -1 /mission_list mr2_action_interface/msg/MissionList "{
  missions: [
    {mission_id: 1, mission_type: 1, detection_method: 0, object_type: 0,
     target_latitude: 38.4065, target_longitude: -110.7900,
     target_radius: 0.0, waypoint_count: 0}
  ]
}"
```

Publish a GNSS-only mission list (two missions):
```bash
ros2 topic pub -1 /mission_list mr2_action_interface/msg/MissionList "{
  missions: [
    {mission_id: 2, mission_type: 1, detection_method: 0, object_type: 0,
     target_latitude: 38.4067, target_longitude: -110.7900,
     target_radius: 0.0, waypoint_count: 0},
    {mission_id: 3, mission_type: 1, detection_method: 0, object_type: 0,
     target_latitude: 38.4065, target_longitude: -110.7900,
     target_radius: 0.0, waypoint_count: 0}
  ]
}"
```

Publish a CoverVision mission with ArUco:
```bash
ros2 topic pub -1 /mission_list mr2_action_interface/msg/MissionList "{
  missions: [
    {mission_id: 5, mission_type: 2, detection_method: 1, object_type: 0,
     target_latitude: 38.4065, target_longitude: -110.7900,
     target_radius: 5.0, waypoint_count: 0}
  ]
}"
```

Publish a CoverVision mission with ArUco and then YOLO:
```bash
ros2 topic pub -1 /mission_list mr2_action_interface/msg/MissionList "{
  missions: [
    {mission_id: 4, mission_type: 2, detection_method: 1, object_type: 0,
     target_latitude: 38.40645496, target_longitude: -110.7900,
     target_radius: 5.0, waypoint_count: 0},
    {mission_id: 5, mission_type: 2, detection_method: 2, object_type: 0,
     target_latitude: 38.4065, target_longitude: -110.7900,
     target_radius: 5.0, waypoint_count: 0}
  ]
}"
```

Pause (optionally clear costmaps), resume, abort: (Sent via XBEE!)
```bash
ros2 topic pub -1 /mission_control mr2_action_interface/msg/MissionControl "{command: 1, clear_costmap: true, mission_id: 0}"
ros2 topic pub -1 /mission_control mr2_action_interface/msg/MissionControl "{command: 2, clear_costmap: false, mission_id: 0}"
ros2 topic pub -1 /mission_control mr2_action_interface/msg/MissionControl "{command: 3, clear_costmap: true, mission_id: 0}"
```

Monitor Mission Master:
```bash
# state: 0=IDLE, 1=RUNNING, 2=PAUSED, 3=COMPLETED, 4=FAILED
ros2 topic echo /mission_status
ros2 topic hz /mission_status
```

Notes:
- Missions execute in the order listed in `missions: [...]` (Mission Master does not loop the list).
- `mission_id` identifies missions in status/debug output. `MissionControl.mission_id` is reserved for compatibility and is currently ignored by Mission Master.
- `target_radius` is only used by `COVER_VISION` missions; set it to `0` for `GNSS_ONLY`.
- `waypoint_count: 1` marks a mission as a via point and skips the arrival delay after success; use `0` otherwise.
- `object_type` is used by `COVER_VISION` + `YOLO` (class id). For `GNSS_ONLY` or `ARUCO`, set to `0`.
- GNSS missions rely on `robot_localization/srv/FromLL` (WGS84 -> map) from `navsat_transform_node`. Debug:
```bash
ros2 service list | rg fromLL
ros2 service call /fromLL robot_localization/srv/FromLL "{ll_point: {latitude: 38.5, longitude: -110.8, altitude: 0.0}}"
```

### Vision Topics (ArUco + YOLO)

CoverVision mission consumes detection topics selected by `detection_method`:
- ArUco (`detection_method=1`): `cover_vision/object_pose/aruco`
- YOLO (`detection_method=2`): `cover_vision/object_pose/yolo`

Adapters (launched by `mr2_rover_auto/launch/action.launch.py`) bridge perception into those mission-level topics:
- YOLO adapter node: `cover_vision_yolo_adapter` subscribes `yolo/object_pose/class_<object_type>` -> publishes `cover_vision/object_pose/yolo`
- ArUco adapter node: `cover_vision_aruco_adapter` subscribes `aruco_detections` -> publishes `cover_vision/object_pose/aruco`

CoverVision coverage path (for RViz/debug):
- `cover_vision/coverage_path` (`nav_msgs/Path`, latched / transient-local)

Monitor mission-level detection:
```bash
ros2 topic echo /cover_vision/object_pose/aruco
ros2 topic echo /cover_vision/object_pose/yolo
ros2 topic echo /cover_vision/coverage_path
```

ArUco tracker (`aruco_opencv/aruco_tracker_autostart`) notes:
- Input image base topic is set by `aruco_cam_topic` in `rover.launch.py` (default `/rgbd_camera/color/image_raw`)
- The base topic must have matching `/camera_info` (see comment in `rover.launch.py`)
- Output topics are defined by `aruco_opencv`; list them at runtime:
```bash
ros2 node info /aruco_tracker
ros2 topic list | rg aruco
```
- If `publish_tf` is enabled in `aruco_opencv` config, TF frames are published for detected markers/boards:
```bash
ros2 run tf2_tools view_frames
```

YOLO RGBD detector (`mr2_yolo_perception/yolo_rgbd_detector.py`) topic defaults:
- Inputs (params): `rgb_topic=/rgbd_camera/color/image_raw`, `depth_topic=/rgbd_camera/depth/image_rect_raw`, `camera_info_topic=/rgbd_camera/color/camera_info`
- Outputs (params): `annotated_topic=yolo/annotated_image`, `pose_topic=yolo/object_pose`
- Published poses are per class: `yolo/object_pose/class_<id>` (`geometry_msgs/PoseStamped`)
- TF frames published per class: `yolo/class_<id>`

Useful checks:
```bash
ros2 param get /yolo_rgbd_detector rgb_topic
ros2 topic echo /yolo/object_pose/class_0
ros2 topic echo /yolo/annotated_image
```
