#!/usr/bin/env python3
"""Ground-truth rover pose from Gazebo transport (not wheel odometry).

Usage: python3 /opt/mr2/gz_pose.py            one sample as JSON (make pose)
       python3 /opt/mr2/gz_pose.py --follow   a JSON line per ~0.1 s
Run on the machine (or container) running Gazebo, same Gazebo partition.
Wheel odometry over-reports in sim (wheel radius 0.125 m vs 0.17 m in the
controller), so use this for distances and arrival checks.
"""
import json
import subprocess
import sys
import time

TOPIC = "/world/empty_world/dynamic_pose/info"


def gz_cmd():
    return ["ign", "topic", "-e", "-t", TOPIC, "--json-output"]


def rover_pose(msg):
    for p in msg.get("pose", []):
        if p.get("name") == "rover":
            pos = p.get("position", {})
            ori = p.get("orientation", {})
            st = msg.get("header", {}).get("stamp", {})
            return {
                "sim": float(st.get("sec", 0)) + st.get("nsec", 0) * 1e-9,
                "wall": time.time(),
                "x": pos.get("x", 0.0), "y": pos.get("y", 0.0),
                "z": pos.get("z", 0.0),
                "qz": ori.get("z", 0.0), "qw": ori.get("w", 1.0),
            }
    return None


def main():
    follow = "--follow" in sys.argv
    proc = subprocess.Popen(gz_cmd(), stdout=subprocess.PIPE, text=True)
    last = 0.0
    deadline = time.time() + 30
    try:
        for line in proc.stdout:
            try:
                pose = rover_pose(json.loads(line))
            except ValueError:
                continue
            if pose is None:
                continue
            if not follow:
                print(json.dumps(pose), flush=True)
                return 0
            if pose["wall"] - last >= 0.1:
                last = pose["wall"]
                print(json.dumps(pose), flush=True)
            if not follow and time.time() > deadline:
                break
    finally:
        proc.kill()
    return 1


if __name__ == "__main__":
    sys.exit(main())
