import gi,json,time,statistics,subprocess
from pathlib import Path
gi.require_version('Gst','1.0')
from gi.repository import Gst
Gst.init(None)
devices=['FL','FR','LEFT','RIGHT']
pipes=[];stats={}
for dev in devices:
 stats[dev]={'arrivals':[],'pts':[],'bytes':0,'errors':[]}
 p=Gst.parse_launch(f'v4l2src name=src device=/dev/video{dev} do-timestamp=true ! image/jpeg,width=640,height=360,framerate=30/1 ! fakesink sync=false async=false')
 def callback(pad,info,name=dev):
  b=info.get_buffer();s=stats[name];s['arrivals'].append(time.monotonic());s['pts'].append(b.pts);s['bytes']+=b.get_size();return Gst.PadProbeReturn.OK
 p.get_by_name('src').get_static_pad('src').add_probe(Gst.PadProbeType.BUFFER,callback)
 pipes.append(p)
started=time.monotonic()
try:
 for p in pipes:p.set_state(Gst.State.PLAYING)
 while time.monotonic()-started<12:
  for dev,p in zip(devices,pipes):
   m=p.get_bus().pop_filtered(Gst.MessageType.ERROR)
   if m: stats[dev]['errors'].append(str(m.parse_error()[0]))
  time.sleep(.05)
finally:
 for p in pipes:p.set_state(Gst.State.NULL)
report={}
for dev,s in stats.items():
 ts=[t for t in s['arrivals'] if t>started+2]
 diffs=sorted((b-a)*1000 for a,b in zip(ts,ts[1:]))
 report[dev]={'capture_fps':(len(ts)-1)/(ts[-1]-ts[0]) if len(ts)>1 else 0,'frames':len(ts),'median_interval_ms':statistics.median(diffs) if diffs else None,'p95_interval_ms':diffs[int(len(diffs)*.95)] if diffs else None,'errors':s['errors']}
print(json.dumps(report,indent=2));Path('/tmp/mr2-fps-audit/capture.json').write_text(json.dumps(report,indent=2))
