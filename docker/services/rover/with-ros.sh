#!/usr/bin/env bash
# Source ROS + workspace only (health checks, make shell). No radio bridge.
source "/opt/ros/${ROS_DISTRO}/setup.bash"
source "${MR2_WS}/install/setup.bash"
exec "$@"
