"""Exercise the public dashboard WebSocket through the unchanged rover stack."""
import argparse
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import time

import rclpy
from geometry_msgs.msg import Twist
from mr2_action_interface.msg import MissionControl
from sensor_msgs.msg import JointState


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--real-wheel", choices=["none", "fl"], default="none")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    rclpy.init()
    node = rclpy.create_node("flat_bench_gateway_probe")
    received = []; joints = []; controls = []
    phase = "startup"

    def state(msg):
        joints.append({"time": time.monotonic(), "phase": phase,
                       "fl_velocity": msg.velocity[msg.name.index("fl_wheel_joint")],
                       "fl_position": msg.position[msg.name.index("fl_wheel_joint")]})

    subscriptions = [
        node.create_subscription(Twist, "/base/cmd_vel", lambda msg: received.append(
            {"time": time.monotonic(), "x": msg.linear.x}), 10),
        node.create_subscription(JointState, "/joint_states", state, 20),
        node.create_subscription(MissionControl, "/mission_control", lambda msg: controls.append(msg.command), 10),
    ]
    log = (args.output / "launch.log").open("w")
    launch = subprocess.Popen(["ros2", "launch", "mr2_flat_bench", "rover_flat.launch.py",
                               "real_wheel:=" + args.real_wheel, "enable_video_streaming:=false"],
                              stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    client = None
    client_log = (args.output / "websocket.log").open("w")
    try:
        deadline = time.monotonic() + 40
        while time.monotonic() < deadline:
            assert launch.poll() is None, "Full launch exited"
            rclpy.spin_once(node, timeout_sec=0.1)
            if joints and joints[-1]["time"] > time.monotonic() - 1:
                try:
                    with socket.create_connection(("127.0.0.1", 8081), timeout=0.2):
                        break
                except OSError:
                    pass
        else:
            raise RuntimeError("Gateway and joint state startup timed out")
        client = subprocess.Popen([
            "/home/mr2/.nvm/versions/node/v24.15.0/bin/node",
            str(Path(__file__).with_name("ws_input.js")), "wss://10.42.0.1/xbee-ws"],
            stdin=subprocess.PIPE, stdout=client_log, stderr=subprocess.STDOUT, text=True)

        def set_input(**values):
            client.stdin.write(json.dumps(values) + "\n"); client.stdin.flush()

        def run(name, value, seconds, estop=False):
            nonlocal phase
            phase = name
            set_input(x=value, estop=estop)
            begin = time.monotonic()
            while time.monotonic() - begin < seconds:
                assert launch.poll() is None and client.poll() is None
                rclpy.spin_once(node, timeout_sec=0.02)
                time.sleep(0.005)
            rows = [s for s in joints if s["phase"] == name]
            assert rows and time.monotonic() - rows[-1]["time"] < 0.3
            tail = [s for s in rows if s["time"] > rows[-1]["time"] - 0.35]
            value = sum(s["fl_velocity"] for s in tail) / len(tail)
            print(name, value, flush=True)
            return value

        run("initial_zero", 0, 1.0)
        forward = run("forward", 0.02, 1.2)
        assert forward > 0.04 and any(s["x"] > 0.01 for s in received)
        finding = None
        if args.real_wheel == "none":
            after_abort = run("abort_with_held_drive", 0.02, 0.7, estop=True)
            assert 3 in controls, "Dashboard ABORT did not arrive through XBee"
            finding = {"abort_reaches_mission_control": True,
                       "drive_continues_while_held": after_abort > 0.04,
                       "mean_velocity_after_abort": after_abort}
        zero = run("release_triggers", 0, 1.5)
        assert abs(zero) < 0.06
        reverse = run("reverse", -0.02, 1.2)
        assert reverse < -0.04 and any(s["x"] < -0.01 for s in received)
        run("final_zero", 0, 1.5)
        result = {"result": "PASS", "real_wheel": args.real_wheel,
                  "public_websocket": "wss://10.42.0.1/xbee-ws",
                  "forward_joint_velocity": forward, "reverse_joint_velocity": reverse,
                  "twist_messages": len(received), "joint_messages": len(joints),
                  "existing_stack_abort_behavior": finding}
        (args.output / "summary.json").write_text(json.dumps(result, indent=2) + "\n")
        print(json.dumps(result, indent=2), flush=True)
    finally:
        if client and client.poll() is None:
            client.stdin.write('{"x":0}\n'); client.stdin.flush()
            for _ in range(30): rclpy.spin_once(node, timeout_sec=0.02)
            client.send_signal(signal.SIGINT)
            try: client.wait(timeout=3)
            except subprocess.TimeoutExpired: client.kill(); client.wait()
        os.killpg(launch.pid, signal.SIGINT)
        try: launch.wait(timeout=10)
        except subprocess.TimeoutExpired: os.killpg(launch.pid, signal.SIGTERM); launch.wait(timeout=3)
        (args.output / "joint-states.json").write_text(json.dumps(joints) + "\n")
        log.close(); client_log.close(); node.destroy_node(); rclpy.shutdown()


if __name__ == "__main__":
    main()
