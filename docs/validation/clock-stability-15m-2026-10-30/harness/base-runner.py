import json,os,signal,subprocess,time
from pathlib import Path
out=Path('/tmp/mr2-clock-stability/15m');out.mkdir(exist_ok=True)
stop=out/'stop';stop.unlink(missing_ok=True)
log=(out/'base.log').open('w')
cmd=['bash','/home/mr2/mr2-stack/scripts/start_chrony_base.sh','--rover-host','192.168.0.57','--antenna-enable','false']
p=subprocess.Popen(cmd,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
(out/'base-pid.json').write_text(json.dumps({'pid':p.pid,'started':time.time(),'cmd':cmd}))
try:
    start=time.monotonic()
    while p.poll() is None and time.monotonic()-start<1200 and not stop.exists():time.sleep(.25)
finally:
    if p.poll() is None:
        os.killpg(p.pid,signal.SIGINT)
        try:p.wait(timeout=15)
        except subprocess.TimeoutExpired:os.killpg(p.pid,signal.SIGTERM);p.wait(timeout=10)
    log.close()
    (out/'base-exit.json').write_text(json.dumps({'returncode':p.returncode,'stopped':time.time()}))
