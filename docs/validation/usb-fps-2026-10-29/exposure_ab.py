import subprocess,json,gi,time,statistics
from pathlib import Path
gi.require_version('Gst','1.0');from gi.repository import Gst
Gst.init(None)
devs=['FR','RIGHT']
def ctrl(dev,value=None):
 cmd=['v4l2-ctl','-d','/dev/video'+dev]
 cmd+=['--get-ctrl=exposure_dynamic_framerate'] if value is None else ['--set-ctrl=exposure_dynamic_framerate='+str(value)]
 return subprocess.check_output(cmd,text=True).strip()
original={d:int(ctrl(d).split(':')[1]) for d in devs}
report={'original':original,'phases':[]}
def measure(setting):
 for d in devs:ctrl(d,setting)
 times={d:[] for d in devs};pipes=[];errors={d:[] for d in devs}
 try:
  for d in devs:
   p=Gst.parse_launch(f'v4l2src name=src device=/dev/video{d} do-timestamp=true ! image/jpeg,width=640,height=360,framerate=30/1 ! fakesink sync=false async=false')
   def sample(pad,info,name=d):times[name].append(time.monotonic());return Gst.PadProbeReturn.OK
   p.get_by_name('src').get_static_pad('src').add_probe(Gst.PadProbeType.BUFFER,sample);pipes.append(p)
  start=time.monotonic()
  for p in pipes:p.set_state(Gst.State.PLAYING)
  while time.monotonic()-start<10:
   for d,p in zip(devs,pipes):
    m=p.get_bus().pop_filtered(Gst.MessageType.ERROR)
    if m:errors[d].append(str(m.parse_error()[0]))
   time.sleep(.05)
 finally:
  for p in pipes:p.set_state(Gst.State.NULL)
 result={}
 for d in devs:
  ts=[x for x in times[d] if x>start+2]
  gaps=[(b-a)*1000 for a,b in zip(ts,ts[1:])]
  result[d]={'fps':(len(ts)-1)/(ts[-1]-ts[0]) if len(ts)>1 else 0,'frames':len(ts),'median_interval_ms':statistics.median(gaps) if gaps else None,'errors':errors[d]}
 return {'exposure_dynamic_framerate':setting,'cameras':result}
try:
 for setting in [1,0,1]:
  phase=measure(setting);report['phases'].append(phase);print(json.dumps(phase),flush=True)
finally:
 for d,value in original.items():ctrl(d,value)
 report['restored']={d:ctrl(d) for d in devs}
 Path('/tmp/mr2-fps-audit/exposure-ab.json').write_text(json.dumps(report,indent=2))
