"""ROS/SocketCAN adapter for the isolated flat motor controller."""

import fcntl
import json
import math
import socket
import struct
import time

import rclpy
from rclpy.clock import Clock, ClockType
from rclpy.node import Node
from rclpy.qos import QoSProfile
from geometry_msgs.msg import Twist
from mr2_action_interface.msg import MissionControl
from std_msgs.msg import String

from .control import MotorControl, velocity_frame


class FlatMotor(Node):
    def __init__(self):
        super().__init__('flat_motor')
        def param(name, value):
            return self.declare_parameter(name, value).value
        iface = param('can_iface', 'can0')
        self.motor_id = param('motor_id', 104)
        if not 1 <= self.motor_id <= 255 or not iface.isalnum():
            raise ValueError('Invalid CAN interface or motor ID')
        if self.get_parameter('use_sim_time').value:
            raise ValueError('Hardware control requires use_sim_time=false')
        self.control = MotorControl(
            speed=param('max_speed_deg_s', 180.0),
            clockwise_sign=param('clockwise_sign', -1),
            acceleration=param('max_accel_deg_s2', 720.0))
        self.command_topic = param('command_topic', '/base/cmd_vel')
        self.command_component = param('command_component', 'linear.x')
        self.full_scale = param('full_scale_input', 0.8)
        if self.command_component not in ('linear.x', 'linear.y', 'angular.z'):
            raise ValueError('command_component must be linear.x, linear.y or angular.z')
        if not math.isfinite(self.full_scale) or self.full_scale <= 0:
            raise ValueError('full_scale_input must be positive')
        self.lock = open(f'/tmp/mr2-flat-motor-{iface}-{self.motor_id}.lock', 'a')
        fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        self.can = socket.socket(socket.PF_CAN, socket.SOCK_RAW, socket.CAN_RAW)
        self.can.setsockopt(socket.SOL_CAN_RAW, socket.CAN_RAW_ERR_FILTER,
                            struct.pack('I', 0x1FFFFFFF))
        self.can.setsockopt(socket.SOL_CAN_RAW, socket.CAN_RAW_FILTER,
                            struct.pack('=II', socket.CAN_EFF_FLAG | self.motor_id,
                                        socket.CAN_EFF_FLAG | socket.CAN_RTR_FLAG | 0xFF))
        self.can.bind((iface,))
        self.can.setblocking(False)
        self.create_subscription(Twist, self.command_topic, self.command, QoSProfile(depth=1))
        self.create_subscription(MissionControl, '/mission_control', self.mission_control, 10)
        self.publisher = self.create_publisher(String, '/flat_motor/status', 1)
        self.last_tick = self.last_status = time.monotonic()
        self.last_fault = ''
        self.create_timer(0.02, self.tick, clock=Clock(clock_type=ClockType.STEADY_TIME))
        self.get_logger().info(f'Motor {self.motor_id} on {iface}, input {self.command_topic}.{self.command_component}; waiting for stable feedback and neutral commands')

    def command(self, msg):
        group, axis = self.command_component.split('.')
        value = getattr(getattr(msg, group), axis)
        self.control.command(value / self.full_scale, time.monotonic())

    def mission_control(self, msg):
        if msg.command == 3:
            self.control.emergency_stop()

    def tick(self):
        now = time.monotonic()
        try:
            # Bound RX work so unrelated bus traffic cannot starve the watchdog.
            for _ in range(256):
                try:
                    raw = self.can.recv(16)
                except BlockingIOError:
                    break
                cid, dlc, data = struct.unpack('=IB3x8s', raw)
                if cid & socket.CAN_ERR_FLAG:
                    self.control.fail(f'CAN error {cid:#x}')
                elif cid == (socket.CAN_EFF_FLAG | 0x2900 | self.motor_id) and dlc == 8:
                    self.control.feedback(data, now)
                elif (cid & 0xE0000000 == socket.CAN_EFF_FLAG
                      and cid & 0xFF == self.motor_id
                      and (cid & 0x1FFFFF00) >> 8 in (*range(7), 15)):
                    self.control.fail('Another CAN sender is commanding this motor')
            velocity = self.control.step(now, now - self.last_tick)
            if velocity is not None:
                self.can.send(velocity_frame(self.motor_id, velocity))
        except (OSError, ValueError, struct.error) as exc:
            self.control.fail(f'CAN I/O failure: {exc}')
        self.last_tick = now
        if self.control.fault != self.last_fault:
            self.last_fault = self.control.fault
            if self.last_fault:
                self.get_logger().error(self.last_fault)
        if now - self.last_status >= 0.1:
            self.publisher.publish(String(data=json.dumps(
                dict(motor_id=self.motor_id, command_topic=self.command_topic,
                     command_component=self.command_component,
                     **self.control.status(now)))))
            self.last_status = now

    def close(self):
        # Stop an earlier velocity command even if feedback has disappeared.
        if self.control.engaged:
            try:
                self.can.send(velocity_frame(self.motor_id, 0.0))
            except OSError:
                pass
        self.can.close()
        self.lock.close()


def main(args=None):
    rclpy.init(args=args)
    node = None
    try:
        node = FlatMotor()
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        if node is not None:
            node.close()
            node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()
