#!/usr/bin/env python3
"""Verify native AU framing delivers a 1 FPS frame before the next frame exists."""
import argparse
import json
import os
import select
import struct
import subprocess
import time
from pathlib import Path


def run(binary, native_metadata):
    command = [str(binary),
        'videotestsrc', 'is-live=true', 'num-buffers=3', '!',
        'video/x-raw,width=80,height=60,framerate=1/1', '!',
        'x264enc', 'tune=zerolatency', 'speed-preset=ultrafast', 'key-int-max=1', '!',
        'rtph264pay', 'name=video_source', 'pt=96', '!']
    if native_metadata:
        command += ['rtpjitterbuffer', 'name=video_jitter', 'latency=40', '!']
    command += [
        'rtph264depay', '!', 'h264parse', '!',
        'video/x-h264,stream-format=byte-stream,alignment=au', '!',
        'appsink', 'name=video_output', 'sync=false', 'async=false']
    started = time.monotonic()
    child = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    pending = b''
    frames = []
    try:
        while time.monotonic() - started < 5:
            if not select.select([child.stdout], [], [], .1)[0]:
                continue
            chunk = os.read(child.stdout.fileno(), 65536)
            if not chunk:
                break
            pending += chunk
            while len(pending) >= 4:
                length, = struct.unpack('!I', pending[:4])
                framed = bool(length & 0x80000000)
                length &= 0x7fffffff
                assert framed == native_metadata
                header = 28 if framed else 4
                assert 0 < length < 1024 * 1024, length
                if len(pending) < length + header:
                    break
                if framed:
                    assert struct.unpack('!I', pending[4:8])[0] == 1, 'AU correlation was not verified'
                payload, pending = pending[header:length + header], pending[length + header:]
                assert payload.startswith(b'\0\0\0\1') or payload.startswith(b'\0\0\1')
                frames.append({'at_s': time.monotonic() - started, 'bytes': length})
        child.wait(timeout=2)
        stderr = child.stderr.read().decode(errors='replace')
        assert child.returncode == 0, stderr
        assert len(frames) == 3 and not pending, (frames, len(pending), stderr)
        assert frames[0]['at_s'] < .75, frames
        assert frames[-1]['at_s'] < 2.75, frames
        assert 'CRITICAL' not in stderr and 'ERROR' not in stderr, stderr
        print(json.dumps({'passed': True, 'fps': 1, 'native_metadata': native_metadata, 'frames': frames}))
    finally:
        if child.poll() is None:
            child.kill()
            child.wait()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bin', required=True, type=Path, help='video_receiver executable')
    args = parser.parse_args()
    for native_metadata in (False, True):
        run(args.bin, native_metadata)


if __name__ == '__main__':
    main()
