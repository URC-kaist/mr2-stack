# Local setup: the whole system in simulation on one computer

Gazebo stands in for the rover's hardware; everything else is the real code.
Run from `docker/`: `make local` (see [../../README.md](../../README.md)).

## Before and after

| | Native one-PC sim | `make local` |
|---|---|---|
| Rover | terminal 1: `ros2 launch mr2_launch rover_sim.launch.py` | `rover` container |
| Autonomy | terminal 2: `ros2 launch mr2_rover_auto navigation.launch.py mode:=sim` | `autonomy` container, starts when the rover is healthy |
| Gateway | terminal 3: `npm start` in `base/gateway` | `gateway` container |
| Dashboard | `scripts/deploy_dashboard.bash` (sudo, nginx on the host) | `nginx` container |
| Fake radio | socat PTY pair `/tmp/xbee_sim0` ⇄ `/tmp/xbee_sim1` on the host | same pair inside the rover, carried to the gateway over TCP |
| Addresses | `.env` with `127.0.0.1` everywhere | fixed in `compose.local.yaml` |

## Architecture

```text
        ┌─────────────────────────────────────────────────────────────────┐
        │ Browser   https://localhost               http://localhost:6080 │
        └──────┬──────────────────────────────────────────────┬───────────┘
               │                                              │
┌──────────────┼────── Your computer ─────────────────────────┼──────────────┐
│ Docker       │ :443 published               :6080 published │              │
│              │ network "mr2" 10.242.42.0/24                 │              │
│  ┌───────────▼─ nginx ──────────────────────────┐   ┌───────▼─ viewer ──┐  │
│  │ dashboard over HTTPS                         │   │ RViz, Gazebo GUI  │  │
│  │ mounts: nginx-ssl · teleop, tiles            │   │ mounts: ws-*      │  │
│  └────────┬──────────────────────────────┬──────┘   │ · src, tests      │  │
│           │ /xbee-ws, /video-ws          │          └───────┬───────────┘  │
│           │ :8081                        │ /rosbridge-ws    │ ROS 2,       │
│           │                              │ :9090            │ Gazebo       │
│  ┌────────▼─ gateway ─────────────┐      │                  │              │
│  │ 10.242.42.10                   │      │                  │              │
│  │ Node, no ROS                   │      │                  │              │
│  └────────▲──────────────▲────────┘      │                  │              │
│           │              │               │                  │              │
│           │ radio        │ video         │                  │              │
│           │ TCP :7777    │ UDP :5022     │                  │              │
│           │              │               │                  │              │
│ ┌─ rover ─┼──────────────┼───────────────┼──────────────────┼────────────┐ │
│ │         │              │               │                  │            │ │
│ │ ┌───────▼─────┐  ┌─────┴───────┐  ┌────▼───────┐   ┌──────▼──────────┐ │ │
│ │ │ XBee        │  │ video       │  │ rosbridge  │   │ Gazebo          │ │ │
│ │ │ bridge      │  │ sender      │  │ :9090      │   │ simulated world │ │ │
│ │ └─────────────┘  └─────────────┘  └────────────┘   └─────────────────┘ │ │
│ │                                                                        │ │
│ │ drive control, localization, battery emulators                         │ │
│ │ mounts: ws-*, gz-cache · src, tests                                    │ │
│ └───────────────────────────────────▲────────────────────────────────────┘ │
│                                     │ ROS 2 topics, services, TF           │
│                                     │ DDS over UDP, domain 42              │
│ ┌─ autonomy ────────────────────────▼────────────────────────────────────┐ │
│ │ Nav2, mission servers, mission_master, map cropper                     │ │
│ │ mounts: ws-* · src, tests                                              │ │
│ └────────────────────────────────────────────────────────────────────────┘ │
│                      ▲                                    ▲                │
├┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┼┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┼┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┤
│ Host                 │ bind mount                         │ volume mount   │
│                      │                                    │                │
│  ┌───────────────────▼────────────────────┐  ┌────────────▼─────────────┐  │
│  │ rover/ros2_ws/src                      │  │ ws-build, ws-install,    │  │
│  │ docker/tests                           │  │ ws-log                   │  │
│  │ docker/tools/teleop.html               │  │ gz-cache                 │  │
│  │ docker/tiles                           │  │ nginx-ssl                │  │
│  └────────────────────────────────────────┘  └──────────────────────────┘  │
└────────────────────────────────────────────────────────────────────────────┘
```

