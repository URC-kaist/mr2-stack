"""Explicit integration check; --real-wheel fl opts into the capped physical fixture."""
import argparse
import json
import os
from pathlib import Path
import signal
import socket
import struct
import subprocess
import time

import rclpy
from controller_manager_msgs.srv import ListControllers, ListHardwareComponents
from geometry_msgs.msg import Twist
from nav_msgs.msg import Odometry
from sensor_msgs.msg import JointState


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--real-wheel", choices=["none", "fl"], default="none")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    rclpy.init()
    node = rclpy.create_node("flat_bench_stack_probe")
    publisher = node.create_publisher(Twist, "/base/cmd_vel", 1)
    phase = "startup"
    samples = []
    latest = {}
    odom = []
    stream = (args.output / "joint-states.jsonl").open("w")

    def state(msg):
        nonlocal latest
        latest = {name: {"position": msg.position[i], "velocity": msg.velocity[i]}
                  for i, name in enumerate(msg.name)}
        entry = {"time": time.monotonic(), "phase": phase, "joints": latest}
        samples.append(entry)
        stream.write(json.dumps(entry) + "\n")
        stream.flush()

    subscription = node.create_subscription(JointState, "/joint_states", state, 20)
    odom_sub = node.create_subscription(Odometry, "/wheel_encoder/odometry", lambda msg: odom.append(msg), 10)
    log = (args.output / "launch.log").open("w")
    can = None
    can_stream = None
    if args.real_wheel != "none":
        can = socket.socket(socket.PF_CAN, socket.SOCK_RAW, socket.CAN_RAW)
        can.bind(("can0",)); can.setblocking(False)
        can_stream = (args.output / "can-frames.jsonl").open("w")

    def drain_can():
        if can is None:
            return
        for _ in range(1024):
            try:
                raw = can.recv(16)
            except BlockingIOError:
                break
            cid, dlc, data = struct.unpack("=IB3x8s", raw)
            can_stream.write(json.dumps({"time": time.monotonic(), "can_id": f"{cid:08X}",
                                         "dlc": dlc, "data": data[:dlc].hex()}) + "\n")
        can_stream.flush()
    process = subprocess.Popen(["ros2", "launch", "mr2_flat_bench", "hardware.launch.py",
                                "real_wheel:=" + args.real_wheel], stdout=log,
                               stderr=subprocess.STDOUT, start_new_session=True)
    try:
        client = node.create_client(ListControllers, "/controller_manager/list_controllers")
        assert client.wait_for_service(timeout_sec=10), "Controller manager did not start"
        deadline = time.monotonic() + 10
        controllers = {}
        while time.monotonic() < deadline:
            future = client.call_async(ListControllers.Request())
            rclpy.spin_until_future_complete(node, future, timeout_sec=1)
            if future.done():
                controllers = {c.name: {"type": c.type, "state": c.state} for c in future.result().controller}
                if all(controllers.get(n, {}).get("state") == "active" for n in
                       ("joint_state_broadcaster", "rover_controller")):
                    break
            time.sleep(0.1)
        assert controllers["rover_controller"] == {"type": "mr2_rover_control/TwistToCommandsController", "state": "active"}
        hardware = node.create_client(ListHardwareComponents, "/controller_manager/list_hardware_components")
        assert hardware.wait_for_service(timeout_sec=2)
        future = hardware.call_async(ListHardwareComponents.Request())
        rclpy.spin_until_future_complete(node, future, timeout_sec=2)
        components = [{"name": c.name, "plugin": c.class_type, "state": c.state.label}
                      for c in future.result().component]
        assert components == [{"name": "RoverCanSystem", "plugin": "mr2_can_hardware_interface/CanHW", "state": "active"}]
        drain_can()

        def run(name, x, y, seconds):
            nonlocal phase
            phase = name
            begin = time.monotonic()
            last_pub = 0
            while time.monotonic() - begin < seconds:
                assert process.poll() is None, "Launch exited"
                if x is not None and time.monotonic() - last_pub >= 0.05:
                    msg = Twist(); msg.linear.x = float(x); msg.linear.y = float(y)
                    publisher.publish(msg); last_pub = time.monotonic()
                rclpy.spin_once(node, timeout_sec=0.02)
                drain_can()
                time.sleep(0.005)
            selected = [s for s in samples if s["phase"] == name]
            assert selected and time.monotonic() - selected[-1]["time"] < 0.2
            tail = [s for s in selected if s["time"] > selected[-1]["time"] - 0.35]
            velocity = sum(s["joints"]["fl_wheel_joint"]["velocity"] for s in tail) / len(tail)
            delta = selected[-1]["joints"]["fl_wheel_joint"]["position"] - selected[0]["joints"]["fl_wheel_joint"]["position"]
            result = {"mean_fl_velocity": velocity, "fl_position_delta": delta, "samples": len(selected)}
            print(name, json.dumps(result), flush=True)
            return result

        run("zero", 0, 0, 1)
        assert set(latest) == {f"{key}_{part}_joint" for key in ("fl", "fr", "rl", "rr") for part in ("wheel", "steering")}
        forward = run("forward", 0.02, 0, 1)
        assert forward["mean_fl_velocity"] > 0.04 and forward["fl_position_delta"] > 0.025
        stopped = run("command_timeout", None, 0, 1.5)
        assert abs(stopped["mean_fl_velocity"]) < 0.06
        reverse = run("reverse", -0.02, 0, 1)
        assert reverse["mean_fl_velocity"] < -0.04 and reverse["fl_position_delta"] < -0.025
        run("zero_again", 0, 0, 1)
        turn = run("steer", 0.02, 0.01, 0.8)
        for key in ("fl", "fr", "rl", "rr"):
            assert abs(latest[key + "_steering_joint"]["position"] - 0.4636476) < 0.02
        run("final_zero", 0, 0, 1.2)
        assert odom, "No normal wheel odometry"
        result = {"result": "PASS", "real_wheel": args.real_wheel,
                  "controllers": controllers, "hardware": components,
                  "forward": forward, "timeout": stopped, "reverse": reverse,
                  "joint_samples": len(samples), "odom_samples": len(odom)}
        (args.output / "summary.json").write_text(json.dumps(result, indent=2) + "\n")
        print(json.dumps(result, indent=2), flush=True)
    finally:
        # Zero first, then use the production controller/hardware shutdown lifecycle.
        msg = Twist()
        for _ in range(5):
            publisher.publish(msg)
            rclpy.spin_once(node, timeout_sec=0.02)
        os.killpg(process.pid, signal.SIGINT)
        try:
            process.wait(timeout=8)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGTERM); process.wait(timeout=3)
        drain_can()
        if can is not None:
            can.close(); can_stream.close()
        stream.close(); log.close()
        node.destroy_node(); rclpy.shutdown()


if __name__ == "__main__":
    main()
