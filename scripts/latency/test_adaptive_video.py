#!/usr/bin/env python3
"""Real RTP header, pre-jitter timing, pacing and live bitrate IPC smoke test.
No cameras or robot nodes. Usage: python3 .../test_adaptive_video.py --bin DIR
"""
import argparse
import os
from pathlib import Path
import select
import signal
import socket
import struct
import subprocess
import tempfile
import time


def run(directory, hardware=False):
    receive_fd, write_fd = os.pipe()
    parent, child = socket.socketpair(socket.AF_UNIX, socket.SOCK_DGRAM)
    with tempfile.TemporaryFile() as errors:
        receiver = subprocess.Popen([str(directory / 'video_receiver'),
            'udpsrc', 'name=video_source', 'port=53910',
            'caps=application/x-rtp,media=video,encoding-name=H264,payload=96,clock-rate=90000',
            '!', 'rtpjitterbuffer', 'latency=40', '!', 'rtph264depay', '!', 'h264parse',
            '!', 'fakesink', 'sync=false', 'async=false'],
            pass_fds=(write_fd, 4), preexec_fn=lambda: os.dup2(write_fd, 4),
            stdout=subprocess.DEVNULL, stderr=errors)
        os.close(write_fd)
        args = [str(directory / 'video_sender'), 'adaptive_test', 'videotestsrc',
                'name=src', 'is-live=true', 'pattern=snow', 'num-buffers=120', '!',
                'video/x-raw,width=320,height=180,framerate=30/1', '!']
        args += (['nvvidconv', '!', 'video/x-raw(memory:NVMM),format=NV12', '!',
                  'nvv4l2h264enc', 'name=latency_encoder', 'bitrate=1000000',
                  'copy-timestamp=true', 'maxperf-enable=true', 'control-rate=1'] if hardware else
                 ['x264enc', 'name=latency_encoder', 'bitrate=1000', 'tune=zerolatency',
                  'speed-preset=ultrafast', 'bframes=0'])
        args += ['!', 'h264parse', '!', 'rtph264pay', 'name=latency_pay', 'mtu=1176', '!',
                 'udpsink', 'name=video_udp', 'host=127.0.0.1', 'port=53910', 'sync=false', 'async=false']
        time.sleep(.3)
        sender = subprocess.Popen(args, env={**os.environ, 'MR2_VIDEO_CONTROL_FD': str(child.fileno())},
                                  pass_fds=(child.fileno(),), stdout=subprocess.DEVNULL, stderr=errors)
        child.close()
        packets, pending = [], b''
        changed = False
        start = time.monotonic()
        try:
            while time.monotonic() - start < 8 and (sender.poll() is None or select.select([receive_fd], [], [], 0)[0]):
                if not changed and time.monotonic() - start > 1.7:
                    parent.send(struct.pack('=II', 250, 300))
                    changed = True
                if select.select([receive_fd], [], [], .1)[0]:
                    pending += os.read(receive_fd, 65536)
                    while len(pending) >= 52:
                        packet, pending = pending[:52], pending[52:]
                        row = struct.unpack('!QQQQIIHHII', packet)
                        assert row[-2] == 0x4d523201
                        assert row[0] >= row[2]  # same-host monotonic clocks
                        assert row[1] >= row[3]
                        assert row[-3] <= 1228, row[-3]
                        packets.append(row)
            assert sender.wait(timeout=2) == 0
            assert len(packets) > 20, len(packets)
            # Packet lists must be split before UDP sends. Artificial spacing
            # of timestamps followed by one list burst would fail this check.
            groups = {}
            for packet in packets:
                groups.setdefault(packet[5], []).append(packet)
            spans = [(g[-1][0] - g[0][0], g[-1][2] - g[0][2]) for g in groups.values()
                     if len(g) >= 5 and g[-1][2] - g[0][2] > 5000]
            assert spans and sum(a >= s * .5 for a, s in spans) >= len(spans) * .8, spans
            origin = packets[0][0]
            early = sum(p[-3] for p in packets if .3e6 < p[0] - origin < 1.3e6)
            late = sum(p[-3] for p in packets if 2.5e6 < p[0] - origin < 3.5e6)
            assert 0 < late < early * .65, (early, late)
            print(f'{"Jetson" if hardware else "x264"}: {len(packets)} packet observations; bytes/s {early} -> {late}')
        finally:
            for process in (sender, receiver):
                if process.poll() is None:
                    process.send_signal(signal.SIGTERM)
                    try:
                        process.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        process.kill(); process.wait()
            os.close(receive_fd); parent.close()
            errors.seek(0)
            log = errors.read().decode(errors='replace')
            if 'CRITICAL' in log or 'ERROR' in log or sender.returncode:
                raise AssertionError(log)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--bin', type=Path, required=True)
    parser.add_argument('--jetson', action='store_true')
    options = parser.parse_args()
    run(options.bin)
    if options.jetson:
        run(options.bin, hardware=True)
