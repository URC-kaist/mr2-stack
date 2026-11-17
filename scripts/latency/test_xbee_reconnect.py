#!/usr/bin/env python3
"""Actual bridge hot-unplug/replug test in ROS domain 174; only PTYs, no hardware."""
import argparse
import json
import os
from pathlib import Path
import pty
import select
import signal
import struct
import subprocess
import tempfile
import time
import tty

from test_video_xbee_control import crc, frame


def main(install, output):
    os.environ.update(ROS_DOMAIN_ID='174', ROS_LOCALHOST_ONLY='1')
    import rclpy
    from std_msgs.msg import UInt8MultiArray
    rclpy.init()
    node = rclpy.create_node('xbee_reconnect_test')
    received = []
    publisher = node.create_publisher(UInt8MultiArray, '/video/state', 20)
    def command(msg):
        data = bytes(msg.data)
        if data[0] != 0x40:
            return
        session, request, port, enabled, high = struct.unpack('<IIHBB', data[1:])
        received.append(request)
        state = struct.pack('<IIIHBBIHHBB', 7, session, request, port,
                            enabled, enabled | (high << 1), 408, 320, 180, 30, 0)
        publisher.publish(UInt8MultiArray(data=[0x42, *state]))
    subscription = node.create_subscription(UInt8MultiArray, '/video/control', command, 10)
    del subscription  # The node retains the subscription.
    output.mkdir(parents=True, exist_ok=True)
    report = {'phases': []}
    child = None
    master = slave = None
    pending = b''
    try:
        with tempfile.TemporaryDirectory(prefix='mr2-xbee-reconnect-') as temp, (output/'bridge.log').open('w') as log:
            device = Path(temp)/'ttyXBEE'
            child = subprocess.Popen([str(install/'mr2_xbee_bridge/lib/mr2_xbee_bridge/xbee_bridge_node'),
                '--ros-args', '-p', f'device:={device}', '-p', 'nav_tx_rate_hz:=0.0',
                '-p', 'battery_tx_rate_hz:=0.0'], stdout=log, stderr=subprocess.STDOUT)
            def spin(seconds):
                end = time.monotonic()+seconds
                while time.monotonic() < end:
                    assert child.poll() is None, 'Bridge exited'
                    rclpy.spin_once(node, timeout_sec=.02)
            def receive(predicate, timeout=6):
                nonlocal pending
                end = time.monotonic()+timeout
                while time.monotonic() < end:
                    spin(.02)
                    if select.select([master], [], [], .02)[0]:
                        pending += os.read(master, 65536)
                    while len(pending) >= 4:
                        if pending[0] != 0xa5:
                            pending = pending[1:]; continue
                        size = pending[2]+6
                        if len(pending) < size: break
                        packet, pending = pending[:size], pending[size:]
                        assert crc(packet[:-2]) == struct.unpack('<H', packet[-2:])[0]
                        if packet[1] == 0x42:
                            assert struct.unpack_from('<I', packet, 12)[0] != 999, 'Disconnected TX was replayed'
                        if predicate(packet): return packet
                raise AssertionError('No heartbeat/ACK after reconnect')
            def connect():
                nonlocal master, slave, pending
                master, slave = pty.openpty()
                tty.setraw(slave)
                # Kernel UART input left over before open must also be flushed.
                os.write(master, frame(0x40, struct.pack('<IIHBB', 123, 999, 53920, 1, 1)))
                staging = device.with_suffix('.next')
                staging.symlink_to(os.ttyname(slave));os.replace(staging, device)
                pending = b''
                receive(lambda p: p[1] == 3)
            def exchange(request):
                os.write(master, frame(0x40, struct.pack('<IIHBB', 123, request, 53920, 1, request % 2)))
                packet = receive(lambda p: p[1] == 0x42 and struct.unpack_from('<I', p, 12)[0] == request)
                assert struct.unpack_from('<I', packet, 8)[0] == 123
                assert received[-1] == request
            spin(2)
            report['phases'].append('startup_device_absent_survives')
            device.write_text('not a serial device')
            spin(1.5);device.unlink()
            report['phases'].append('invalid_tty_survives')
            connect();exchange(1)
            report['phases'].append('first_heartbeat_and_class_ack')
            reconnect_times = []
            for cycle in range(6):
                # Leave an incomplete command in the old parser, then remove
                # the USB endpoint while preserving the stable symlink name.
                old_command = frame(0x40, struct.pack('<IIHBB', 123, 999, 53920, 1, 1))
                os.write(master, old_command[:7]);spin(.1)
                os.close(master);master = None
                old_slave = slave;slave = None
                if cycle % 2 == 0: device.unlink()
                spin(1.2)
                # Outgoing state while disconnected must not be replayed.
                stale = struct.pack('<IIIHBBIHHBB', 7, 123, 999, 53920, 1, 3, 408, 320, 180, 30, 0)
                publisher.publish(UInt8MultiArray(data=[0x42, *stale]));spin(.2)
                start = time.monotonic()
                connect();os.close(old_slave)
                reconnect_times.append(time.monotonic()-start)
                os.write(master, old_command[7:]);spin(.1)
                assert 999 not in received, 'Partial old command was replayed'
                exchange(cycle+2)
                report['phases'].append(f'reconnect_{cycle+1}_heartbeat_and_class_ack')
            report['reconnect_seconds'] = reconnect_times
            report['requests_received'] = received
            assert received == list(range(1, 8)), received
            report['passed'] = True
    finally:
        if child and child.poll() is None:
            child.send_signal(signal.SIGINT)
            try: child.wait(timeout=3)
            except subprocess.TimeoutExpired: child.kill();child.wait()
        for descriptor in [master, slave]:
            if descriptor is not None: os.close(descriptor)
        node.destroy_node();rclpy.shutdown()
        (output/'result.json').write_text(json.dumps(report, indent=2)+'\n')
        print(json.dumps(report, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--install', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    main(args.install.resolve(), args.output)
