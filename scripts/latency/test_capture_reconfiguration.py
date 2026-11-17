#!/usr/bin/env python3
"""Exercise the production persistent capture/encoder path without a camera."""
import json
import os
from pathlib import Path
import select
import socket
import struct
import subprocess
import tempfile
import time

ROOT = Path(__file__).resolve().parents[2]


def main():
    receiver = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    receiver.bind(('127.0.0.1', 0))
    parent, control = socket.socketpair(socket.AF_UNIX, socket.SOCK_DGRAM)
    worker = ROOT / 'rover/ros2_ws/install/mr2_video_streaming/lib/mr2_video_streaming/video_sender'
    command = [str(worker), 'split_test', 'videotestsrc', 'name=src', 'is-live=true', 'pattern=snow', '!',
               'video/x-raw,format=I420,width=640,height=360,framerate=30/1', '!',
               'queue', 'leaky=downstream', 'max-size-buffers=1', 'max-size-bytes=0', 'max-size-time=0', '!',
               'videorate', 'drop-only=true', '!', 'video/x-raw,framerate=30/1', '!',
               'nvvidconv', '!', 'video/x-raw(memory:NVMM),format=NV12,width=320,height=180', '!',
               'nvv4l2h264enc', '!', 'h264parse', '!', 'rtph264pay', '!', 'udpsink',
               'host=127.0.0.1', f'port={receiver.getsockname()[1]}', 'sync=false', 'async=false']
    env = {**os.environ, 'MR2_VIDEO_PROFILE': '510 600 320 180 30 51000 30 2',
           'MR2_VIDEO_CONTROL_FD': str(control.fileno())}
    phases = [(600, 510, 320, 180, 30, 2), (1800, 1530, 640, 360, 30, 2),
              (300, 255, 320, 180, 15, 2), (100, 85, 160, 90, 5, 2),
              (32, 27, 80, 44, 3, 0), (0, 0, 80, 44, 3, 4),
              (32, 27, 80, 44, 3, 0), (1800, 1530, 640, 360, 30, 2)]
    with tempfile.TemporaryFile() as log:
        child = subprocess.Popen(command, env=env, pass_fds=[control.fileno()], stdout=log, stderr=log)
        try:
            for wire, encoder, width, height, fps, flags in phases:
                vbv = max(100, 1000 // fps + 1) * encoder
                parent.send(struct.pack('=9I', 0x4d523243, encoder, wire, width, height, fps, vbv, min(fps, 30), flags))
                start = time.monotonic()
                markers = 0
                timestamps = []
                while time.monotonic() - start < 3:
                    if child.poll() is not None:
                        raise RuntimeError(f'capture worker exited: {child.returncode}')
                    if select.select([receiver], [], [], .1)[0]:
                        packet = receiver.recv(65536)
                        marker = len(packet) > 12 and packet[1] & 128 != 0
                        markers += int(marker)
                        if marker:
                            timestamps.append(struct.unpack('!I', packet[4:8])[0])
                if wire == 0:
                    assert markers <= 1, ('paused capture kept transmitting', markers)
                    print(json.dumps(dict(paused=True, capture_worker_alive=child.poll() is None, received_frames=markers)), flush=True)
                    continue
                assert markers > 0, (wire, width, height, fps)
                deltas = [((b - a) & 0xffffffff) for a, b in zip(timestamps[-9:], timestamps[-8:])]
                assert deltas and all(90000 / fps - 1 <= d <= 90000 / fps * 3 for d in deltas[-4:]), (fps, deltas)
                print(json.dumps(dict(wire_kbps=wire, width=width, height=height, fps=fps,
                                      received_frames=markers, final_rtp_intervals=deltas)), flush=True)
        except Exception:
            log.seek(0)
            print(log.read().decode(errors='replace'))
            raise
        finally:
            child.terminate()
            try:
                child.wait(timeout=8)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()
            log.seek(0)
            errors = log.read().decode(errors='replace')
            print('\n'.join(line for line in errors.splitlines() if 'encoder profile' in line), flush=True)
            parent.close()
            control.close()
            receiver.close()
    assert 'CRITICAL' not in errors and 'ERROR' not in errors, errors


if __name__ == '__main__':
    main()
