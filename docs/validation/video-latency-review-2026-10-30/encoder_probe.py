import gi
import json
import statistics
import time
from pathlib import Path

gi.require_version('Gst', '1.0')
from gi.repository import Gst
Gst.init(None)
rows = []
pipeline = Gst.parse_launch(
    'videotestsrc is-live=true pattern=snow ! '
    'video/x-raw,width=640,height=360,framerate=30/1 ! nvvidconv ! '
    'video/x-raw(memory:NVMM),format=NV12 ! '
    'nvv4l2h264enc name=enc bitrate=1800000 control-rate=1 vbv-size=180000 '
    'iframeinterval=30 idrinterval=30 insert-sps-pps=true maxperf-enable=true ! '
    'fakesink sync=false async=false')
encoder = pipeline.get_by_name('enc')
pad = encoder.get_static_pad('src')
def observe(pad, info):
    b = info.get_buffer()
    if b:
        rows.append((time.monotonic(), b.get_size()))
    return Gst.PadProbeReturn.OK
pad.add_probe(Gst.PadProbeType.BUFFER, observe)
windows = []
try:
    pipeline.set_state(Gst.State.PLAYING)
    for target in (1800000, 300000, 1800000):
        encoder.set_property('bitrate', target)
        time.sleep(1.5)
        start = time.monotonic()
        time.sleep(2.)
        end = time.monotonic()
        data = [size for at, size in rows if start <= at <= end]
        windows.append(dict(target_bps=target, property_bps=encoder.get_property('bitrate'),
                            observed_bps=sum(data)*8/(end-start), frames=len(data),
                            frame_bytes_p50=statistics.median(data) if data else None))
    error = pipeline.get_bus().pop_filtered(Gst.MessageType.ERROR)
    if error:
        raise RuntimeError(str(error.parse_error()))
finally:
    pipeline.set_state(Gst.State.NULL)
report = dict(gstreamer=Gst.version_string(), description='Generated snow; hardware encode to fakesink; no camera or network', windows=windows)
Path('/tmp/mr2-video-latency-review/encoder_probe.json').write_text(json.dumps(report, indent=2)+'\n')
print(json.dumps(report, indent=2))
