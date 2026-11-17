#!/usr/bin/env python3
"""Receive-only CAN/ROS journal with periodic fdatasync for power-cut tests.

Run before ROS launch. Redirect launch output to OUTPUT/console.log. Individual
JSONL records survive an unclean exit independently; discard an incomplete final
line when recovering. A sync interval is a target, not a power-loss guarantee.
"""

import argparse
import json
import math
import os
from pathlib import Path
import queue
import signal
import socket
import struct
import threading
import time


def clean(value):
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {k: clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [clean(v) for v in value]
    return value


def sync_directory(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


class Journal:
    def __init__(self, directory, interval):
        self.directory = directory
        self.interval = interval
        self.pending = queue.Queue(maxsize=100000)
        self.closing = threading.Event()
        self.dropped = 0
        self.drop_lock = threading.Lock()
        self.worker = threading.Thread(target=self.write_loop, daemon=True)
        self.worker.start()

    def record(self, kind, **payload):
        record = dict(kind=kind, epoch_ns=time.time_ns(),
                      monotonic_ns=time.monotonic_ns(), **payload)
        try:
            self.pending.put_nowait(record)
        except queue.Full:
            with self.drop_lock:
                self.dropped += 1

    def write_loop(self):
        try:
            self._write_loop()
        except BaseException as exc:
            os.write(2, f"FATAL: experiment journal failed: {exc}\n".encode())
            os._exit(2)

    def _write_loop(self):
        count = 0
        max_sync_ms = 0.0
        max_sync_gap_ms = 0.0
        last_sync = time.monotonic()
        next_sync = last_sync + self.interval
        next_status = last_sync
        with (self.directory / 'events.jsonl').open('x', buffering=65536) as out:
            sync_directory(self.directory)
            while not self.closing.is_set() or not self.pending.empty():
                try:
                    row = self.pending.get(timeout=max(0, next_sync - time.monotonic()))
                    count += 1
                    row['sequence'] = count
                    out.write(json.dumps(clean(row), allow_nan=False, separators=(',', ':')) + '\n')
                except queue.Empty:
                    pass
                if time.monotonic() >= next_sync:
                    started = time.monotonic()
                    out.flush()
                    os.fdatasync(out.fileno())
                    finished = time.monotonic()
                    max_sync_ms = max(max_sync_ms, (finished - started) * 1000)
                    max_sync_gap_ms = max(max_sync_gap_ms, (finished - last_sync) * 1000)
                    last_sync = finished
                    next_sync = finished + self.interval
                    if finished >= next_status:
                        status = dict(pid=os.getpid(), last_sync_epoch_ns=time.time_ns(),
                                      synced_records=count, queue_depth=self.pending.qsize(),
                                      queue_dropped=self.dropped, max_sync_ms=max_sync_ms,
                                      max_sync_gap_ms=max_sync_gap_ms,
                                      target_sync_interval_ms=self.interval * 1000)
                        tmp = self.directory / 'status.json.tmp'
                        with tmp.open('w') as status_file:
                            json.dump(status, status_file, indent=2)
                            status_file.flush()
                            os.fsync(status_file.fileno())
                        tmp.replace(self.directory / 'status.json')
                        sync_directory(self.directory)
                        next_status = finished + 1
            out.flush()
            os.fdatasync(out.fileno())

    def close(self):
        self.closing.set()
        self.worker.join()


def receive_can(journal, stop, interface):
    while not stop.is_set():
        try:
            with socket.socket(socket.PF_CAN, socket.SOCK_RAW, socket.CAN_RAW) as sock:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4 * 1024 * 1024)
                sock.setsockopt(socket.SOL_CAN_RAW, 2, struct.pack('=I', 0x1FFFFFFF))
                # Linux CAN_RAW_FD_FRAMES, SO_TIMESTAMPNS, SO_RXQ_OVFL.
                sock.setsockopt(socket.SOL_CAN_RAW, 5, 1)
                sock.setsockopt(socket.SOL_SOCKET, 35, 1)
                sock.setsockopt(socket.SOL_SOCKET, 40, 1)
                sock.settimeout(0.2)
                sock.bind((interface,))
                journal.record('can_open', interface=interface,
                               receive_buffer=sock.getsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF))
                while not stop.is_set():
                    try:
                        frame, ancillary, flags, _ = sock.recvmsg(72, 128)
                    except socket.timeout:
                        continue
                    if len(frame) < 16:
                        journal.record('can_short_frame', size=len(frame))
                        continue
                    can_id, length = struct.unpack_from('=IB', frame)
                    extra = {}
                    for level, kind, data in ancillary:
                        if level == socket.SOL_SOCKET and kind == 35 and len(data) >= 16:
                            sec, ns = struct.unpack_from('=qq', data)
                            extra['kernel_epoch_ns'] = sec * 1000000000 + ns
                        elif level == socket.SOL_SOCKET and kind == 40:
                            extra['socket_dropped'] = struct.unpack_from('=I', data)[0]
                    journal.record('can', can_id=can_id, length=length,
                                   data=frame[8:8 + length].hex(), recv_flags=flags, **extra)
        except OSError as exc:
            journal.record('can_error', error=str(exc))
            stop.wait(0.5)


