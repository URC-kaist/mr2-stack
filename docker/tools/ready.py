#!/usr/bin/env python3
"""Readiness checks for the MR2 containers (real checks, not "port open").

  ready.py rover      clock advancing, controllers active, local filter
                      publishing, rosbridge accepting connections
  ready.py autonomy   clock + local filter seen from this container,
                      Nav2 lifecycle active, mission nodes present,
                      /fromLL answers (service call across containers)

Prints one JSON object; exit 0 only if every check passes.
"""
import json
import math
import os
import socket
import sys
import time

import rclpy
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy, HistoryPolicy

from rosgraph_msgs.msg import Clock
from nav_msgs.msg import Odometry

NAV2_LIFECYCLE = [
    "map_server", "controller_server", "smoother_server", "planner_server",
    "behavior_server", "bt_navigator", "waypoint_follower",
    "velocity_smoother",
]
AUTONOMY_NODES = [
    "mission_master", "map_cropper", "gnss_only_server",
    "cover_vision_server", "pc2_to_heightmap", "grid_map_filters",
    "plan_path_to_geopath",
]
BEST_EFFORT = QoSProfile(
    reliability=ReliabilityPolicy.BEST_EFFORT,
    history=HistoryPolicy.KEEP_LAST, depth=10)


class Probe(Node):
    def __init__(self):
        super().__init__("mr2_ready_probe")
        self.clock_s = []
        self.odom_s = []
        self.create_subscription(Clock, "/clock", self._on_clock, BEST_EFFORT)
        self.create_subscription(
            Odometry, "/odometry/filtered/local", self._on_odom, BEST_EFFORT)

    def _on_clock(self, msg):
        self.clock_s.append(msg.clock.sec + msg.clock.nanosec * 1e-9)

    def _on_odom(self, msg):
        p = msg.pose.pose.position
        self.odom_s.append((p.x, p.y))

    def spin_for(self, seconds):
        end = time.time() + seconds
        while time.time() < end:
            rclpy.spin_once(self, timeout_sec=0.1)

    def call(self, srv_type, name, request, timeout=4.0):
        cli = self.create_client(srv_type, name)
        try:
            if not cli.wait_for_service(timeout_sec=timeout):
                return None, "service not found"
            fut = cli.call_async(request)
            end = time.time() + timeout
            while not fut.done() and time.time() < end:
                rclpy.spin_once(self, timeout_sec=0.1)
            if not fut.done():
                return None, "no answer"
            return fut.result(), None
        finally:
            self.destroy_client(cli)


def check_common(n, res):
    # Sample >= 3 s wall, up to 10 s until both streams have data: launch
    # timers are wall time but sim runs at ~0.2x, so right after start-up a
    # fixed 3 s window can be < 0.5 sim s of filter output.
    n.spin_for(3.0)
    end = time.time() + 7.0
    while time.time() < end and (len(n.clock_s) < 2 or not n.odom_s):
        n.spin_for(0.5)
    c = n.clock_s
    res["clock"] = {
        "ok": len(c) >= 2 and c[-1] > c[0] > 0,
        "samples": len(c), "first": c[0] if c else None,
        "last": c[-1] if c else None}
    o = n.odom_s
    res["local_filter"] = {
        "ok": len(o) > 0 and all(math.isfinite(v) for v in o[-1]),
        "samples": len(o), "last": o[-1] if o else None}


def check_rover(n, res):
    from controller_manager_msgs.srv import ListControllers
    out, err = n.call(ListControllers, "/controller_manager/list_controllers",
                      ListControllers.Request())
    states = {c.name: c.state for c in out.controller} if out else {}
    want = ["joint_state_broadcaster", "rover_controller"]
    res["controllers"] = {
        "ok": all(states.get(w) == "active" for w in want),
        "states": states, "error": err}
    host = os.environ.get("ROSBRIDGE_HOST", "127.0.0.1")
    try:
        with socket.create_connection((host, 9090), timeout=2):
            res["rosbridge"] = {"ok": True, "host": host}
    except OSError as e:
        res["rosbridge"] = {"ok": False, "host": host, "error": str(e)}


def check_autonomy(n, res):
    from lifecycle_msgs.srv import GetState
    states = {}
    for name in NAV2_LIFECYCLE:
        out, err = n.call(GetState, f"/{name}/get_state", GetState.Request(),
                          timeout=2.0)
        states[name] = out.current_state.label if out else err
    res["nav2_lifecycle"] = {
        "ok": all(v == "active" for v in states.values()), "states": states}
    present = set(n.get_node_names())
    missing = [x for x in AUTONOMY_NODES if x not in present]
    res["autonomy_nodes"] = {"ok": not missing, "missing": missing}
    from robot_localization.srv import FromLL
    req = FromLL.Request()
    req.ll_point.latitude = 38.406766
    req.ll_point.longitude = -110.791394
    out, err = n.call(FromLL, "/fromLL", req)
    if out:
        m = out.map_point
        res["fromLL"] = {"ok": all(math.isfinite(v) for v in (m.x, m.y)),
                         "map_point": [m.x, m.y, m.z]}
    else:
        res["fromLL"] = {"ok": False, "error": err}


def main():
    role = sys.argv[1] if len(sys.argv) > 1 else "rover"
    rclpy.init()
    n = Probe()
    res = {"role": role}
    try:
        check_common(n, res)
        if role == "rover":
            check_rover(n, res)
        elif role == "autonomy":
            check_autonomy(n, res)
    finally:
        n.destroy_node()
        rclpy.shutdown()
    ok = all(v.get("ok") for v in res.values() if isinstance(v, dict))
    res["ok"] = ok
    print(json.dumps(res))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
