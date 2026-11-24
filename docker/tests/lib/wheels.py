#!/usr/bin/env python3
"""Print max |wheel velocity| (rad/s) from /joint_states, ~5 lines per wall s.

Usage: wheels.py [seconds]   JSON lines: {"wall", "sim", "max_wheel"}
"""
import json
import sys
import time

import rclpy
from rclpy.node import Node
from sensor_msgs.msg import JointState


def main():
    dur = float(sys.argv[1]) if len(sys.argv) > 1 else 60.0
    rclpy.init()
    n = Node("mr2_wheel_probe")
    last = [0.0]

    def cb(m):
        now = time.time()
        if now - last[0] < 0.2:
            return
        last[0] = now
        v = [abs(x) for name, x in zip(m.name, m.velocity)
             if "wheel" in name and x == x]
        print(json.dumps({
            "wall": round(now, 3),
            "sim": m.header.stamp.sec + m.header.stamp.nanosec * 1e-9,
            "max_wheel": max(v) if v else None}), flush=True)

    n.create_subscription(JointState, "/joint_states", cb, 10)
    end = time.time() + dur
    while time.time() < end:
        rclpy.spin_once(n, timeout_sec=0.1)
    n.destroy_node()
    rclpy.shutdown()


if __name__ == "__main__":
    main()
