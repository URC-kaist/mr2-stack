import json,os,signal,ssl,subprocess,time,urllib.request
from pathlib import Path
out=Path('/tmp/mr2-clock-stability/live')
ssh=['ssh','-o','BatchMode=yes','-o','ConnectTimeout=5','-o','HostKeyAlias=192.168.0.54','mr2@192.168.0.55']
base=None;rover=None;observer=None
try:
    base=subprocess.Popen(ssh+['python3 /tmp/mr2-stable-base-runner.py'],stdout=(out/'base-ssh.log').open('w'),stderr=subprocess.STDOUT,start_new_session=True)
    start=time.monotonic()
    while True:
        try:
            with urllib.request.urlopen('https://192.168.0.55/video/streams',context=ssl._create_unverified_context(),timeout=2) as r:
                assert r.status==200;break
        except Exception:
            if base.poll() is not None or time.monotonic()-start>25:raise RuntimeError('Base startup failed')
            time.sleep(.5)
    print('Base ready',flush=True)
    command='source /opt/ros/humble/setup.bash\nsource /home/mr2/mr2-stack/rover/ros2_ws/install/setup.bash\nexec ros2 launch mr2_launch rover_real.launch.py enable_manipulator_module:=false enable_xbee_sim:=false video_base_host:=192.168.0.55 enable_rover_hardware:=false enable_gnss:=false enable_localization:=false enable_led:=false enable_camera_turret:=false'
    rover=subprocess.Popen(['bash','-c',command],cwd='/home/mr2/mr2-stack/rover/ros2_ws',stdout=(out/'rover.log').open('w'),stderr=subprocess.STDOUT,start_new_session=True)
    (out/'processes.json').write_text(json.dumps({'base_ssh_pid':base.pid,'rover_pid':rover.pid,'rover_command':command}))
    start=time.monotonic()
    while True:
        text=(out/'rover.log').read_text()
        if 'Started V4L2 video stream' in text:break
        if rover.poll() is not None or time.monotonic()-start>115:raise RuntimeError('Rover video startup failed')
        time.sleep(.5)
    print('Rover video ready; starting 300-second observation',flush=True)
    observer=subprocess.Popen(ssh+['bash /tmp/mr2-clock-stability/live/observe.sh'],stdout=(out/'observer.log').open('w'),stderr=subprocess.STDOUT)
    observer.wait(timeout=425)
    if observer.returncode:raise RuntimeError('Observer failed; see observer.log')
    print('300-second observation finished',flush=True)
finally:
    if observer and observer.poll() is None:observer.terminate();observer.wait(timeout=5)
    if rover and rover.poll() is None:
        os.killpg(rover.pid,signal.SIGINT)
        try:rover.wait(timeout=20)
        except subprocess.TimeoutExpired:os.killpg(rover.pid,signal.SIGTERM);rover.wait(timeout=10)
    if base:
        subprocess.run(ssh+['touch /tmp/mr2-clock-stability/live/stop'],timeout=10,check=False)
        try:base.wait(timeout=25)
        except subprocess.TimeoutExpired:os.killpg(base.pid,signal.SIGTERM)
    print('Test-launched rover/base processes stopped',flush=True)
