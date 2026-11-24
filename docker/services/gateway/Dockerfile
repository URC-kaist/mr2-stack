# syntax=docker/dockerfile:1.7
# MR2 gateway (Node, no ROS). Context = the repo root.
FROM node:24.15.0-bookworm-slim
RUN apt-get update && apt-get install -y --no-install-recommends \
      socat python3 build-essential gstreamer1.0-tools \
      gstreamer1.0-plugins-base gstreamer1.0-plugins-good \
      gstreamer1.0-plugins-bad gstreamer1.0-libav \
 && rm -rf /var/lib/apt/lists/*
# Keep the repository layout: the tests read
# ../../../rover/.../video_streams.json relative to base/gateway/test.
WORKDIR /repo/base/gateway
COPY base/gateway/package.json base/gateway/package-lock.json ./
RUN npm ci --ignore-scripts && npm rebuild @serialport/bindings-cpp
# rclnodejs is a regular dependency: left installed but unbuilt,
# require() tries to compile it. Removing it makes require() throw,
# so ros_topic_relay.js disables the relay (b04 used an empty mount).
RUN rm -rf node_modules/rclnodejs
COPY base/gateway/ ./
COPY rover/ros2_ws/src/mr2_launch/config/video_streams.json \
     /repo/rover/ros2_ws/src/mr2_launch/config/
COPY --from=tooling config/video_streams.sim.json /opt/mr2/config/
COPY --from=tooling --chmod=0755 services/gateway/entrypoint.sh /opt/mr2/
ENTRYPOINT ["/opt/mr2/entrypoint.sh"]
CMD ["node", "index.js", "--video-config", \
     "/opt/mr2/config/video_streams.sim.json"]
