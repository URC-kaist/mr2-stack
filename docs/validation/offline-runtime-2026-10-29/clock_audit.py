import json, os, shlex, signal, subprocess, sys, time
from pathlib import Path

ROOT = Path('/home/mr2/mr2-stack')
OUT = Path('/tmp/mr2-offline-audit')
sys.path.insert(0, str(ROOT/'rover/ros2_ws/src/mr2_system_status'))
from mr2_system_status.chrony_status import read_chrony_status, require_measurement_clock

SNAP = '''import json, subprocess
from pathlib import Path
def run(cmd):
 r=subprocess.run(cmd,capture_output=True,text=True,timeout=8);return r.stdout
print(json.dumps({'policy':json.loads(Path('/run/mr2-clock/status.json').read_text()),
'config':Path('/etc/chrony/chrony.conf').read_text(),'tracking':run(['chronyc','-n','tracking']),
'sources':run(['chronyc','-n','sources']), 'routes':run(['ip','-j','route','show']),
'addresses':run(['ip','-j','-4','addr','show'])}))'''
def snap(remote=False):
 cmd=['python3','-c',SNAP]
 if remote: cmd=['ssh','-o','BatchMode=yes','-o','HostKeyAlias=192.168.0.54','mr2@192.168.0.55',shlex.join(cmd)]
 return json.loads(subprocess.check_output(cmd,text=True,timeout=12))

FIXTURE = '''import sys
from launch import LaunchService, LaunchDescription
from launch.actions import SetLaunchConfiguration, OpaqueFunction, ExecuteProcess
sys.path.insert(0,'/home/mr2/mr2-stack/rover/ros2_ws/src/mr2_launch')
from mr2_launch.clock_guard import acquire_clock
s=LaunchService()
s.include_launch_description(LaunchDescription([
SetLaunchConfiguration('video_base_host','192.168.0.55'),
OpaqueFunction(function=acquire_clock), ExecuteProcess(cmd=['sleep','180'])]))
sys.exit(s.run())'''

report={'before':{'rover':snap(),'base':snap(True)},'samples':[]}
fixture=None
try:
 with (OUT/'clock-fixture.log').open('w') as log:
  fixture=subprocess.Popen(['/usr/bin/python3','-c',FIXTURE],stdout=log,stderr=subprocess.STDOUT)
  started=time.monotonic();ready_since=None
  while time.monotonic()-started<160:
   if fixture.poll() is not None: raise RuntimeError('Clock launch fixture exited')
   r,b=snap(),snap(True); status=read_chrony_status()
   try: require_measurement_clock();ready=True;error=None
   except RuntimeError as e: ready=False;error=str(e)
   sample={'at_s':time.monotonic()-started,'rover':r,'base':b,'status':status,'measurement_ready':ready,'error':error}
   report['samples'].append(sample)
   if r['policy']['locked'] and b['policy']['locked']:
    for name,state in [('rover',r),('base',b)]:
     assert 'pool ' not in state['config'] and 'makestep' not in state['config'], (name,state)
    assert 'local stratum 8' in b['config']
   print(json.dumps({'at_s':round(sample['at_s'],1),'base_locked':b['policy']['locked'],'base_source':b['policy']['selected_source'],'rover_locked':r['policy']['locked'],'peer':r['policy']['base_peer'],'measurement_ready':ready,'offset_ms':status.get('system_time_offset_s',0)*1000,'error_bound_ms':(status.get('root_dispersion_s',0)+abs(status.get('root_delay_s',0))/2)*1000}),flush=True)
   if ready:
    ready_since=ready_since or time.monotonic()
    if time.monotonic()-ready_since>=40: break
   else: ready_since=None
   time.sleep(5)
  assert ready_since and time.monotonic()-ready_since>=40, 'Relative clock did not remain measurement-ready'
 report['passed']=True
except Exception as e:
 report['passed']=False;report['error']=str(e);raise
finally:
 if fixture and fixture.poll() is None:
  fixture.send_signal(signal.SIGINT)
  try: fixture.wait(timeout=12)
  except subprocess.TimeoutExpired: fixture.kill();fixture.wait()
 for _ in range(8):
  report['after']={'rover':snap(),'base':snap(True)}
  if not any(v['policy']['locked'] for v in report['after'].values()): break
  time.sleep(2)
 for role in ['rover','base']:
  assert report['before'][role]['routes']==report['after'][role]['routes'], 'Host routes changed'
  # Address lifetimes can change under DHCP. Compare only addresses/interfaces.
  def addresses(s):return [(i['ifname'],[(a['local'],a['prefixlen']) for a in i['addr_info']]) for i in json.loads(s['addresses'])]
  assert addresses(report['before'][role])==addresses(report['after'][role]), 'Host addresses changed'
 report['host_network_unchanged']=True
 (OUT/'clock-live.json').write_text(json.dumps(report,indent=2)+'\n')
