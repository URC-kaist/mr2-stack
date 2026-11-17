import json, os, signal, socket, subprocess, sys, tempfile, time
from pathlib import Path
out=Path('/tmp/mr2-offline-audit')
assert os.readlink('/proc/self/ns/net') != sys.argv[1]
assert [i['ifname'] for i in json.loads(subprocess.check_output(['ip','-j','link','show'],text=True))]==['lo']
subprocess.run(['ip','link','set','lo','up'],check=True)
assert json.loads(subprocess.check_output(['ip','-j','route','show'],text=True))==[]
with tempfile.TemporaryDirectory(prefix='mr2-offline-cold-') as tmp:
 p=Path(tmp)
 with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as s:s.bind(('127.0.0.1',0));port=s.getsockname()[1]
 text=f'local stratum 8\npool ntp.ubuntu.com iburst maxsources 4\npool pool.ntp.org iburst maxsources 4\nmakestep 0.1 -1\nallow 127.0.0.1\nbindaddress 127.0.0.1\nport {port}\ncmdport 0\npidfile {p}/server.pid\n'
 (p/'server.conf').write_text(text)
 with (out/'cold-boot-ntp-server.log').open('w') as log:
  started=time.monotonic()
  server=subprocess.Popen(['chronyd','-x','-U','-n','-u','root','-f',str(p/'server.conf')],stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
  try:
   time.sleep(.5);assert server.poll() is None
   client=subprocess.run(['chronyd','-Q','-U','-u','root','-t','8',f'server 127.0.0.1 port {port} iburst maxsamples 1','port 0','cmdport 0',f'pidfile {p}/client.pid'],capture_output=True,text=True,timeout=12)
   report={'internet_available':False,'server_config':text,'server_clock_control_disabled':True,'client_measure_only':True,'elapsed_s':time.monotonic()-started,'exit_code':client.returncode,'output':client.stdout+client.stderr}
   assert client.returncode==0,report
   report['passed']=True;(out/'cold-boot-ntp.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report))
  finally:
   os.killpg(server.pid,signal.SIGTERM)
   try:server.wait(timeout=2)
   except subprocess.TimeoutExpired:os.killpg(server.pid,signal.SIGKILL);server.wait()
