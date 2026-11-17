import json, os, re, signal, socket, subprocess, sys, time
from pathlib import Path
cap = int(sys.argv[1]) if len(sys.argv) > 1 else 0
listing = subprocess.check_output(['ss', '-ltnp', 'sport = :8081']).decode()
pid = int(re.search(r'pid=(\d+)', listing)[1])
proc = Path('/proc') / str(pid)
args = [s.decode() for s in (proc / 'cmdline').read_bytes().split(b'\0')[:-1]]
assert 'index.js' in args
env = dict(s.decode().split('=', 1) for s in (proc / 'environ').read_bytes().split(b'\0') if b'=' in s)
cwd = (proc / 'cwd').resolve()
if args[-1] == '--drone-rocket': args.append('')
clean = []
i = 0
while i < len(args):
    if args[i] in ['--video-max-kbps', '--video-initial-kbps']:
        i += 2; continue
    if args[i].startswith(('--video-max-kbps=', '--video-initial-kbps=')):
        i += 1; continue
    clean.append(args[i]); i += 1
if cap: clean += ['--video-max-kbps', str(cap), '--video-initial-kbps', str(min(3000, cap))]
os.kill(pid, signal.SIGINT)
for _ in range(150):
    if not proc.exists() or (proc / 'stat').read_text().split()[2] == 'Z': break
    time.sleep(.1)
else: raise RuntimeError('Old gateway did not stop')
logpath = f'/tmp/mr2-adaptive-gateway-{cap}.log'
with open(logpath, 'a') as log:
    child = subprocess.Popen(clean, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
        stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
for _ in range(150):
    if child.poll() is not None: raise RuntimeError('New gateway exited; inspect ' + logpath)
    try:
        with socket.create_connection(('127.0.0.1', 8081), timeout=.1): break
    except OSError: time.sleep(.1)
else: raise RuntimeError('Gateway did not listen')
print(json.dumps({'pid': child.pid, 'cap_kbps': cap, 'args': clean[1:], 'log': logpath}))
