#!/usr/bin/env python3
"""Real H.264 + native receiver through an isolated delayed UDP relay.

No cameras, field interfaces, routing or Internet settings are touched.
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
import tempfile
import time

import gi
gi.require_version('Gst', '1.0')
from gi.repository import Gst


def run(directory, guarded):
    udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    udp.bind(('127.0.0.1', 0))
    free = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    free.bind(('127.0.0.1', 0)); port = free.getsockname()[1]; free.close()
    obs_read, obs_write = os.pipe()
    decoded = []
    pipe = Gst.parse_launch('appsrc name=in is-live=true do-timestamp=true format=time '
        'caps=video/x-h264,stream-format=byte-stream,alignment=au '
        '! h264parse ! openh264dec ! appsink name=out emit-signals=true sync=false async=false')
    def output(sink):
        sink.emit('pull-sample'); decoded.append(time.monotonic()); return Gst.FlowReturn.OK
    pipe.get_by_name('out').connect('new-sample', output)
    pipe.set_state(Gst.State.PLAYING)
    with tempfile.TemporaryFile() as log:
        env = {**os.environ, **({'MR2_VIDEO_FRESHNESS_FD': '0'} if guarded else {})}
        receiver = subprocess.Popen([str(directory / 'video_receiver'), 'udpsrc', 'name=video_source',
            f'port={port}', 'caps=application/x-rtp,media=video,encoding-name=H264,payload=96,clock-rate=90000',
            '!', 'rtpjitterbuffer', 'name=video_jitter', 'latency=40', 'drop-on-latency=true', '!',
            'rtph264depay', 'wait-for-keyframe=true', 'request-keyframe=true', '!', 'h264parse', '!',
            'video/x-h264,stream-format=byte-stream,alignment=au', '!',
            'appsink', 'name=video_output', 'sync=false', 'async=false'],
            env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=log,
            pass_fds=(obs_write, 4), preexec_fn=lambda: os.dup2(obs_write, 4))
        os.close(obs_write)
        sender = None
        try:
            time.sleep(.15)
            start = time.monotonic()
            sender = subprocess.Popen([str(directory / 'video_sender'), 'freshness_test',
                'videotestsrc', 'name=src', 'is-live=true', 'num-buffers=90', 'pattern=ball', '!',
                'video/x-raw,format=I420,width=320,height=180,framerate=30/1', '!',
                'x264enc', 'name=latency_encoder', 'tune=zerolatency', 'speed-preset=ultrafast',
                'bitrate=128', 'bframes=0', 'key-int-max=1', '!', 'h264parse', '!',
                'rtph264pay', 'name=latency_pay', 'config-interval=1', '!',
                'udpsink', 'name=video_udp', 'host=127.0.0.1', f'port={udp.getsockname()[1]}', 'sync=false', 'async=false'],
                stdout=log, stderr=log)
            pending = []
            units, observations, wire = b'', b'', []
            old_packets = 0
            opened = paused = False
            while time.monotonic() - start < 4:
                now = time.monotonic(); elapsed = now - start
                if not opened and elapsed > .2:
                    receiver.stdin.write(struct.pack('<IIqQQ', 0x4d523246, 0, 0, int(now*1e6), int((now+15)*1e6)))
                    receiver.stdin.flush(); opened = True
                if not paused and elapsed > 2.5:
                    receiver.stdin.write(struct.pack('<IIqQQ', 0x4d523246, 1, 0, int(now*1e6), int((now+15)*1e6)))
                    receiver.stdin.flush(); paused = True
                readable = select.select([udp, receiver.stdout, obs_read], [], [], .002)[0]
                if udp in readable:
                    data = udp.recv(65536)
                    # The packet keeps its actual sender timestamp while this
                    # user-space bottleneck holds it for 1.2 real seconds.
                    pending.append((now + (1.2 if elapsed < 1.5 else 0), data))
                pending.sort(key=lambda p:p[0])
                while pending and pending[0][0] <= now:
                    _, data = pending.pop(0); udp.sendto(data, ('127.0.0.1', port))
                if obs_read in readable:
                    observations += os.read(obs_read, 65536)
                    while len(observations) >= 52:
                        row, observations = observations[:52], observations[52:]
                        arrival, _, sent = struct.unpack('!QQQ', row[:24])
                        old_packets += int(arrival-sent > 1000000)
                if receiver.stdout in readable:
                    units += os.read(receiver.stdout.fileno(), 65536)
                    while len(units) >= 28:
                        size = struct.unpack('!I', units[:4])[0] & 0x7fffffff
                        if len(units) < size+28: break
                        payload, units = units[28:size+28], units[size+28:]
                        wire.append(elapsed)
                        buffer = Gst.Buffer.new_allocate(None, len(payload), None); buffer.fill(0, payload)
                        pipe.get_by_name('in').emit('push-buffer', buffer)
                assert receiver.poll() is None, 'native receiver exited'
            sender.wait(timeout=3)
            assert old_packets >= 10, old_packets
            if guarded:
                assert not any(t < 1.45 for t in wire), wire
                assert not any(t > 2.62 for t in wire), wire
                assert len(decoded) >= 10, (wire, decoded)
            else:
                assert any(t < 1.5 for t in wire), 'control did not display old queued frames'
                assert len(decoded) >= 10, 'control stream did not decode'
            result = {'guarded': guarded, 'decoded_frames': len(decoded), 'old_packets_observed':old_packets,
                      'first_output_s': min(wire) if wire else None, 'last_output_s':max(wire) if wire else None}
            print(json.dumps(result), flush=True)
            log.seek(0); text = log.read().decode(errors='replace')
            assert 'CRITICAL' not in text and 'ERROR' not in text, text
        finally:
            for p in (sender, receiver):
                if p and p.poll() is None:
                    p.send_signal(signal.SIGTERM)
                    try:p.wait(timeout=5)
                    except subprocess.TimeoutExpired:p.kill();p.wait()
            pipe.set_state(Gst.State.NULL)
            udp.close();os.close(obs_read)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bin', required=True, type=Path)
    args = parser.parse_args()
    Gst.init(None)
    for guarded in (False, True):run(args.bin, guarded)


if __name__ == '__main__':
    main()
