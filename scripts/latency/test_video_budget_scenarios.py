#!/usr/bin/env python3
"""Exercise the production video node/encoder/decoder in an isolated ROS domain.

Uses synthetic ROS images, loopback RTP and real H.264 decoding. Never opens a
rover camera or changes the production ROS domain. --bin is the installed node
directory; results and logs are written below --output.
"""
import argparse
import json
import os
from pathlib import Path
import select
import signal
import socket
import struct
import subprocess
import time

os.environ['ROS_DOMAIN_ID'] = '176'
os.environ['ROS_LOCALHOST_ONLY'] = '1'
import gi
gi.require_version('Gst', '1.0')
from gi.repository import Gst
import numpy as np
import rclpy
from rclpy.qos import qos_profile_sensor_data
from sensor_msgs.msg import Image
from std_msgs.msg import UInt8MultiArray


def probe_token(session, identifier):
    if not session:
        return identifier
    value = (session ^ (identifier * 0x9e3779b1)) & 0xffffffff
    value = ((value ^ (value >> 16)) * 0x7feb352d) & 0xffffffff
    value = ((value ^ (value >> 15)) * 0x846ca68b) & 0xffffffff
    return (value ^ (value >> 16)) & 0xffffffff


def run(binary, output, failed_prefix=False, cumulative_only=False):
    output.mkdir(parents=True, exist_ok=True)
    Gst.init(None)
    rclpy.init()
    node = rclpy.create_node('video_budget_scenarios')
    ports = [53930, 53932, 53934]
    config = {'version': 1, 'streams': []}
    publishers, images, receivers, sockets = [], [], [], []
    frames = [[] for _ in ports]
    markers = [[] for _ in ports]
    states, timings, errors, probes = {}, {}, [], []
    source = json.loads(Path('rover/ros2_ws/src/mr2_launch/config/video_streams.json').read_text())
    selected = [s for s in source['streams'] if s['stream_id'] in ['rgbd_camera', 'front_left_cam', 'front_right_cam']]
    for i, stream in enumerate(selected):
        height = stream['source']['capture_height']
        item = dict(stream)
        item.update(priority='low', udp_port=ports[i], source={'type': 'ros_topic', 'ros_topic': f'/scenario/camera{i}', 'ros_encoding': 'rgb8'})
        config['streams'].append(item)
        publishers.append(node.create_publisher(Image, f'/scenario/camera{i}', qos_profile_sensor_data))
        pixels = np.random.default_rng(i).integers(0, 256, (height, 640, 3), dtype=np.uint8).tobytes()
        images.append(Image(height=height, width=640, encoding='rgb8', step=1920, data=pixels))
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.bind(('127.0.0.1', ports[i])); sock.setblocking(False); sockets.append(sock)
        pipe = Gst.parse_launch('appsrc name=src is-live=true do-timestamp=true format=time '
            'caps=application/x-rtp,media=video,encoding-name=H264,payload=96,clock-rate=90000 '
            '! rtpjitterbuffer latency=40 drop-on-latency=true ! rtph264depay ! h264parse '
            '! openh264dec ! appsink name=decoded emit-signals=true sync=false max-buffers=1 drop=true')
        def decoded(sink, index=i):
            sample = sink.emit('pull-sample')
            caps = sample.get_caps().get_structure(0)
            frames[index].append({'at': time.monotonic(), 'width': caps.get_value('width'), 'height': caps.get_value('height')})
            return Gst.FlowReturn.OK
        pipe.get_by_name('decoded').connect('new-sample', decoded)
        pipe.set_state(Gst.State.PLAYING)
        receivers.append(pipe)

    config_path = output / 'streams.json'
    if failed_prefix:
        config['streams'].insert(0, {'stream_id': 'failed_first', 'priority': 'low', 'udp_port': 53928,
            'source': {'type': 'v4l2', 'device': '/dev/null', 'pixel_format': 'MJPG', 'capture_width': 640, 'capture_height': 360},
            'width': 320, 'height': 180, 'framerate': 30, 'encoder': {'type': 'nvv4l2h264enc', 'bitrate_kbps': 1800}})
    config_path.write_text(json.dumps(config))
    control = node.create_publisher(UInt8MultiArray, '/video/control', 10)
    def status(message):
        b = bytes(message.data)
        if len(b) != 27: return
        states[int.from_bytes(b[13:15], 'little')] = {
            'state': b[15], 'priority': b[16] & 1, 'enabled': bool(b[16] & 2),
            'bitrate': int.from_bytes(b[17:21], 'little'),
            'width': int.from_bytes(b[21:23], 'little'), 'height': int.from_bytes(b[23:25], 'little'), 'fps': b[25]}
    node.create_subscription(UInt8MultiArray, '/video/state', status, 10)
    timing = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    timing_name = f'mr2-budget-scenarios-{os.getpid()}'
    timing.bind('\0' + timing_name); timing.setblocking(False)
    log = (output / 'node.log').open('w')
    child = subprocess.Popen([str(binary / 'video_streaming_node'), '--ros-args', '-p',
        f'video_config_path:={config_path}', '-p', 'base_host:=127.0.0.1'],
        env={**os.environ, 'MR2_VIDEO_TIMING_SOCKET': timing_name}, stdout=log, stderr=subprocess.STDOUT)
    sequence, request = 0, 0
    last_image = last_feedback = 0
    samples, report = [], {'phases': []}

    def command(index, enabled=True, high=False):
        nonlocal request
        request += 1
        data = b'\x40' + struct.pack('<IIHBB', 1234567, request, ports[index], enabled, high)
        control.publish(UInt8MultiArray(data=list(data)))

    def phase(name, budget, duration, active=(True, True, True), feedback=True, probe_id=0, actions=()):
        nonlocal sequence, last_image, last_feedback
        start = time.monotonic(); cutoff = start + min(4, duration / 2)
        counts = [len(f) for f in frames]
        remaining = list(actions)
        while time.monotonic() - start < duration:
            at = time.monotonic()
            while remaining and at - start >= remaining[0][0]:
                remaining.pop(0)[1]()
            if at - last_image >= 1 / 30:
                for i, pub in enumerate(publishers):
                    if active[i]:
                        images[i].header.stamp = node.get_clock().now().to_msg(); pub.publish(images[i])
                last_image = at
            if feedback and at - last_feedback >= .2:
                sequence += 1
                data = b'\x41' + struct.pack('<IIIIiH', 1234567, sequence, budget, 0, 0, 0)
                if probe_id: data += struct.pack('<HIH', probe_id, budget * 3 // 2, 100)
                control.publish(UInt8MultiArray(data=list(data))); last_feedback = at
            rclpy.spin_once(node, timeout_sec=.001)
            ready = select.select([*sockets, timing], [], [], 0)[0]
            for sock in ready:
                while True:
                    try: data = sock.recv(65536)
                    except BlockingIOError: break
                    if sock is timing:
                        record = json.loads(data); timings[(record['ssrc'], record['rtp_timestamp'])] = record
                        continue
                    i = sockets.index(sock)
                    if len(data) < 36: continue
                    ssrc, timestamp = struct.unpack_from('!I', data, 8)[0], struct.unpack_from('!I', data, 4)[0]
                    if ssrc == 0x4d523250:
                        probes.append({'id': timestamp, 'at': at}); continue
                    if data[1] & 128:
                        markers[i].append({'at': at, 'ssrc': ssrc, 'timestamp': timestamp, 'send_epoch_us': int.from_bytes(data[26:34], 'big')})
                    buf = Gst.Buffer.new_allocate(None, len(data), None); buf.fill(0, data)
                    receivers[i].get_by_name('src').emit('push-buffer', buf)
            if child.poll() is not None: raise AssertionError('video node exited; inspect node.log')
            for pipe in receivers:
                message = pipe.get_bus().pop_filtered(Gst.MessageType.ERROR)
                if message:
                    error, debug = message.parse_error(); errors.append(str(error) + ' ' + str(debug))
            samples.append({'phase': name, 'at': at, 'states': dict(states)})
        result = {'name': name, 'budget': budget, 'states': dict(states),
            'decoded_total': [len(frames[i]) - counts[i] for i in range(3)],
            'decoded_stable': [sum(f['at'] >= cutoff for f in rows) for rows in frames]}
        report['phases'].append(result)
        print(json.dumps(result), flush=True)
        return result

    try:
        if cumulative_only:
            phase('warm5400', 5400, 8)
            phase('smallDrop4320', 4320, .4)
            started = time.monotonic()
            phase('cumulativeDrop3456', 3456, 1.2)
            delays = []
            for rows in frames:
                candidates = [f['at'] - started for f in rows if f['at'] >= started and f['width'] == 480]
                assert candidates and min(candidates) < .8, candidates
                delays.append(min(candidates))
            report['cumulative_downgrade_first_decoded_s'] = delays
            report['passed'] = True
            return
        if failed_prefix:
            r = phase('failedFirstDoesNotBlockJsonSuccessor', 300, 12)
            assert r['decoded_stable'][0] >= 10, r
            report['passed'] = True
            return
        r = phase('minimum32', 32, 7)
        assert [r['states'][p]['bitrate'] for p in ports] == [27, 0, 0], r
        assert r['decoded_stable'][0] >= 2, r
        r = phase('zeroBudget', 0, 3)
        assert [r['states'][p]['bitrate'] for p in ports] == [0, 0, 0], r
        assert r['decoded_stable'] == [0, 0, 0], r
        r = phase('minimum100', 100, 7)
        assert [r['states'][p]['bitrate'] for p in ports] == [85, 0, 0], r
        assert r['decoded_stable'][0] >= 3, r
        r = phase('two600', 600, 8)
        assert [r['states'][p]['bitrate'] for p in ports] == [255, 255, 0], r
        assert min(r['decoded_stable'][:2]) >= 10, r
        r = phase('three900', 900, 7)
        assert [r['states'][p]['bitrate'] for p in ports] == [255] * 3, r
        assert min(r['decoded_stable']) >= 10, r
        r = phase('maximum5400', 5400, 10)
        assert min(r['decoded_stable']) >= 30, r
        assert all(r['states'][p]['width'] == 640 and r['states'][p]['fps'] == 30 for p in ports), r
        r = phase('abrupt5400to100', 100, 7)
        assert r['decoded_stable'][0] >= 3 and r['decoded_stable'][1:] == [0, 0], r
        r = phase('recover5400', 5400, 10)
        assert min(r['decoded_stable']) >= 30, r
        command(1, high=True)
        r = phase('oneHigh900', 900, 9)
        assert [r['states'][p]['bitrate'] for p in ports] == [153, 612, 0], r
        assert min(r['decoded_stable'][:2]) >= 8, r
        command(1, high=False); command(0, enabled=False)
        r = phase('disableFirst900', 900, 7)
        assert [r['states'][p]['bitrate'] for p in ports] == [0, 382, 382], r
        assert min(r['decoded_stable'][1:]) >= 10, r
        command(0)
        r = phase('missingFirst900', 900, 6, active=(False, True, True))
        assert r['states'][ports[0]]['state'] == 2, r
        assert min(r['decoded_stable'][1:]) >= 5, r
        r = phase('cameraReconnect900', 900, 7)
        assert min(r['decoded_stable']) >= 5, r
        assert all(r['states'][p]['width'] == 320 and r['states'][p]['fps'] == 15 for p in ports), r
        actions = [(i * .3, lambda high=bool((i + 1) % 2): command(1, high=high)) for i in range(10)]
        r = phase('tenRapidPriorityChanges', 900, 10, actions=actions)
        assert min(r['decoded_stable']) >= 5, r
        assert all(r['states'][p]['priority'] == 0 for p in ports), r
        r = phase('allCamerasMissing', 900, 5, active=(False, False, False))
        assert all(r['states'][p]['state'] == 2 for p in ports), r
        r = phase('allCamerasReturn', 900, 8)
        assert min(r['decoded_stable']) >= 5, r
        assert all(r['states'][p]['width'] == 320 and r['states'][p]['fps'] == 15 for p in ports), r
        r = phase('feedbackTimeout', 900, 4, feedback=False)
        assert sum(r['states'][p]['bitrate'] for p in ports) <= 425, r
        r = phase('feedbackRestored900', 900, 8, probe_id=1)
        assert min(r['decoded_stable']) >= 10, r
        assert len(probes) >= 5 and {p['id'] for p in probes} == {probe_token(1234567, 1)}, probes
        assert len(probes) < 100, 'duplicate feedback restarted the same probe'
        latencies = []
        for rows in markers:
            for marker in rows:
                t = timings.get((marker['ssrc'], marker['timestamp']))
                if t: latencies.append((marker['send_epoch_us'] - t['ingest_epoch_us']) / 1000)
        latencies.sort()
        report['ingest_to_send_ms'] = {'samples': len(latencies), 'p50': latencies[len(latencies)//2], 'p95': latencies[int(len(latencies)*.95)], 'max': max(latencies)} if latencies else None
        assert latencies and report['ingest_to_send_ms']['p95'] < 250 and max(latencies) < 500, report['ingest_to_send_ms']
        assert not errors, errors
        report['probe_packets'] = len(probes)
        report['passed'] = True
    except Exception as error:
        report.update(passed=False, error=str(error)); raise
    finally:
        (output / 'result.json').write_text(json.dumps(report, indent=2))
        (output / 'states.json').write_text(json.dumps(samples, indent=2))
        child.send_signal(signal.SIGINT)
        try: child.wait(timeout=8)
        except subprocess.TimeoutExpired: child.kill(); child.wait()
        for pipe in receivers: pipe.set_state(Gst.State.NULL)
        for sock in [*sockets, timing]: sock.close()
        log.close(); node.destroy_node(); rclpy.shutdown()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--bin', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--failed-prefix', action='store_true')
    parser.add_argument('--cumulative-only', action='store_true')
    args = parser.parse_args()
    run(args.bin, args.output, args.failed_prefix, args.cumulative_only)
