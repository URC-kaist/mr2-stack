import json
import os
from pathlib import Path
import socket
import struct
import subprocess
import sys
import threading
import time

root=Path('/home/mr2/mr2-stack')
sys.path.insert(0,str(root/'rover/ros2_ws/src/mr2_latency_diagnostics'))
from mr2_latency_diagnostics.video_timing import VideoTimingCollector
out=Path('/tmp/mr2-steady-latency-review')


def stats(values):
    v=sorted(values)
    return dict(n=len(v),p50_ms=v[(len(v)-1)//2],p95_ms=v[int((len(v)-1)*.95)],max_ms=v[-1]) if v else None


def run(pattern,wire):
    name=f'{pattern}-{wire}'
    received={};stopping=threading.Event()
    udp=socket.socket(socket.AF_INET,socket.SOCK_DGRAM)
    udp.setsockopt(socket.SOL_SOCKET,socket.SO_RCVBUF,1048576)
    udp.bind(('127.0.0.1',19222));udp.settimeout(.05)
    def read():
        while not stopping.is_set():
            try:packet,_=udp.recvfrom(65535)
            except socket.timeout:continue
            epoch=time.time_ns()//1000
            if len(packet)>=36 and packet[1]&128:
                seq,rtpts,ssrc=struct.unpack_from('!HII',packet,2)
                # Native sender's ID 7, two-byte 16-byte extension.
                offset=12+(packet[0]&15)*4
                if packet[0]&16 and packet[offset+4]==7 and packet[offset+5]==16:
                    mono,send_epoch=struct.unpack_from('!QQ',packet,offset+6)
                    received[(ssrc,rtpts,seq)]=dict(arrival_epoch_us=epoch,send_epoch_us=send_epoch)
    thread=threading.Thread(target=read);thread.start()
    control,parent=socket.socketpair(socket.AF_UNIX,socket.SOCK_DGRAM)
    address='mr2-steady-probe-'+str(os.getpid())
    args=[str(root/'rover/ros2_ws/install/mr2_video_streaming/lib/mr2_video_streaming/video_sender'),name,
          'videotestsrc','name=src','is-live=true','num-buffers=96','pattern='+pattern,'!',
          'video/x-raw,width=640,height=360,framerate=30/1','!',
          'queue','leaky=downstream','max-size-buffers=1','max-size-bytes=0','max-size-time=0','!',
          'videorate','drop-only=true','!','video/x-raw,framerate=30/1','!',
          'nvvidconv','!','video/x-raw(memory:NVMM),format=NV12','!',
          'nvv4l2h264enc','name=latency_encoder','copy-timestamp=true','bitrate=1800000','vbv-size=180000',
          'iframeinterval=30','idrinterval=30','insert-sps-pps=true','maxperf-enable=true','control-rate=1','!',
          'video/x-h264,stream-format=byte-stream,alignment=au,level=(string)3.1','!',
          'h264parse','config-interval=1','!','rtph264pay','name=latency_pay','pt=96','mtu=1176','config-interval=1','!',
          'udpsink','name=video_udp','host=127.0.0.1','port=19222','buffer-size=65536','sync=false','async=false']
    try:
        with VideoTimingCollector([name],address='\0'+address) as collector:
            with (out/(name+'.log')).open('w') as log:
                worker=subprocess.Popen(args,env={**os.environ,'MR2_VIDEO_TIMING_SOCKET':address,'MR2_VIDEO_CONTROL_FD':str(parent.fileno())},pass_fds=(parent.fileno(),),stdout=log,stderr=subprocess.STDOUT)
                control.send(struct.pack('II',1800,wire))
                try:worker.wait(timeout=15)
                finally:
                    if worker.poll() is None:worker.terminate();worker.wait(timeout=5)
                if worker.returncode:raise RuntimeError('Native sender failed: '+name)
                time.sleep(.15)
        joined=[]
        for row in collector.samples:
            packet=received.get((row['ssrc'],row['rtp_timestamp'],row['marker_sequence']))
            if packet:
                joined.append(dict(encode_ms=row['encode_us']/1000,
                    source_to_last_send_ms=(packet['send_epoch_us']-row['ingest_epoch_us'])/1000,
                    source_to_last_arrival_ms=(packet['arrival_epoch_us']-row['ingest_epoch_us'])/1000,
                    loopback_link_ms=(packet['arrival_epoch_us']-packet['send_epoch_us'])/1000))
        valid=joined[10:]
        return dict(pattern=pattern,encoder_target_kbps=1800,pacing_kbps=wire,joined_frames=len(joined),
                    statistics={k:stats([r[k] for r in valid]) for k in valid[0]},samples=joined)
    finally:
        stopping.set();thread.join();udp.close();control.close();parent.close()


rows=[]
for pattern,wire in [('ball',2100),('snow',2100),('snow',8400),('snow',2100)]:
    r=run(pattern,wire);rows.append(r);print(json.dumps({k:v for k,v in r.items() if k!='samples'}),flush=True)
(out/'pacer-results.json').write_text(json.dumps(rows,indent=2)+'\n')
