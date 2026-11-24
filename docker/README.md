# MR2 in Docker

Run the whole MR2 software (dashboard, gateway, rover stack, autonomy,
simulator) from this folder with one command, on any computer with Docker,
Mac included. Docker only packages and starts the existing code: same
services, same ports, same messages.

## Quick start (local simulation)

```bash
cd docker
make local                    # first time: ~25 min to build (downloads)
open https://localhost        # dashboard; accept the self-signed certificate
```

| Address | What |
|---|---|
| `https://localhost` | dashboard |
| `https://localhost/dev-teleop.html` | keyboard driving (W/A/S/D, Q/E, Space) |
| `http://localhost:6080` | RViz + Gazebo GUI (`make local-viewer`) |

```bash
make local VIDEO=true         # with the simulated camera stream
make local-viewer             # + RViz/Gazebo in the browser
make mission                  # one GNSS mission, ~3 m north
make test                     # 8-check suite against the running stack
make down                     # stop everything
```

## Before and after

| | Native (before) | Docker (this folder) |
|---|---|---|
| OS | Ubuntu 22.04 + ROS 2 Humble, installed by hand | anything with Docker |
| Install | apt + rosdep by hand, then build the workspace | `make local` |
| Start | 3+ terminals: rover launch, navigation launch, gateway, nginx deploy | one command; started in order, each waits until the previous is healthy |
| Addresses | `.env` edited by hand | set in the compose file |
| Checks | by hand | `make test` (readiness, radio, nginx, video, a mission) |

## Setup

[`compose/local/`](compose/local/README.md) runs the whole system in
simulation on one computer: architecture, links, ports, workflow and
troubleshooting are in its README.

## What's in this folder

```text
docker/
├── Makefile                  # Commands to build, start, stop and test the stack
├── compose/                  # Container setups: services, network, ports, volumes
│   └── local/                # Whole system in simulation on one computer
│       ├── compose.local.yaml
│       ├── env.example
│       └── README.md
├── services/                 # Image builds, one folder per image
│   ├── rover/                # ROS workspace image for the rover and autonomy
│   │   ├── Dockerfile
│   │   ├── entrypoint.sh
│   │   ├── with-ros.sh
│   │   └── radio-bridge.sh
│   ├── gateway/              # Node gateway for the radio link and video
│   │   ├── Dockerfile
│   │   └── entrypoint.sh
│   ├── dashboard/            # Dashboard build served by nginx over HTTPS
│   │   ├── Dockerfile
│   │   └── 90-mr2.sh
│   └── viewer/               # RViz and the Gazebo GUI in the browser
│       ├── Dockerfile
│       └── start.sh
├── config/                   # Config for ROS between containers and sim video
├── tools/                    # Tools for health checks, missions, pose, driving
├── tests/                    # Tests for the running stack (make test)
└── tiles/                    # Offline map tiles for the dashboard (optional)
```

## What to expect (measured on an M4 Pro Mac, Docker Desktop)

| | Time |
|---|---|
| First build, rover image (mostly package downloads) | ~25 min |
| Workspace compile alone (29 packages) | ~3 min 15 s |
| Start to all-healthy, images built | ~40–70 s |
| `make clean-run` (wipe, start, suite) | ~2 min 15 s |
| Disk: rover image / all images | ~6.7 GB / ~9 GB |
| Simulation speed (software rendering) | ~0.18× real time |

The simulation runs slower than real time on a laptop without a GPU in
Docker; missions still complete, they just take longer in wall time.

Known in this code: `npm run typecheck` in `dashboard/` fails on duplicate
type names in `rosMessages.ts`; the build itself works, so the dashboard
image is unaffected.
