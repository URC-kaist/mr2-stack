import gi,json,struct,time
from pathlib import Path
gi.require_version('Gst','1.0')
from gi.repository import Gst
Gst.init(None)
out=Path('/tmp/mr2-steady-latency-review')


def run(latency):
    entries={};values=[];sent={};frame_at_pts={};full=[];au=[]
    r=Gst.parse_launch(f'udpsrc name=src port=19223 caps="application/x-rtp,media=video,encoding-name=H264,payload=96,clock-rate=90000" ! rtpjitterbuffer name=jb latency={latency} drop-on-latency=true ! rtph264depay ! h264parse name=parse disable-passthrough=true ! video/x-h264,stream-format=byte-stream,alignment=au ! fakesink sync=false async=false')
    s=Gst.parse_launch('videotestsrc is-live=true num-buffers=96 pattern=snow ! video/x-raw,width=640,height=360,framerate=30/1 ! nvvidconv ! video/x-raw(memory:NVMM),format=NV12 ! nvv4l2h264enc bitrate=1800000 vbv-size=180000 iframeinterval=30 idrinterval=30 insert-sps-pps=true maxperf-enable=true ! rtph264pay name=pay pt=96 mtu=1176 ! udpsink host=127.0.0.1 port=19223 sync=false async=false')
    def callback(stage):
        def probe(pad,info):
            b=info.get_buffer()
            if b:
                head=b.extract_dup(0,12)
                if len(head)>=12 and head[1]&128:
                    seq,rtp,ssrc=struct.unpack_from('!HII',head,2)
                    key=(ssrc,rtp,seq);now=time.monotonic_ns()
                    if stage==0:entries[key]=now
                    elif key in entries:
                        values.append((now-entries.pop(key))/1e6)
                        if key in sent:
                            full.append((now-sent[key])/1e6)
                            frame_at_pts[int(b.pts)]=sent[key]
            return Gst.PadProbeReturn.OK
        return probe
    r.get_by_name('src').get_static_pad('src').add_probe(Gst.PadProbeType.BUFFER,callback(0))
    r.get_by_name('jb').get_static_pad('src').add_probe(Gst.PadProbeType.BUFFER,callback(1))
    def before_send(pad,info):
        bl=info.get_buffer_list() if info.type&Gst.PadProbeType.BUFFER_LIST else None
        buffers=[bl.get(i) for i in range(bl.length())] if bl else [info.get_buffer()]
        for b in buffers:
            if b:
                head=b.extract_dup(0,12)
                if len(head)>=12 and head[1]&128:
                    seq,rtp,ssrc=struct.unpack_from('!HII',head,2);sent[(ssrc,rtp,seq)]=time.monotonic_ns()
        return Gst.PadProbeReturn.OK
    def after_parse(pad,info):
        b=info.get_buffer()
        if b and int(b.pts) in frame_at_pts:au.append((time.monotonic_ns()-frame_at_pts[int(b.pts)])/1e6)
        return Gst.PadProbeReturn.OK
    s.get_by_name('pay').get_static_pad('src').add_probe(Gst.PadProbeType.BUFFER|Gst.PadProbeType.BUFFER_LIST,before_send)
    r.get_by_name('parse').get_static_pad('src').add_probe(Gst.PadProbeType.BUFFER,after_parse)
    try:
        r.set_state(Gst.State.PLAYING);time.sleep(.15);s.set_state(Gst.State.PLAYING)
        msg=s.get_bus().timed_pop_filtered(12*Gst.SECOND,Gst.MessageType.EOS|Gst.MessageType.ERROR)
        if not msg:raise RuntimeError('Sender timeout')
        if msg.type==Gst.MessageType.ERROR:raise RuntimeError(str(msg.parse_error()))
        time.sleep(.2)
        err=r.get_bus().pop_filtered(Gst.MessageType.ERROR)
        if err:raise RuntimeError(str(err.parse_error()))
    finally:s.set_state(Gst.State.NULL);r.set_state(Gst.State.NULL)
    v=sorted(values[10:])
    def stats(v):
        v=sorted(v[10:]);return dict(n=len(v),p50=v[(len(v)-1)//2],p95=v[int((len(v)-1)*.95)],max=v[-1]) if v else None
    return dict(configured_latency_ms=latency,matched_markers=len(values),steady_n=len(v),
                jitter_residence_ms=stats(values),sender_pay_to_post_jitter_ms=stats(full),sender_pay_to_access_unit_ms=stats(au))


results=[]
for latency in [40,20,10,40]:
    row=run(latency);results.append(row);print(json.dumps(row),flush=True)
(out/'jitter-results.json').write_text(json.dumps(results,indent=2)+'\n')