def follow_console(journal, stop, path):
    with path.open(errors='replace') as stream:
        while not stop.is_set():
            if path.stat().st_size < stream.tell():
                stream.seek(0)
                journal.record('console_truncated')
            line = stream.readline()
            if line:
                journal.record('console', line=line.rstrip('\n'))
            else:
                stop.wait(0.02)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--can', default='can0')
    parser.add_argument('--sync-ms', default=100, type=int)
    args = parser.parse_args()
    if not 10 <= args.sync_ms <= 1000:
        parser.error('--sync-ms must be between 10 and 1000')
    args.output.mkdir(exist_ok=False)
    sync_directory(args.output.parent)
    console = args.output / 'console.log'
    console.touch()
    journal = Journal(args.output, args.sync_ms / 1000)
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    journal.record('recorder_start', pid=os.getpid(), interface=args.can,
                   sync_ms=args.sync_ms, ros_domain=os.environ.get('ROS_DOMAIN_ID', '0'))

    import rclpy
    from rclpy.qos import QoSProfile, ReliabilityPolicy, DurabilityPolicy
    from rosidl_runtime_py.convert import message_to_ordereddict
    from sensor_msgs.msg import JointState
    from std_msgs.msg import Float64MultiArray, Int8
    from geometry_msgs.msg import TwistStamped, Twist
    from control_msgs.msg import JointJog
    from diagnostic_msgs.msg import DiagnosticArray
    from rcl_interfaces.msg import Log

    rclpy.init()
    node = rclpy.create_node('arm_experiment_recorder')
    reliable = QoSProfile(depth=1000, reliability=ReliabilityPolicy.RELIABLE,
                          durability=DurabilityPolicy.VOLATILE)
    best_effort = QoSProfile(depth=1000, reliability=ReliabilityPolicy.BEST_EFFORT,
                             durability=DurabilityPolicy.VOLATILE)
    topics = [
        ('/joint_states', JointState, best_effort),
        ('/manipulator_controller/commands', Float64MultiArray, reliable),
        ('/gripper_position_controller/commands', Float64MultiArray, reliable),
        ('/gripper_controller/commands', Float64MultiArray, reliable),
        ('/moveit_servo/delta_twist_cmds', TwistStamped, reliable),
        ('/moveit_servo/delta_joint_cmds', JointJog, reliable),
        ('/moveit_servo/status', Int8, reliable),
        ('/mr2_control/diagnostics', DiagnosticArray, reliable),
        ('/rosout', Log, reliable),
        ('/cmd_vel', Twist, reliable),
    ]
    subscriptions = []
    for topic, kind, qos in topics:
        subscriptions.append(node.create_subscription(
            kind, topic, lambda msg, topic=topic: journal.record(
                'ros', topic=topic, message=message_to_ordereddict(msg)), qos))
    threads = [threading.Thread(target=receive_can, args=(journal, stop, args.can), daemon=True),
               threading.Thread(target=follow_console, args=(journal, stop, console), daemon=True)]
    for thread in threads:
        thread.start()
    journal.record('subscriptions_ready', topics=[t[0] for t in topics])
    print(f'RECORDER_READY output={args.output.resolve()} sync_ms={args.sync_ms}', flush=True)
    last_heartbeat = 0
    try:
        while not stop.is_set() and rclpy.ok():
            rclpy.spin_once(node, timeout_sec=0.05)
            if time.monotonic() - last_heartbeat >= 1:
                last_heartbeat = time.monotonic()
                stats = {}
                for key in ('rx_packets', 'tx_packets', 'rx_errors', 'rx_dropped', 'tx_errors'):
                    try:
                        stats[key] = int(Path(f'/sys/class/net/{args.can}/statistics/{key}').read_text())
                    except OSError:
                        pass
                journal.record('heartbeat', can_stats=stats, queue_dropped=journal.dropped)
    finally:
        stop.set()
        for thread in threads:
            thread.join(timeout=2)
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()
        journal.record('recorder_stop')
        journal.close()


if __name__ == '__main__':
    main()
