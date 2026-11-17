#!/usr/bin/env python3
"""Isolated ROS domain + pseudo serial port; no real devices/motors are opened.
Exercise actual C++ bridge/node command, status, retry, budget, and trial lock.
Source ROS and the workspace first, then pass --install overlay install root.
"""
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


def probe_token(session, identifier):
    if not session:
        return identifier
    value = (session ^ (identifier * 0x9e3779b1)) & 0xffffffff
    value = ((value ^ (value >> 16)) * 0x7feb352d) & 0xffffffff
    value = ((value ^ (value >> 15)) * 0x846ca68b) & 0xffffffff
    return (value ^ (value >> 16)) & 0xffffffff


def crc(data):
    value = 0xffff
    for byte in data:
        value ^= byte << 8
        for _ in range(8):
            value = ((value << 1) ^ (0x1021 if value & 0x8000 else 0)) & 0xffff
    return value


def frame(kind, payload):
    body = bytes([0xa5, kind, len(payload), 0]) + payload
    return body + struct.pack('<H', crc(body))


def main(install):
    import rclpy
    import gi
    gi.require_version('Gst', '1.0')
    from gi.repository import Gst
    from sensor_msgs.msg import Image
    from std_msgs.msg import UInt8MultiArray
    from mr2_latency_msgs.srv import AcquireVideoStreamLease, ReleaseVideoStreamLease
    master, slave = pty.openpty()
    buffer = b''
    env = {**os.environ, 'ROS_DOMAIN_ID': '171', 'ROS_LOCALHOST_ONLY': '1', 'MR2_BASE_IP': '127.0.0.1'}
    os.environ.update({k: env[k] for k in ['ROS_DOMAIN_ID', 'ROS_LOCALHOST_ONLY']})
    with tempfile.TemporaryDirectory() as temp, tempfile.TemporaryFile() as logs:
        config = {'streams': [{
            'stream_id': f'camera_{i}', 'udp_port': 53920 + i, 'priority': 'high' if i == 0 else 'low',
            'source': {'type': 'ros_topic', 'ros_topic': f'/test_image_{i}', 'ros_encoding': 'rgb8'},
            'width': 320, 'height': 180, 'framerate': 30,
            'encoder': {'type': 'x264', 'bitrate_kbps': 1800}} for i in range(3)]}
        config['streams'].append({'stream_id': 'missing', 'udp_port': 53923,
            'source': {'type': 'v4l2', 'device': temp + '/absent-camera'}, 'width': 320, 'height': 180})
        cfg = Path(temp) / 'video.json'
        cfg.write_text(json.dumps(config))
        feedback_timer = None
        device = Path(temp) / 'ttyXBEE'
        device.symlink_to(os.ttyname(slave))
        def start(package, executable, args):
            return subprocess.Popen([str(install / package / 'lib' / package / executable), '--ros-args', *args],
                env=env, stdout=logs, stderr=logs)
        def receive(predicate, timeout=8):
            nonlocal buffer
            end = time.monotonic() + timeout
            while time.monotonic() < end:
                rclpy.spin_once(node, timeout_sec=0)
                if select.select([master], [], [], .02)[0]:
                    buffer += os.read(master, 65536)
                while len(buffer) >= 4:
                    if buffer[0] != 0xa5:
                        buffer = buffer[1:]; continue
                    length = buffer[2] + 6
                    if len(buffer) < length:
                        break
                    data, buffer = buffer[:length], buffer[length:]
                    if crc(data[:-2]) != struct.unpack('<H', data[-2:])[0] or data[1] != 0x42:
                        continue
                    p = data[4:-2]
                    row = dict(zip(['boot', 'session', 'request', 'port', 'state', 'flags', 'bitrate', 'width', 'height', 'fps', 'result'], struct.unpack('<IIIHBBIHHBB', p)))
                    if predicate(row):
                        return row
                assert video.poll() is None and bridge.poll() is None, 'ROS process exited'
            raise AssertionError('Timed out waiting for XBee state')
        def command(request, port, enabled, high):
            os.write(master, frame(0x40, struct.pack('<IIHBB', 100, request, port, enabled, high)))
        rclpy.init()
        node = rclpy.create_node('video_control_test')
        forwarded = []
        keyframe_subscription = node.create_subscription(
            UInt8MultiArray, '/video/control', lambda msg: forwarded.append(bytes(msg.data)), 10)
        Gst.init(None)
        sizes = set()
        decoder = 'avdec_h264' if Gst.ElementFactory.find('avdec_h264') else 'openh264dec'
        decode = Gst.parse_launch('udpsrc name=rtp_source port=53920 caps="application/x-rtp,media=video,encoding-name=H264,payload=96,clock-rate=90000" ! rtpjitterbuffer latency=40 ! rtph264depay ! h264parse ! ' + decoder + ' ! appsink name=decoded emit-signals=true sync=false async=false')
        probes = []
        def inspect_probe(_pad, info):
            packet = info.get_buffer()
            data = packet.extract_dup(0, packet.get_size())
            if len(data) >= 36 and data[1] & 127 == 127 and int.from_bytes(data[8:12], 'big') == 0x4d523250:
                probes.append(int.from_bytes(data[4:8], 'big'))
                return Gst.PadProbeReturn.DROP
            return Gst.PadProbeReturn.OK
        decode.get_by_name('rtp_source').get_static_pad('src').add_probe(Gst.PadProbeType.BUFFER, inspect_probe)
        sink = decode.get_by_name('decoded')
        def decoded(appsink):
            sample = appsink.emit('pull-sample')
            caps = sample.get_caps().get_structure(0)
            sizes.add((caps.get_value('width'), caps.get_value('height')))
            return Gst.FlowReturn.OK
        sink.connect('new-sample', decoded)
        decode.set_state(Gst.State.PLAYING)
        publishers = [node.create_publisher(Image, f'/test_image_{i}', 1) for i in range(3)]
        image = Image(height=360, width=640, encoding='rgb8', step=1920, data=bytes([32, 128, 64]) * (640 * 360))
        image_timer = node.create_timer(1 / 30, lambda: [pub.publish(image) for pub in publishers])
        def wait_decoded(size):
            end = time.monotonic() + 6
            while time.monotonic() < end and size not in sizes:
                rclpy.spin_once(node, timeout_sec=.02)
            assert size in sizes, sizes
        video = start('mr2_video_streaming', 'video_streaming_node', ['-p', f'video_config_path:={cfg}', '-p', 'initial_budget_kbps:=3000'])
        bridge = start('mr2_xbee_bridge', 'xbee_bridge_node', ['-p', f'device:={device}', '-p', 'nav_tx_rate_hz:=0.0'])
        try:
            rows = {}
            def ready(p):
                rows[p['port']] = p
                return len(rows) == 4 and all(rows[port]['state'] == 1 for port in [53920, 53921, 53922]) and rows[53920]['bitrate'] == 1530
            receive(ready)
            assert rows[53920]['bitrate'] == 1530, rows
            assert rows[53921]['bitrate'] == 510, rows
            assert rows[53922]['bitrate'] == 510, rows
            assert rows[53923]['state'] == 2
            receive(lambda p: p['port'] == 53920 and p['width'] == 640)
            wait_decoded((640, 360))
            keyframe_payload = struct.pack('<IIH', 100, 33, 53920)
            os.write(master, frame(0x43, keyframe_payload[:-1]))
            os.write(master, frame(0x43, keyframe_payload))
            until = time.monotonic() + 2
            while time.monotonic() < until and bytes([0x43]) + keyframe_payload not in forwarded:
                rclpy.spin_once(node, timeout_sec=.02)
            assert bytes([0x43]) + keyframe_payload in forwarded, 'XBee keyframe request was not forwarded to video control'
            assert not any(b[0] == 0x43 and len(b) != 11 for b in forwarded)
            command(1, 53920, 0, 1)
            assert receive(lambda p: p['request'] == 1)['state'] == 0
            command(1, 53920, 1, 0)  # duplicate request MUST NOT reverse the applied command
            duplicate = receive(lambda p: p['request'] == 1)
            assert duplicate['state'] == 0 and duplicate['flags'] == 1
            command(2, 53920, 1, 1)
            assert receive(lambda p: p['request'] == 2)['state'] == 1
            command(3, 53923, 0, 0)
            missing = receive(lambda p: p['port'] == 53923 and p['request'] == 3)
            assert missing['state'] == 2 and missing['flags'] == 0
            # Budget is aggregate wire kbps, 80:20, with 15% encoder margin.
            os.write(master, frame(0x41, struct.pack('<IIIIiH', 100, 1, 600, 500, 0, 0)))
            assert receive(lambda p: p['port'] == 53920 and p['bitrate'] == 408)['state'] == 1
            receive(lambda p: p['port'] == 53920 and p['width'] == 320)
            wait_decoded((320, 180))
            first_probe_count = None
            for sequence in [2, 3]:
                payload = struct.pack('<IIIIiHHIH', 100, sequence, 600, 500, 0, 0, 7, 900, 100)
                os.write(master, frame(0x41, payload))
                receive(lambda p: p['port'] == 53920 and p['bitrate'] == 408)
                until = time.monotonic() + .4
                while time.monotonic() < until: rclpy.spin_once(node, timeout_sec=.01)
                if first_probe_count is None: first_probe_count = len(probes)
                else: assert len(probes) == first_probe_count, 'duplicate probe command restarted the cluster'
            assert 5 <= len(probes) < 50 and set(probes) == {probe_token(100, 7)}, probes
            # Reproduce a USB endpoint changing while the ROS launch stays up.
            # Missing feedback must pause media, then the new
            # device must carry class ACKs and restore quality with new feedback.
            os.close(master)
            master = -1
            device.unlink()
            until = time.monotonic() + 3.2
            while time.monotonic() < until:
                rclpy.spin_once(node, timeout_sec=.02)
            old_slave = slave
            master, slave = pty.openpty()
            device.symlink_to(os.ttyname(slave))
            os.close(old_slave)
            buffer = b''
            receive(lambda p: p['port'] == 53920 and p['bitrate'] == 0)
            command(4, 53920, 1, 1)
            assert receive(lambda p: p['port'] == 53920 and p['request'] == 4)['result'] == 0
            os.write(master, frame(0x41, struct.pack('<IIIIiH', 100, 4, 1800, 1500, 0, 0)))
            feedback_seq = 4
            def refresh_feedback():
                nonlocal feedback_seq
                feedback_seq += 1
                os.write(master, frame(0x41, struct.pack('<IIIIiH', 100, feedback_seq, 1800, 1500, 0, 0)))
            feedback_timer = node.create_timer(.2, refresh_feedback)
            sizes.clear()
            receive(lambda p: p['port'] == 53920 and p['bitrate'] == 1224)
            receive(lambda p: p['port'] == 53920 and p['width'] == 640)
            wait_decoded((640, 360))
            acquire = node.create_client(AcquireVideoStreamLease, '/video_streaming/acquire_stream_lease')
            assert acquire.wait_for_service(timeout_sec=5)
            req = AcquireVideoStreamLease.Request(owner_id='adaptive-test', stream_ids=['camera_0'], lease_timeout_s=15.)
            future = acquire.call_async(req); rclpy.spin_until_future_complete(node, future, timeout_sec=5)
            assert future.result().accepted
            command(5, 53920, 0, 0)
            locked = receive(lambda p: p['port'] == 53920 and p['request'] == 5)
            assert locked['result'] == 1 and locked['state'] == 1
            periodic = receive(lambda p: p['port'] == 53920 and p['request'] == 5)
            assert periodic['result'] == 1  # a lost rejection must not later become an ACK
            release = node.create_client(ReleaseVideoStreamLease, '/video_streaming/release_stream_lease')
            assert release.wait_for_service(timeout_sec=5)
            future = release.call_async(ReleaseVideoStreamLease.Request(owner_id='adaptive-test'))
            rclpy.spin_until_future_complete(node, future, timeout_sec=5)
            assert future.result().released
            command(6, 53920, 0, 1)
            assert receive(lambda p: p['request'] == 6)['state'] == 0
            print('XBee PTY -> ROS video control -> XBee status: keyframe recovery, 80:20, ON/OFF/NO DEVICE, retry, live decode, USB reconnect, feedback-loss pause/recovery, budget and trial lock passed')
        finally:
            image_timer.cancel()
            if feedback_timer is not None: feedback_timer.cancel()
            decode.set_state(Gst.State.NULL)
            node.destroy_node(); rclpy.shutdown()
            for process in (bridge, video):
                if process.poll() is None:
                    process.send_signal(signal.SIGINT)
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill(); process.wait()
            if master >= 0: os.close(master)
            os.close(slave)
            logs.seek(0)
            if video.returncode not in (0, -2) or bridge.returncode not in (0, -2):
                print(logs.read().decode(errors='replace'))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--install', type=Path, required=True)
    main(parser.parse_args().install)
