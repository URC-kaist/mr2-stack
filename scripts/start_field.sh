#!/usr/bin/env bash
# Run in a local terminal: field setup switches the Ethernet address.
set -e
repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
role="${1:-}"
if [[ ( "$role" != base && "$role" != rover ) || $# -gt 2 ||
      ( $# == 2 && "$2" != --check ) ]]; then
  echo "Usage: bash $0 {base|rover} [--check]" >&2
  exit 2
fi

python3 "$repo_root/scripts/field_setup.py" "$role" check

# Verify the actual installed ROS/Node environment before changing networking.
source /opt/ros/humble/setup.bash
source "$repo_root/rover/ros2_ws/install/setup.bash"
if [[ "$role" == base ]]; then
  if [[ -s "$HOME/.nvm/nvm.sh" ]]; then
    source "$HOME/.nvm/nvm.sh"
    cd "$repo_root"
    nvm use
  fi
  cd "$repo_root/base/gateway"
  node - <<'JS'
if (Number(process.versions.node.split('.')[0]) !== 24) throw Error('Node 24 must already be installed')
const rcl = require('rclnodejs')
for (const type of ['ublox_ubx_msgs/msg/UBXNavSvin', 'rtcm_msgs/msg/Message']) rcl.require(type)
require('serialport'); require('ws')
console.log('[OK] Installed gateway and ROS message bindings')
JS
else
  ros2 pkg prefix mr2_launch
  ros2 pkg prefix mr2_latency_msgs
fi

if [[ "${2:-}" == --check ]]; then
  echo '[OK] Installation check only; no network changes or processes started.'
  exit 0
fi

sudo python3 "$repo_root/scripts/field_setup.py" "$role" prepare
if [[ "$role" == base ]]; then
  optional=()
  if [[ ! -e /dev/ttySIK ]]; then
    echo '[INFO] No SiK device; MAVProxy disabled for this test.'
    optional=(--mavproxy-enable false)
  fi
  echo '[FIELD] Open https://192.168.1.101/ on the laptop. Start rover next.'
  exec bash "$repo_root/scripts/start_chrony_base.sh" \
    --gateway-profile base \
    --latency-diagnostics-enable true \
    --latency-base-interface enp2s0 \
    --latency-public-base-url http://192.168.1.101 \
    --video-config "$repo_root/rover/ros2_ws/src/mr2_launch/config/video_streams_flat.json" \
    "${optional[@]}"
else
  python3 "$repo_root/scripts/field_setup.py" rover clock
  if [[ ! -f "$repo_root/.mr2-rover-profile" ]] ||
     [[ $(cat "$repo_root/.mr2-rover-profile") != flat ]]; then
    bash "$repo_root/scripts/rover.bash" start flat
  fi
  exec bash "$repo_root/scripts/start_chrony.sh" video_base_host:=192.168.1.101
fi
