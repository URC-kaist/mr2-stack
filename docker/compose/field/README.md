# Field setup: the real rover and base computer

> **Draft. Not run on hardware.** For the rover's current platform (Ubuntu
> 22.04, JetPack 6, ROS 2 Humble); `docker compose config` validates the
> files, nothing more.
>
> **Important: this branch is not what is on the rover.** The rover runs local
> changes never pushed to `2026` (snapshot `demo_2026` / `2026-NX-tangled`:
> Robstride motor plugin, video and latency work). Built from `2026`, the
> rover image would not drive the current motors. For a real trial, build
> from `demo_2026`.

## Why Docker in the field (and why not)

| | Native (terminals on the machine) | Docker |
|---|---|---|
| Start | SSH in, start 3–4 launches by hand, in order | one command, or none: start at boot, restart on crash |
| Install | apt, rosdep, pip and colcon by hand on the machine | build the image once (or in CI), copy it over |
| Same as tested | whatever is installed on that machine | the exact image that was tested |
| Rebuild | old builds and packages pile up | a fresh container; the host stays clean |
| Rollback | hard to undo an upgrade | switch back to the previous image tag |
| Spare hardware | full setup again | load the same image |
| Offline sites | apt/pip mirrors or caches | `docker save` / `docker load` |

**What it doesn't fix, or costs**
- Hardware setup stays on the host: udev rules, `can0`, JetPack 6 and the
  NVIDIA runtime.
- The rover image needs a Jetson base with the hardware video encoder and
  CUDA inside the container: still to be built and tested.
- Size: about 8 GB per image on the rover's storage; updates copy images.
- Debugging goes through `docker exec` instead of the machine directly.
- It is a second way to run the rover next to the native install.

**Where it pays off**: clearly on the base computer (no special hardware,
three serial devices and nginx). On the rover it is a trade-off: easier
start, restart and rollback against the Jetson image work and an extra
layer at competition.

## Architecture

```text
      ┌───────────────────────────────────────────────────────┐
      │ Operator laptop    browser → https://192.168.1.101    │
      └────────┬──────────────────────────────────────────────┘
               │
┌──────────────┼──────────────────────────── Base computer · 192.168.1.101 ──┐
│ Docker       │                                                             │
│              │                                                             │
│  ┌───────────▼─ nginx ──────────────────────┐                              │
│  │ dashboard over HTTPS :443                │                              │
│  │ mounts: nginx-ssl · tiles                │                              │
│  └──────┬───────────────────────────────────┘                              │
│         │ /xbee-ws, /video-ws → 127.0.0.1:8081                             │
│         │                                                                  │
│  ┌──────▼─ gateway ─────────────────────────┐                              │
│  │ XBee radio, video in, MAVProxy           │                              │
│  │ mounts: /dev                             │                              │
│  └──────▲───────────────────────────────────┘                              │
│         │ RTK corrections (ROS)                                            │
│  ┌──────┴─ base-gnss ───────────────────────┐                              │
│  │ u-blox base receiver                     │                              │
│  │ mounts: /dev                             │                              │
│  └──────────────────────────────────────────┘                              │
│              ▲                    ▲                        ▲               │
├┄┄┄┄┄┄┄┄┄┄┄┄┄┄┼┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┼┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┼┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┤
│ Host         │ host network       │ bind mount (/dev)      │ mounts        │
│  ┌───────────▼──────────┐  ┌──────▼────────┐  ┌────────────▼─────────────┐ │
│  │ Rocket M2 radio      │  │ XBee radio    │  │ /dev/ttySIK  drone radio │ │
│  │ via Ethernet         │  │ via USB       │  │ /dev/ttyARDUINO antenna  │ │
│  └───────────▲──────────┘  └──────▲────────┘  │ u-blox GNSS              │ │
│              │                    │           │ tiles, nginx-ssl         │ │
│              │                    │           └──────────────────────────┘ │
└──────────────┼────────────────────┼────────────────────────────────────────┘
               ╎ 2.4 GHz, IP        ╎ 900 MHz, serial
               ╎ rosbridge, video   ╎ drive, mission, RTK
               ╎                    ╎             ╎ = over the air
               ╎                    ╎
┌──────────────┼────────────────────┼─────── Rover · Orin NX · 192.168.1.102 ┐
│ Host         │                    │                                        │
│  ┌───────────▼──────────┐  ┌──────▼────────┐  ┌──────────────────────────┐ │
│  │ Rocket M2 radio      │  │ XBee radio    │  │ can0  CAN → motor boards │ │
│  │ via Ethernet         │  │ via USB       │  │ u-blox GNSS ×2           │ │
│  └───────────┬──────────┘  └──────┬────────┘  │ RealSense, cameras       │ │
│              │                    │           └────────────┬─────────────┘ │
│              │                    │                        │               │
│              │ host network       │ bind mount (/dev)      │ mounts        │
├┄┄┄┄┄┄┄┄┄┄┄┄┄┄┼┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┼┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┼┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┤
│ Docker       │                    │                        │               │
│  ┌─ rover ───▲────────────────────▲────────────────────────▲─────────────┐ │
│  │ ┌────────────────────┐ ┌────────────────┐ ┌─────────────────────────┐ │ │
│  │ │ rosbridge :9090    │ │ XBee bridge    │ │ drive control (CAN)     │ │ │
│  │ │ video sender       │ │                │ │ sensors                 │ │ │
│  │ └────────────────────┘ └────────────────┘ └─────────────────────────┘ │ │
│  │                                                                       │ │
│  │ localization, autonomy · mounts: /dev, /run/udev                      │ │
│  └───────────────────────────────────────────────────────────────────────┘ │
└────────────────────────────────────────────────────────────────────────────┘
```

