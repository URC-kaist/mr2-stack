#!/usr/bin/env bash
# Rover/autonomy entrypoint: source ROS + workspace, optionally start the
# radio bridge (rover only), then run the command.
set -e
source "/opt/ros/${ROS_DISTRO}/setup.bash"
source "${MR2_WS}/install/setup.bash"
if [ "${RADIO_BRIDGE:-}" = "server" ]; then
  /opt/mr2/radio-bridge.sh &
fi
exec "$@"
