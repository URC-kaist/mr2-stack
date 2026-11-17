import base64
import gi
import json
import re
import time
from pathlib import Path

gi.require_version('Gst', '1.0')
gi.require_version('GstApp', '1.0')
from gi.repository import Gst
Gst.init(None)
out = Path('/tmp/mr2-steady-latency-review')
(out/'fixtures').mkdir(parents=True, exist_ok=True)


def stats(values):
    v = sorted(values)
    return dict(n=len(v), p50_ms=v[(len(v)-1)//2], p95_ms=v[int((len(v)-1)*.95)], max_ms=v[-1]) if v else None


def run(poc, rate=True, fps=30, count=96, tag=None):
    frames, stages = [], {}
    description = (f'videotestsrc name=src is-live=true num-buffers={count} pattern=snow ! '
        f'video/x-raw,width=640,height=360,framerate={fps}/1 ! '
        'queue leaky=downstream max-size-buffers=1 max-size-bytes=0 max-size-time=0 ! ' +
        (f'videorate name=rate drop-only=true ! video/x-raw,framerate={fps}/1 ! ' if rate else '') +
        'nvvidconv ! video/x-raw(memory:NVMM),format=NV12 ! '
        f'nvv4l2h264enc name=enc copy-timestamp=true bitrate=1800000 vbv-size={1800*max(100,1000//fps+1)} '
        f'poc-type={poc} iframeinterval={min(fps,30)} idrinterval={min(fps,30)} insert-sps-pps=true maxperf-enable=true control-rate=1 ! '
        'video/x-h264,stream-format=byte-stream,alignment=au,level=(string)3.1 ! '
        'h264parse ! video/x-h264,stream-format=byte-stream,alignment=au ! '
        'appsink name=sink sync=false async=false emit-signals=true')
    p = Gst.parse_launch(description)
    def probe(label):
        def callback(pad, info):
            b = info.get_buffer()
            if b:
                stages.setdefault(int(b.pts), {})[label] = time.monotonic_ns()
            return Gst.PadProbeReturn.OK
        return callback
    for element, pad, label in [('src','src','src'),('enc','sink','enc_in'),('enc','src','enc_out')]+([('rate','src','rate')] if rate else []):
        p.get_by_name(element).get_static_pad(pad).add_probe(Gst.PadProbeType.BUFFER, probe(label))
    def sample(sink):
        s = sink.emit('pull-sample');b = s.get_buffer()
        raw = b.extract_dup(0,b.get_size())
        nals = [n for n in re.split(b'\x00\x00\x00?\x01',raw) if n]
        frames.append(dict(timestamp_us=int(b.pts//1000),key=any(n[0]&31==5 for n in nals),payload=base64.b64encode(raw).decode()))
        return Gst.FlowReturn.OK
    p.get_by_name('sink').connect('new-sample', sample)
    try:
        p.set_state(Gst.State.PLAYING)
        msg=p.get_bus().timed_pop_filtered(12*Gst.SECOND,Gst.MessageType.ERROR|Gst.MessageType.EOS)
        if msg is None:raise RuntimeError('pipeline timeout')
        if msg.type==Gst.MessageType.ERROR:raise RuntimeError(str(msg.parse_error()))
    finally:p.set_state(Gst.State.NULL)
    valid=[v for _,v in sorted(stages.items())[10:] if all(k in v for k in ('src','enc_in','enc_out'))]
    result=dict(poc_type=poc,videorate=rate,frames=len(frames),
                source_to_encoder_ms=stats([(v['enc_in']-v['src'])/1e6 for v in valid]),
                encoder_ms=stats([(v['enc_out']-v['enc_in'])/1e6 for v in valid]),
                source_to_encoded_ms=stats([(v['enc_out']-v['src'])/1e6 for v in valid]))
    if rate:result['source_to_rate_ms']=stats([(v['rate']-v['src'])/1e6 for v in valid if 'rate' in v])
    if rate:
        nals=[n for f in frames[:3] for n in re.split(b'\x00\x00\x00?\x01',base64.b64decode(f['payload'])) if n]
        sps=next(n for n in nals if n[0]&31==7);pps=next(n for n in nals if n[0]&31==8)
        fixture=dict(width=640,height=360,framerate=fps,codec='avc1.'+sps[1:4].hex(),
                     sps=base64.b64encode(sps).decode(),pps=base64.b64encode(pps).decode(),frames=frames)
        (out/'fixtures'/f'{tag or "poc"+str(poc)}.json').write_text(json.dumps(fixture))
    return result


if __name__=='__main__':
    results=[]
    for poc,rate in [(0,True),(0,False),(2,True)]:
        r=run(poc,rate);results.append(r);print(json.dumps(r),flush=True)
    (out/'encoder-stages.json').write_text(json.dumps(results,indent=2)+'\n')