| Container | Image | Runs |
|---|---|---|
| `rover` | `mr2/rover-sim:humble` | `rover_sim.launch.py` (camera-off baseline: no arm, no YOLO, no ArUco) |
| `autonomy` | `mr2/rover-sim:humble` (same image) | `navigation.launch.py mode:=sim` |
| `gateway` | `mr2/gateway:humble` | `node index.js` + socat radio link |
| `nginx` | `mr2/dashboard:humble` | the built dashboard + `scripts/nginx` template |
| `viewer` (optional) | `mr2/viewer:humble` | VNC desktop with RViz and the Gazebo GUI |

Rover and autonomy share the source (`rover/ros2_ws/src` mounted in) and the
build (`build/`, `install/`, `log/` in shared volumes), at
`/home/mr2/mr2-stack/rover/ros2_ws`: the path `nav2_params.yaml` hard-codes
for the behaviour trees.

## Links

| Link | How | Note |
|---|---|---|
| browser → dashboard | HTTPS 443 → nginx | self-signed cert for `localhost` |
| dashboard → gateway | `/xbee-ws`, `/video-ws`, `/video/streams` via nginx | same origin; no `VITE_*` settings |
| dashboard → rosbridge | `/rosbridge-ws` via nginx → `rover:9090` | |
| gateway ⇄ rover (900 MHz radio, faked) | gateway PTY ⇄ TCP `rover:7777` ⇄ rover PTY ⇄ XBee bridge | both ends restart on their own |
| rover → gateway (video) | UDP 5022 to `10.242.42.10` | an IP, not a name: the sender resolves once at start, before the gateway exists |
| rover ⇄ autonomy (ROS) | Fast DDS, UDP only (`config/fastdds-udp.xml`), domain 42 | containers can't share memory |
| Gazebo GUI ⇄ simulation | Gazebo transport, `IGN_PARTITION=mr2` (Fortress) | |

## Network and ports

| Port | Where | Published |
|---|---|---|
| 443 | nginx | `127.0.0.1:443` |
| 6080 | viewer (noVNC) | `127.0.0.1:6080` |
| 8081 | gateway | network only |
| 9090 | rosbridge (rover) | network only |
| 7777 | fake radio (rover) | network only |
| 5022/udp | video into the gateway | network only |

## Devices

None. What stands in for the hardware:

| Real | Local |
|---|---|
| CAN motors and steering | Gazebo joints (`gz_ros2_control`) |
| 2 GNSS, IMU, RealSense, front camera (USB) | Gazebo sensors |
| XBee radios (`/dev/ttyXBEE`) | socat PTY pair over TCP |
| Rocket M2 network | the Docker network |
| 13 V4L2 cameras + Jetson encoder | one stream from the Gazebo camera, software x264 (`docker/config/video_streams.sim.json`) |

## Settings

All in `compose.local.yaml`; optional overrides as environment variables
(see `env.example`): `MR2_VIDEO=true` (or `make ... VIDEO=true`),
`MR2_TILES=/path/to/tiles` (offline map), `BASE_XBEE_HEARTBEAT_HZ`.
The gateway's own `.env` files are excluded from its image so they can't
override these. No secrets locally.

## Development workflow

- Edit code in `rover/ros2_ws/src` on your computer; it is mounted in.
- `make build` rebuilds the workspace in the running rover container and
  restarts autonomy. For launch-file changes, `make down && make local`.
- `make shell` (or `make shell s=autonomy`): a shell with ROS sourced.
- `make logs s=gateway`, `make ps`, `make ready`.
- Dependency changes (`package.xml`, apt): `make rebuild-images`.

## Verify it works

`make test` runs 8 checks against the running stack: services healthy;
rover ready (clock, controllers, position filter, rosbridge); autonomy ready
(Nav2 active, mission nodes, `/fromLL` answered across containers); radio
both ways through the real gateway; nginx (page + three WebSocket upgrades);
video available; a GNSS mission completed within 0.75 m by Gazebo's own pose;
the gateway's unit tests. `make clean-run` wipes everything first.

In the browser: XBee and ROS show **Up**; teleop moves the rover (watch the
Live Feed or Gazebo); a mission from the Autonomous tab completes.

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| Rover crawls or barely moves when driving | two clients sending drive commands (a dashboard with a gamepad selected, another teleop): use one |
| Live Feed grey or viewer shows nothing after changes | only some containers were recreated; `make down && make local` |
| Dashboard GNSS boxes show "--" | expected in sim: they read the u-blox driver's status, Gazebo publishes only fixes |
| Map panel blank | no offline tiles; set `MR2_TILES` |
| `TLS handshake timeout` while building | Docker Hub hiccup; run the command again |
| Viewer still shows old code after `make rebuild-images` | the viewer is built on the rover image and may build first; run `make rebuild-images` again, or rebuild the viewer last |
| Simulation slow | expected without a GPU (~0.2× real time); missions still complete |
| Nav2 goal sent directly does nothing | the controller obeys autonomy only while a mission is RUNNING; send a mission |
