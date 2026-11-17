#!/usr/bin/env python3
"""Run the real ROS runtime with synthetic devices and uniquely remapped topics.

Requires the built workspace sourced. Does not open SocketCAN or launch hardware.
"""
import json
import math
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import time
import argparse
import yaml
import xacro
import rclpy
from geometry_msgs.msg import Twist
from sensor_msgs.msg import JointState
from nav_msgs.msg import Odometry
from std_msgs.msg import Float64MultiArray
from std_srvs.srv import SetBool
from diagnostic_msgs.msg import DiagnosticArray
from mr2_action_interface.msg import MissionStatus
from ament_index_python.packages import get_package_share_directory, get_package_prefix


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--base-only', action='store_true')
    parser.add_argument('--standalone', action='store_true')
    args = parser.parse_args()
    share = Path(get_package_share_directory('mr2_rover_description'))
    name = 'mr2_control_test_' + str(os.getpid())
    prefix = '/' + name
    params = {
        'robot_description': xacro.process_file(
            str(share / 'urdf' / ('manipulator_ak_can.urdf.xacro' if args.standalone else 'rover.urdf.xacro')),
            mappings={'ros2_control_mode': 'real_hardware', 'can_iface': 'unused_mock',
                      'enable_manipulator_module': 'false' if args.base_only else 'true'},
        ).toxml(),
        'controller_config': str(share / 'config/controllers' /
                                 ('manipulator_controllers.yaml' if args.standalone else 'rover_controllers.yaml')),
        'mock_devices': True,
        'enable_manipulator_module': not args.base_only,
    }
    topics = ['/joint_states', '/wheel_encoder/odometry', '/base/cmd_vel', '/cmd_vel',
              '/mission_status', '/manipulator_controller/commands',
              '/gripper_controller/commands', '/gripper_position_controller/commands']
    command = [str(Path(get_package_prefix('mr2_control')) / 'lib/mr2_control/mr2_control_node'),
               '--ros-args', '-r', '__node:=' + name]
    for topic in topics:
        command += ['-r', topic + ':=' + prefix + topic]
    rclpy.init()
    node = rclpy.create_node(name + '_probe')
    joints, odom, diagnostics, arrivals = [], [], [], []
    subscriptions = [
        node.create_subscription(JointState, prefix + '/joint_states', lambda msg: (joints.append(msg), arrivals.append(time.monotonic())), 10),
        node.create_subscription(Odometry, prefix + '/wheel_encoder/odometry', odom.append, 10),
        node.create_subscription(DiagnosticArray, prefix + '/diagnostics', diagnostics.append, 10),
    ]
    arm = node.create_publisher(Float64MultiArray, prefix + '/manipulator_controller/commands', 10)
    gripper_topic = '/gripper_position_controller/commands' if args.standalone else '/gripper_controller/commands'
    gripper = node.create_publisher(Float64MultiArray, prefix + gripper_topic, 10)
    manual = node.create_publisher(Twist, prefix + '/base/cmd_vel', 10)
    autonomous = node.create_publisher(Twist, prefix + '/cmd_vel', 10)
    mission = node.create_publisher(MissionStatus, prefix + '/mission_status', 10)

    def spin(seconds, send=None):
        until = time.monotonic() + seconds
        while time.monotonic() < until:
            if send:
                send()
            rclpy.spin_once(node, timeout_sec=.01)
            assert process.poll() is None, 'runtime exited: ' + log_path.read_text()

    def position(joint):
        return joints[-1].position[joints[-1].name.index(joint)]

    def activate(component, active):
        client = node.create_client(SetBool, prefix + '/set_' + component + '_active')
        assert client.wait_for_service(timeout_sec=3)
        future = client.call_async(SetBool.Request(data=active))
        rclpy.spin_until_future_complete(node, future, timeout_sec=3)
        assert future.done() and future.result().success
        spin(.1)
        node.destroy_client(client)

    with tempfile.TemporaryDirectory(prefix='mr2-control-integration-') as directory:
        param_path = Path(directory) / 'params.yaml'
        param_path.write_text(yaml.safe_dump({name: {'ros__parameters': params}}))
        log_path = Path(directory) / 'runtime.log'
        with log_path.open('w') as log:
            process = subprocess.Popen(command + ['--params-file', str(param_path)], stdout=log, stderr=subprocess.STDOUT)
            try:
                spin(1.5)
                assert joints and len(joints[-1].name) == (8 if args.base_only else 15)
                assert all(math.isfinite(v) for v in joints[-1].position)
                assert all(msg.header.frame_id == 'base_link' and
                           (msg.header.stamp.sec != 0 or msg.header.stamp.nanosec != 0)
                           for msg in joints)
                assert math.isnan(joints[-1].effort[joints[-1].name.index('fl_wheel_joint')])
                if not args.base_only:
                    first = joints[0]
                    assert abs(first.position[first.name.index('arm_j3')] + math.pi / 4) < 1e-8
                if not args.base_only:
                    assert abs(position('arm_j3') + math.pi / 4) < 1e-8
                    arm_order = ['arm_j2', 'arm_j3', 'arm_j4', 'arm_j5', 'arm_j6'] if args.standalone else ['arm_j1', 'arm_j2', 'arm_j3', 'arm_j4', 'arm_j5', 'arm_j6']
                    targets = [.1, -.4, .2, 1.2, -.2] if args.standalone else [.1, .2, -.4, .3, 1.2, -.2]
                    arm_message = Float64MultiArray(data=targets)
                    first = position(arm_order[0])
                    spin(.2, lambda: arm.publish(arm_message))
                    assert position(arm_order[0]) == first
                    activate('arm', True)
                    spin(.3, lambda: arm.publish(arm_message))
                    for joint, target in zip(arm_order, targets):
                        assert abs(position(joint) - target) < 1e-8, (joint, position(joint), target)
                    spin(.1, lambda: arm.publish(Float64MultiArray(data=[42.])))
                    spin(.1, lambda: arm.publish(Float64MultiArray(data=[float('nan')] * len(targets))))
                    assert abs(position(arm_order[0]) - targets[0]) < 1e-8
                    activate('arm', False)
                    spin(.2, lambda: arm.publish(Float64MultiArray(data=[.3] * len(targets))))
                    assert abs(position(arm_order[0]) - targets[0]) < 1e-8
                    activate('gripper', True)
                    spin(.2, lambda: gripper.publish(Float64MultiArray(data=[.01])))
                    assert abs(position('arm_gripper') - .01) < 1e-8
                if not args.standalone:
                    twist = Twist(); twist.linear.x = .2
                    spin(.6, lambda: manual.publish(twist))
                    assert odom and abs(odom[-1].twist.twist.linear.x - .2) < .01
                    status = MissionStatus(); status.state = 1
                    twist.linear.x = -.1
                    spin(.7, lambda: (mission.publish(status), autonomous.publish(twist)))
                    assert abs(odom[-1].twist.twist.linear.x + .1) < .01
                    spin(.65)
                    last = joints[-1]
                    for joint in ['fl_wheel_joint', 'fr_wheel_joint', 'rl_wheel_joint', 'rr_wheel_joint']:
                        assert last.velocity[last.name.index(joint)] == 0
                    assert odom[-1].header.frame_id == 'odom'
                    assert odom[-1].child_frame_id == 'base_link'
                    assert odom[-1].pose.covariance[0] == 1e6
                    assert odom[-1].twist.covariance[0] == .05
                spin(1.)
                assert diagnostics
                values = {v.key: v.value for v in diagnostics[-1].status[0].values}
                intervals = sorted(b - a for a, b in zip(arrivals, arrivals[1:]))
                print(json.dumps({'mode': 'standalone' if args.standalone else 'base-only' if args.base_only else 'full-rover',
                                  'joint_messages': len(joints), 'odometry_messages': len(odom),
                                  'joint_interval_p99_ms': intervals[int(.99 * (len(intervals) - 1))] * 1000,
                                  'joint_interval_max_ms': max(intervals) * 1000,
                                  'diagnostics': values}, indent=2))
            finally:
                process.send_signal(signal.SIGINT)
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill(); process.wait()
                assert process.returncode == 0, log_path.read_text()
    node.destroy_node()
    rclpy.shutdown()


if __name__ == '__main__':
    main()
