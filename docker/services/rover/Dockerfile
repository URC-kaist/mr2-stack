# syntax=docker/dockerfile:1.7-labs
# MR2 rover image: simulated rover stack and autonomy.
# Build context = the repo root; named context "tooling" = docker/.
# Built by docker/compose/local/compose.local.yaml.
ARG ROS_DISTRO=humble

# ---- deps: system packages + every rosdep key, from manifests only ----
FROM ros:${ROS_DISTRO} AS deps
ARG ROS_DISTRO
SHELL ["/bin/bash", "-o", "pipefail", "-c"]
ENV DEBIAN_FRONTEND=noninteractive
RUN printf 'APT::Install-Recommends "false";\nAPT::Install-Suggests "false";\n' \
      > /etc/apt/apt.conf.d/99mr2-lean
# GStreamer dev + plugins: mr2_video_streaming needs them at CMake time
# and x264 (ugly) at run time; rosdep does not supply them.
RUN apt-get update && apt-get install -y --no-install-recommends \
      socat python3-colcon-common-extensions python3-rosdep \
      python3-tornado iproute2 procps \
      libgstreamer1.0-dev libgstreamer-plugins-base1.0-dev \
      gstreamer1.0-tools gstreamer1.0-plugins-base \
      gstreamer1.0-plugins-good gstreamer1.0-plugins-bad \
      gstreamer1.0-plugins-ugly gstreamer1.0-libav \
 && rm -rf /var/lib/apt/lists/*
# package.xml files only, keeping their paths: source edits don't
# invalidate this layer. Submodules must be checked out.
COPY --parents rover/ros2_ws/src/**/package.xml /tmp/manifests/
# Explicit runtime packages the launch files use (as b01 did), then
# every rosdep key. No -r: a missing key fails the build.
RUN source /opt/ros/${ROS_DISTRO}/setup.bash \
 && apt-get update \
 && rosdep update --rosdistro ${ROS_DISTRO} \
 && rosdep install --from-paths /tmp/manifests --ignore-src -y \
      --rosdistro ${ROS_DISTRO} \
 && apt-get install -y --no-install-recommends \
      ros-${ROS_DISTRO}-rosbridge-server ros-${ROS_DISTRO}-ros2-control \
      ros-${ROS_DISTRO}-ros2-controllers ros-${ROS_DISTRO}-navigation2 \
      ros-${ROS_DISTRO}-nav2-bringup ros-${ROS_DISTRO}-robot-localization \
 && rm -rf /var/lib/apt/lists/*

# ---- sim: built workspace for the simulated rover and autonomy ----
FROM deps AS sim
ARG ROS_DISTRO
ENV ROS_DISTRO=${ROS_DISTRO} \
    LIBGL_ALWAYS_SOFTWARE=1 OMP_NUM_THREADS=1 \
    LP_NUM_THREADS=2 GALLIUM_NUM_THREADS=2 \
    MR2_WS=/home/mr2/mr2-stack/rover/ros2_ws
# This exact path: nav2_params.yaml hard-codes the BT XML files here.
WORKDIR /home/mr2/mr2-stack/rover/ros2_ws
COPY rover/ros2_ws/src src
RUN source /opt/ros/${ROS_DISTRO}/setup.bash \
 && MAKEFLAGS=-j6 colcon build --executor sequential --symlink-install \
      --event-handlers console_cohesion+ 2>&1 | tail -n 80 \
 && test -f install/setup.bash
COPY --from=tooling --chmod=0755 services/rover/entrypoint.sh \
     services/rover/with-ros.sh services/rover/radio-bridge.sh \
     tools/ready.py tools/mission.py tools/gz_pose.py /opt/mr2/
COPY --from=tooling config/ /opt/mr2/config/
ENTRYPOINT ["/opt/mr2/entrypoint.sh"]
CMD ["bash"]
