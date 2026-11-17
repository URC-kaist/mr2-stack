#!/usr/bin/env python3
"""Production JS controller/native receiver with a fake XBee/UDP radio, 9 s.

This test changes no network configuration and opens no real serial/camera FD.
"""
import argparse
import base64
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

ROOT = Path(__file__).resolve().parents[2]


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bin',required=True,type=Path)
    args=parser.parse_args();Gst.init(None)
    udp=socket.socket(socket.AF_INET,socket.SOCK_DGRAM);udp.bind(('127.0.0.1',0))
    unused=socket.socket(socket.AF_INET,socket.SOCK_DGRAM);unused.bind(('127.0.0.1',0))
    port=unused.getsockname()[1];unused.close()
    frames=[];policies=[];feedback=[];sent={};pending=[];probes=[];decoded=[];last_probe=0
    decode=Gst.parse_launch('appsrc name=in is-live=true do-timestamp=true format=time '
        'caps=video/x-h264,stream-format=byte-stream,alignment=au ! h264parse ! openh264dec '
        '! appsink name=out emit-signals=true sync=false async=false')
    def on_frame(sink):sink.emit('pull-sample');decoded.append(time.monotonic());return Gst.FlowReturn.OK
    decode.get_by_name('out').connect('new-sample',on_frame);decode.set_state(Gst.State.PLAYING)
    with tempfile.TemporaryFile() as errors:
        base=subprocess.Popen(['node',str(ROOT/'scripts/latency/freshness_base_fixture.cjs'),
            str((args.bin/'video_receiver').resolve()),str(port)],stdout=subprocess.PIPE,stderr=errors,text=True)
        sender=None;start=time.monotonic()
        try:
            sender=subprocess.Popen([str(args.bin/'video_sender'),'base_fixture','videotestsrc','name=src',
                'is-live=true','num-buffers=270','pattern=ball','!',
                'video/x-raw,format=I420,width=320,height=180,framerate=30/1','!',
                'x264enc','name=latency_encoder','bitrate=256','speed-preset=ultrafast','tune=zerolatency',
                'bframes=0','key-int-max=1','!','h264parse','!','rtph264pay','name=latency_pay','config-interval=1',
                '!','udpsink','name=video_udp','host=127.0.0.1',f'port={udp.getsockname()[1]}','sync=false','async=false'],
                stdout=errors,stderr=errors)
            while time.monotonic()-start<10:
                now=time.monotonic();elapsed=now-start
                delay=1.2 if elapsed<1.5 or 4<=elapsed<7 else 0
                readable=select.select([udp,base.stdout],[],[],.002)[0]
                if udp in readable:
                    packet=udp.recv(65536)
                    if len(packet)>34:
                        key=(int.from_bytes(packet[8:12],'big'),int.from_bytes(packet[4:8],'big'),int.from_bytes(packet[2:4],'big'))
                        sent[key]=int.from_bytes(packet[18:26],'big')
                    pending.append((now+delay,packet))
                if base.stdout in readable:
                    line=base.stdout.readline();assert line,'base fixture exited'
                    message=json.loads(line)
                    if message['type']=='feedback':
                        feedback.append(message)
                        if message['probe_id'] and message['probe_id']!=last_probe:
                            last_probe=message['probe_id'];probes.append((now+.02,message['probe_token']))
                    elif message['type']=='policy':policies.append(message)
                    else:
                        message.pop('type');payload=base64.b64decode(message.pop('payload'))
                        message['at_s']=elapsed;frames.append(message)
                        b=Gst.Buffer.new_allocate(None,len(payload),None);b.fill(0,payload)
                        decode.get_by_name('in').emit('push-buffer',b)
                while probes and probes[0][0]<=now:
                    _,token=probes.pop(0);packet=bytearray(72)
                    packet[0]=0x90;packet[1]=127
                    struct.pack_into('!IIHHH',packet,4,token,0x4d523250,0x1000,5,0x0710)
                    struct.pack_into('!QQ',packet,18,int(now*1e6),time.time_ns()//1000)
                    pending.append((now+delay,bytes(packet)))
                pending.sort(key=lambda p:p[0])
                while pending and pending[0][0]<=now:
                    _,packet=pending.pop(0);udp.sendto(packet,('127.0.0.1',port))
                assert base.poll() is None,'base fixture exited'
            ages=[]
            for f in frames:
                c=f.get('correlation')
                if c:
                    key=(c['ssrc'],c['rtp_timestamp'],c['marker_sequence'])
                    if key in sent:ages.append((f['at_us']-sent[key])/1000)
            assert feedback and feedback[0]['wire_kbps']==0,feedback[:3]
            assert any(m['wire_kbps']==32 for m in feedback), 'bootstrap did not reopen at the safe minimum'
            assert frames and min(f['at_s'] for f in frames)>=1.5,'old startup video was replayed'
            assert any(f['at_s']>7.5 for f in frames),'fresh video did not recover'
            assert len(ages)>=10 and max(ages)<550,(len(ages),max(ages) if ages else None)
            assert len(decoded)>=20,len(decoded)
            assert any(m['paused'] for m in policies[1:]),'later stale-link pause was not exercised'
            print(json.dumps({'passed':True,'frames':len(frames),'decoded':len(decoded),'matched_ages':len(ages),
                'max_send_to_base_output_ms':max(ages),'first_frame_s':min(f['at_s'] for f in frames),
                'policy_changes':policies}),flush=True)
        except Exception:
            errors.seek(0);print(errors.read().decode(errors='replace'));raise
        finally:
            for p in (sender,base):
                if p and p.poll() is None:
                    p.send_signal(signal.SIGTERM)
                    try:p.wait(timeout=5)
                    except subprocess.TimeoutExpired:p.kill();p.wait()
            decode.set_state(Gst.State.NULL);udp.close()


if __name__=='__main__':main()
