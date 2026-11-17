import json, os, pwd, socket, subprocess, sys, tempfile, time
from pathlib import Path
OUT=Path('/tmp/mr2-offline-audit')
report={'namespace':os.readlink('/proc/self/ns/net'),'host_namespace':sys.argv[1]}
assert report['namespace']!=report['host_namespace'], 'Refuse to change the host network'
interfaces=json.loads(subprocess.check_output(['ip','-j','link','show'],text=True))
assert [i['ifname'] for i in interfaces]==['lo'], interfaces
subprocess.run(['ip','link','set','lo','up'],check=True)
report['interfaces']=json.loads(subprocess.check_output(['ip','-j','-4','addr','show'],text=True))
report['routes']=json.loads(subprocess.check_output(['ip','-j','route','show'],text=True))
assert not report['routes']
with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as s:
 s.settimeout(.2)
 try: s.connect(('1.1.1.1',123));report['external_reachable']=True
 except OSError: report['external_reachable']=False
assert not report['external_reachable']
with tempfile.TemporaryDirectory(prefix='mr2-isolated-ntp-') as tmp:
 p=Path(tmp)
 with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as s:s.bind(('127.0.0.1',0));port=s.getsockname()[1]
 text=f'local stratum 8\nbindaddress 127.0.0.1\nallow 127.0.0.1\nport {port}\ncmdport 0\npidfile {p}/server.pid\n'
 (p/'server.conf').write_text(text)
 with (OUT/'namespace-ntp-server.log').open('w') as log:
  server=subprocess.Popen(['chronyd','-x','-U','-n','-u','root','-f',str(p/'server.conf')],stdout=log,stderr=subprocess.STDOUT)
  try:
   time.sleep(.5);assert server.poll() is None,(OUT/'namespace-ntp-server.log').read_text()
   client=subprocess.run(['chronyd','-Q','-U','-u','root','-t','8',f'server 127.0.0.1 port {port} iburst maxsamples 1','port 0','cmdport 0',f'pidfile {p}/client.pid'],capture_output=True,text=True,timeout=12)
   report['ntp']={'server_config':text,'returncode':client.returncode,'output':client.stdout+client.stderr}
   assert client.returncode==0,report['ntp']
  finally:server.terminate();server.wait(timeout=3)
commands=[
 ['python3','scripts/latency/test_adaptive_video.py','--bin','rover/ros2_ws/install/mr2_video_streaming/lib/mr2_video_streaming'],
 ['node','--test','base/gateway/test/video_control.test.js','base/gateway/test/video_network_scenarios.test.js','base/gateway/test/chrony_status.test.js','base/gateway/test/uplink_trial_manager.test.js','base/gateway/test/gateway_startup.test.js'],
]
report['checks']=[]
for n,cmd in enumerate(commands):
 result=subprocess.run(cmd,capture_output=True,text=True,timeout=40)
 (OUT/f'namespace-check-{n}.log').write_text(result.stdout+result.stderr)
 report['checks'].append({'command':cmd,'exit_code':result.returncode})
 assert result.returncode==0,result.stdout+result.stderr
report['passed']=True
(OUT/'namespace-runtime.json').write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps(report))
