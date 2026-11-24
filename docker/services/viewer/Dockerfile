# syntax=docker/dockerfile:1.7
# MR2 viewer: a browser desktop (noVNC :6080) with RViz and the Gazebo GUI.
# Based on the rover image so meshes, messages and the workspace match.
ARG ROS_DISTRO=humble
FROM mr2/rover-sim:${ROS_DISTRO}
RUN apt-get update && apt-get install -y --no-install-recommends \
      xfce4 xfce4-terminal dbus-x11 tigervnc-standalone-server \
      novnc websockify mesa-utils libgl1-mesa-dri x11-xserver-utils \
 && rm -rf /var/lib/apt/lists/*
ENV LIBGL_ALWAYS_SOFTWARE=1
# http://localhost:6080/ opens the viewer instead of a directory listing.
RUN printf '%s\n' '<!doctype html><meta charset="utf-8">' \
      '<meta http-equiv="refresh" content="0; url=vnc.html?autoconnect=1&resize=scale">' \
      '<a href="vnc.html?autoconnect=1&resize=scale">Open the MR2 viewer</a>' \
      > /usr/share/novnc/index.html
COPY --from=tooling --chmod=0755 services/viewer/start.sh /opt/mr2/start.sh
EXPOSE 6080
ENTRYPOINT ["/opt/mr2/entrypoint.sh"]
CMD ["/opt/mr2/start.sh"]
