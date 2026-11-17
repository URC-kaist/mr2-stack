#!/usr/bin/env bash
set -e
repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root/rover/ros2_ws"
source /opt/ros/humble/setup.bash
source install/setup.bash
needs_can=$(python3 - "$@" <<'PY'
import sys
from mr2_launch.profile import selected_profile
defaults = {'enable_rover_hardware': 'true', 'enable_flat_motor': 'false',
            'enable_manipulator_module': 'false', 'enable_science_module': 'false',
            'enable_led': 'true', 'enable_camera_turret': 'true'}
defaults.update(selected_profile()[2].get('common', {}))
defaults.update(arg.split(':=', 1) for arg in sys.argv[1:] if ':=' in arg)
print('true' if any(str(defaults[key]).lower() == 'true' for key in
      ('enable_rover_hardware', 'enable_flat_motor', 'enable_manipulator_module',
       'enable_science_module', 'enable_led', 'enable_camera_turret')) else 'false')
PY
)
if [[ "$needs_can" == true ]]; then
  bash "$repo_root/scripts/can0.bash" can0
else
  echo '[INFO] CAN hardware disabled; skipping CAN setup.'
fi
exec ros2 launch mr2_launch rover_real.launch.py can_iface:=can0 enable_manipulator_module:=false enable_video_streaming:=true enable_latency_diagnostics:=true enable_autonomous_module:=false "$@"
