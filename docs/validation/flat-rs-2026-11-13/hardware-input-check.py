import json
import os
from pathlib import Path
import signal
import subprocess
import time

import rclpy
from geometry_msgs.msg import Twist
from mr2_action_interface.msg import MissionControl
from std_msgs.msg import String

root = Path('/tmp/mr2-flat-rs-validation')
root.mkdir(exist_ok=True)
log = (root / 'node.log').open('w')
events = (root / 'status.jsonl').open('w')
rclpy.init()
node = rclpy.create_node('flat_rs_input_path_validation')
drive = node.create_publisher(Twist, '/base/cmd_vel', 1)
stop = node.create_publisher(MissionControl, '/mission_control', 10)
status = {}
history = []
phase = 'starting'

def receive(msg):
    global status
    status = json.loads(msg.data)
    entry = {'time': time.monotonic(), 'phase': phase, **status}
    history.append(entry)
    events.write(json.dumps(entry) + '\n')
    events.flush()

subscription = node.create_subscription(String, '/flat_motor/status', receive, 1)
binary = '/home/mr2/mr2-stack/rover/ros2_ws/install/mr2_devices_robstride/lib/mr2_devices_robstride/robstride_flat_motor'
process = subprocess.Popen([binary, '--ros-args', '-p', 'motor_id:=127'],
                           stdout=log, stderr=subprocess.STDOUT)

def run(name, value, seconds):
    global phase
    phase = name
    begin = time.monotonic()
    while time.monotonic() - begin < seconds:
        assert process.poll() is None, 'RS controller process exited'
        if value is not None:
            msg = Twist()
            msg.linear.x = value
            drive.publish(msg)
        rclpy.spin_once(node, timeout_sec=0.02)
        time.sleep(0.01)
    assert history and time.monotonic() - history[-1]['time'] < 0.4, 'No fresh status'
    print(name, json.dumps(status), flush=True)

try:
    # Held positive input during startup cannot arm or move the controller.
    run('held_at_startup', 0.8, 2.0)
    assert status['ready'] and not status['armed'] and not status['fault']
    assert status['command_velocity_deg_s'] == 0
    run('neutral_to_arm', 0.0, 0.9)
    assert status['armed'] and not status['fault']
    run('positive_input', 0.8, 0.8)
    assert status['command_velocity_deg_s'] < -10
    run('input_timeout', None, 1.1)
    assert not status['armed'] and status['command_velocity_deg_s'] == 0
    run('held_after_timeout', 0.8, 0.5)
    assert not status['armed'] and status['command_velocity_deg_s'] == 0
    run('neutral_rearm', 0.0, 0.8)
    assert status['armed']
    run('negative_input', -0.8, 0.8)
    assert status['command_velocity_deg_s'] > 10
    for _ in range(3):
        stop.publish(MissionControl(command=3))
        rclpy.spin_once(node, timeout_sec=0.03)
    run('emergency_stop', -0.8, 0.8)
    assert status['stop_latched'] and not status['armed']
    assert status['command_velocity_deg_s'] == 0 and status['fault']
    run('neutral_after_estop', 0.0, 0.6)
    run('held_after_estop', 0.8, 0.4)
    assert status['stop_latched'] and not status['armed']
    assert status['command_velocity_deg_s'] == 0
    result = {'result': 'PASS', 'motor_id': 127, 'bitrate': 500000,
              'checks': ['held startup rejected', 'neutral arms', 'positive and negative Twist reach plugin',
                         'input timeout stops', 'held input cannot rearm', 'MissionControl ABORT disables',
                         'neutral cannot clear emergency stop'], 'status_samples': len(history)}
    (root / 'summary.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2), flush=True)
finally:
    process.send_signal(signal.SIGINT)
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.terminate()
        process.wait(timeout=3)
    events.close()
    log.close()
    node.destroy_node()
    rclpy.shutdown()
