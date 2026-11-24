#!/usr/bin/env python3
"""Print /base/cmd_vel and /cmd_vel linear.x/y, angular.z as JSON lines.

Usage: cmdvel.py [seconds]
"""
import json
import sys
import time

import rclpy
from rclpy.node import Node
from geometry_msgs.msg import Twist


def main():
    dur = float(sys.argv[1]) if len(sys.argv) > 1 else 30.0
    rclpy.init()
    n = Node("mr2_cmdvel_probe")
    for topic in ("/base/cmd_vel", "/cmd_vel"):
        n.create_subscription(
            Twist, topic,
            lambda m, t=topic: print(json.dumps({
                "wall": round(time.time(), 3), "topic": t,
                "x": round(m.linear.x, 4), "y": round(m.linear.y, 4),
                "z": round(m.angular.z, 4)}), flush=True), 10)
    end = time.time() + dur
    while time.time() < end:
        rclpy.spin_once(n, timeout_sec=0.1)
    import os
    os._exit(0)


if __name__ == "__main__":
    main()
