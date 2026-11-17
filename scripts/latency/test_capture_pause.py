#!/usr/bin/env python3
"""Opt-in physical V4L2 test: a >5 s budget pause must retain capture on resume.

Stop the normal camera application before running this test. It uses one
explicitly selected physical camera, an isolated ROS domain and loopback UDP;
it does not change camera controls, network interfaces, radios or clock policy.
"""
import argparse
import json
import os
from pathlib import Path
import signal
import socket
import struct
import subprocess
import time

os.environ['ROS_DOMAIN_ID'] = '178'
os.environ['ROS_LOCALHOST_ONLY'] = '1'
import gi
gi.require_version('Gst', '1.0')
from gi.repository import Gst
import rclpy
from std_msgs.msg import UInt8MultiArray


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bin', required=True, type=Path)
    parser.add_argument('--stream', required=True, help='Physical stream in the central JSON')
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--resume-stall-ms', type=int, default=700,
                        help='Briefly stall the paused worker to model delayed encoder scheduling')
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    root = Path(__file__).resolve().parents[2]
    source = json.loads((root / 'rover/ros2_ws/src/mr2_launch/config/video_streams.json').read_text())
    stream = next(s for s in source['streams'] if s['stream_id'] == args.stream)
    assert stream['source']['type'] == 'v4l2'
    stream = {**stream, 'priority': 'low', 'enabled': True}
    free = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    free.bind(('127.0.0.1', 0)); stream['udp_port'] = free.getsockname()[1]; free.close()
    config = args.output / 'streams.json'
    config.write_text(json.dumps({'version': 1, 'streams': [stream]}))
    Gst.init(None)
    frames = []
    decode = Gst.parse_launch(
        f'udpsrc port={stream["udp_port"]} '
        'caps=application/x-rtp,media=video,encoding-name=H264,payload=96,clock-rate=90000 '
        '! rtpjitterbuffer latency=40 drop-on-latency=true ! rtph264depay ! h264parse '
        '! openh264dec ! appsink name=decoded emit-signals=true sync=false async=false')

    def decoded(sink):
        sink.emit('pull-sample'); frames.append(time.monotonic()); return Gst.FlowReturn.OK

    decode.get_by_name('decoded').connect('new-sample', decoded)
    decode.set_state(Gst.State.PLAYING)
    rclpy.init(); node = rclpy.create_node('capture_pause_regression')
    pub = node.create_publisher(UInt8MultiArray, '/video/control', 10)
    child = None; sequence = 0; held_worker = None; release_at = 0
    log = (args.output / 'node.log').open('w')

    def phase(budget, duration):
        nonlocal sequence, held_worker
        started = time.monotonic(); last = -float('inf')
        while time.monotonic() - started < duration:
            at = time.monotonic()
            if held_worker and at >= release_at:
                try: os.kill(held_worker, signal.SIGCONT)
                except ProcessLookupError: pass
                held_worker = None
            if at - last >= .2:
                sequence += 1
                data = b'\x41' + struct.pack('<IIIIiH', 334455, sequence, budget, 0, 0, 0)
                pub.publish(UInt8MultiArray(data=list(data))); last = at
            rclpy.spin_once(node, timeout_sec=.01)
            assert child.poll() is None, 'video node exited'
        return [p for p in frames if p >= started], started

    def workers():
        # Jetson kernels need not expose /proc/PID/task/PID/children.
        rows = subprocess.check_output(
            ['ps', '-o', 'pid=,comm=', '--ppid', str(child.pid)], text=True)
        return sorted(int(row.split()[0]) for row in rows.splitlines() if 'video_sender' in row)

    try:
        child = subprocess.Popen([str((args.bin / 'video_streaming_node').resolve()), '--ros-args',
            '-p', f'video_config_path:={config.resolve()}', '-p', 'base_host:=127.0.0.1'],
            stdout=log, stderr=subprocess.STDOUT)
        warm, _ = phase(1800, 4)
        assert len(warm) >= 20, 'physical camera did not warm up'
        before = workers(); assert len(before) == 1, before
        paused, pause_start = phase(0, 6.5)
        assert not any(p > pause_start + .5 for p in paused), 'media continued while paused'
        during = workers()
        assert 0 <= args.resume_stall_ms < 2000
        if args.resume_stall_ms:
            held_worker = before[0]; os.kill(held_worker, signal.SIGSTOP)
            release_at = time.monotonic() + args.resume_stall_ms / 1000
        resumed, started = phase(1800, 3)
        after = workers()
        result = {'warm_frames': len(warm), 'pause_s': 6.5, 'pause_worker': during,
                  'before_worker': before, 'after_worker': after, 'resumed_frames': len(resumed),
                  'first_resumed_frame_ms': (resumed[0] - started) * 1000 if resumed else None,
                  'capture_retained': before == during == after,
                  'resume_scheduling_stall_ms': args.resume_stall_ms}
        (args.output / 'result.json').write_text(json.dumps(result, indent=2))
        print(json.dumps(result), flush=True)
        assert before == during == after, 'resume needlessly restarted V4L2 capture'
        assert len(resumed) >= 30, 'resumed camera did not decode'
    finally:
        if held_worker:
            try: os.kill(held_worker, signal.SIGCONT)
            except ProcessLookupError: pass
        if child and child.poll() is None:
            child.send_signal(signal.SIGINT)
            try: child.wait(timeout=10)
            except subprocess.TimeoutExpired: child.kill(); child.wait()
        log.close(); decode.set_state(Gst.State.NULL); node.destroy_node(); rclpy.shutdown()


if __name__ == '__main__':
    main()
