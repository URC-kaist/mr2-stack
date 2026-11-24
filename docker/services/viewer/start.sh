#!/usr/bin/env bash
# VNC desktop on :1, served to the browser by noVNC on :6080.
# Starts RViz and the Gazebo client unless VIEWER_APPS=none.
set -e
export DISPLAY=:1 USER=root HOME=/root
mkdir -p /root/.vnc
vncserver :1 -geometry "${VIEWER_GEOMETRY:-1600x900}" -depth 24 \
  -SecurityTypes None -localhost yes --I-KNOW-THIS-IS-INSECURE \
  -xstartup /usr/bin/startxfce4 >/tmp/vnc.log 2>&1
sleep 3
desc="/home/mr2/mr2-stack/rover/ros2_ws/install/mr2_rover_description/share"
export IGN_GAZEBO_RESOURCE_PATH="$desc:${IGN_GAZEBO_RESOURCE_PATH:-}"
if [ "${VIEWER_APPS:-all}" != "none" ]; then
  rviz_cfg="$(ros2 pkg prefix mr2_launch)/share/mr2_launch/rviz/sim.rviz"
  (rviz2 -d "$rviz_cfg" --ros-args -p use_sim_time:=true \
     >/tmp/rviz.log 2>&1 &)
  (ign gazebo -g >/tmp/gz-gui.log 2>&1 &)
fi
exec websockify --web=/usr/share/novnc/ 6080 localhost:5901