| Machine             | Compose file         | Containers                      | Imges                                                                                             |
| ------------------- | -------------------- | ------------------------------- | ------------------------------------------------------------------------------------------------- |
| Rover (Orin, arm64) | `compose.rover.yaml` | `rover`                         | `mr2/rover-field:humble` (`services/rover/Dockerfile.field`)                                      |
| Base computer       | `compose.base.yaml`  | `nginx`, `gateway`, `base-gnss` | `mr2/dashboard:humble`, `mr2/gateway-field:humble`, `mr2/rover-field:humble` (built for the base) |

## Why host networking

Containers use the machine's own network. rosbridge, video and DDS then
work on the real 192.168.1.x addresses with no port mapping, and `can0`,
which is a network interface on the host, is visible inside the rover
container.

## Links

| Link | Path |
|---|---|
| laptop → dashboard | Wi-Fi → base :443 (nginx) |
| dashboard → gateway | same origin, `/xbee-ws`, `/video-ws` → 127.0.0.1:8081 |
| dashboard → rosbridge | `/rosbridge-ws` → rover 192.168.1.102:9090 over the Rocket M2 link |
| drive and mission controls | gateway → `/dev/ttyXBEE` → 900 MHz → rover `/dev/ttyXBEE` → XBee bridge |
| video | rover sender → UDP 5008–5036 → base gateway (13 streams, Jetson encoder) |
| RTK corrections | base-gnss → ROS (domain 11) → gateway relay → XBee → rover GNSS |
| drone telemetry | gateway MAVProxy `/dev/ttySIK` → UDP `192.168.1.108:14550` |

## Network

| Address | Device |
|---|---|
| 192.168.1.101 | base computer |
| 192.168.1.102 | rover (Orin) |
| 192.168.1.100 / .103 / .106 | Rocket M2: base / drone / rover |
| 192.168.1.108 | drone telemetry (MAVProxy output) |

ROS domains: **base 11, rover 10** (`env.example`). They must differ, or
the RTK corrections loop between machines (once measured: one message
became 15,557 radio frames in 45 s).

## Devices

Names come from the repo's udev rules, installed **on the host**:

| Machine | Device | Name | Rules / setup |
|---|---|---|---|
| rover | XBee (FTDI FT231X) | `/dev/ttyXBEE` | `scripts/install_udev_serial_rules.bash` |
| rover | 2 × u-blox ZED-F9P | by receiver serial number | (driver picks by serial) |
| rover | RealSense D435i | `/dev/videoRGBD` + USB | udev |
| rover | front + 12 operator/arm/science cameras | `/dev/videoFRONT`, `videoTOP`, `videoFL`, … | udev, **by USB port path** (redo for the new rover's wiring) |
| rover | CAN (motors, arm, battery, lights) | `can0`, 500 kbit/s | `scripts/can0.bash` (Jetson `mttcan`, not USB) |
| base | XBee | `/dev/ttyXBEE` | `scripts/install_udev_base_station_rules.bash` |
| base | SiK radio (drone, CP2102) | `/dev/ttySIK` | same |
| base | antenna Arduino | `/dev/ttyARDUINO` | same |
| base | u-blox base GNSS | by serial | — |

How the containers see them: `/dev` is mounted and device classes are
allowed by major number (`device_cgroup_rules`: ttyUSB 188, ttyACM 166,
video 81, raw USB 189). Fixed `devices:` entries would break when a device
is unplugged and comes back under another node; this way the udev names
keep working. After adding a new kind of device, add its major number.

## Settings and secrets

Copy `env.example` to `.env` next to the compose files on each machine
(untracked). It holds the addresses, ROS domains, and the Rocket M2 login.

## Setup, per machine

Base computer:

1. Install Docker; run `scripts/install_udev_base_station_rules.bash`.
2. `cp docker/compose/field/env.example docker/compose/field/.env`; fill it.
3. Offline tiles at `MR2_TILES`.
4. `docker compose -f docker/compose/field/compose.base.yaml up -d --build`.

Rover:

1. JetPack 6 (as today); Docker with the NVIDIA container runtime

   (`docker info | grep -i runtime` shows `nvidia`).
2. `scripts/install_udev_serial_rules.bash`; bring up CAN (`scripts/can0.bash`,

   and at boot).
3. Choose the Jetson base image in `services/rover/Dockerfile.field`

   (`BASE_IMAGE`: L4T r36 with Humble), and build from `demo_2026`, not `2026`.
4. `.env` as above; `docker compose -f docker/compose/field/compose.rover.yaml up -d --build`.

## Verify it works (checklist)

- `ip -details link show can0` is UP at 500000 on the rover.
- `ls -l /dev/ttyXBEE` on both machines; dashboard shows **XBee Up**.
- `https://192.168.1.101` loads; **ROS Up** (rosbridge reachable over the Rocket M2).
- Drive at low speed from the dashboard; wheels respond.
- `ros2 service call /fromLL ...` answers on the rover; global TF present
before any mission (the datum comes from the base survey-in).
- Video tiles show the rover cameras.

## Open questions (before first use)

1. The Jetson base image for JetPack 6 with ROS 2 Humble, and the hardware

   video encoder inside the container.
2. Building from `demo_2026` (never built by anyone yet) instead of `2026`.
3. Image size on the Orin (rosdep still pulls Gazebo).
4. YOLO in the field image (2026 had no pinned versions).
5. Start order vs `can0` and devices at boot.
6. Whether to try Docker on the current rover before the switch-over (team,

   with the rover owner).
