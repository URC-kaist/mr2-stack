#!/usr/bin/env python3
"""Exercise real GStreamer pad/PTS/RTP matching without opening a camera.

Run after building mr2_video_streaming. Both x264 and Jetson encoders are tested.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'rover/ros2_ws/src/mr2_latency_diagnostics'))
from mr2_latency_diagnostics.video_timing import VideoTimingCollector  # noqa: E402


def main():
    worker = ROOT / 'rover/ros2_ws/install/mr2_video_streaming/lib/mr2_video_streaming/video_sender'
    for encoder in ('x264', 'jetson'):
        args = [str(worker), 'probe_test', 'videotestsrc', 'name=src', 'is-live=true',
                'num-buffers=60', '!', 'video/x-raw,width=320,height=180,framerate=30/1', '!']
        if encoder == 'jetson':
            args += ['nvvidconv', '!', 'video/x-raw(memory:NVMM),format=NV12', '!',
                     'nvv4l2h264enc', 'name=latency_encoder', 'copy-timestamp=true', 'maxperf-enable=true']
        else:
            args += ['x264enc', 'name=latency_encoder', 'tune=zerolatency', 'bframes=0']
        args += ['!', 'h264parse', '!', 'video/x-h264,stream-format=byte-stream,alignment=au',
                 '!', 'rtph264pay', 'name=latency_pay', '!', 'fakesink', 'sync=false']
        address = f'mr2-video-timing-test-{os.getpid()}-{encoder}'
        with VideoTimingCollector(['probe_test'], address='\0' + address) as collector:
            subprocess.run(args, check=True, timeout=15,
                           env={**os.environ, 'MR2_VIDEO_TIMING_SOCKET': address})
            time.sleep(.2)
        samples = collector.samples
        # A cold NVENC open can hold the first raw images past the 100 ms
        # freshness gate. Those images are intentionally omitted before encode;
        # test timing identity over the delivered stream, including a full
        # second of steady output, rather than require stale startup images.
        assert 55 <= len(samples) <= 60, (encoder, len(samples))
        assert len({(s['ssrc'], s['rtp_timestamp'], s['marker_sequence']) for s in samples}) == len(samples)
        assert samples[-1]['ingest_epoch_us'] - samples[0]['ingest_epoch_us'] >= 1700000
        for sample in samples:
            assert 0 <= sample['encode_us'] <= sample['ingest_to_pay_us']
            assert abs(sample['pay_epoch_us'] - sample['ingest_epoch_us'] - sample['ingest_to_pay_us']) < 2000
        print(json.dumps({'encoder': encoder, 'matched_frames': len(samples)}))


if __name__ == '__main__':
    main()
