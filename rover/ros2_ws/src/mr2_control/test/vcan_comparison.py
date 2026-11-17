#!/usr/bin/env python3
"""ROS command boundary -> real drivers -> unchanged CanBusManager -> vcan0.
Run inside a private network namespace with vcan0 and lo up. Baseline packages
must be built from git HEAD into --baseline; current overlay must be sourced.
Fake arm arrays represent Servo output; this does not exercise XBee serial/RF.
"""
import argparse
import json
import os
from pathlib import Path
import signal
import socket
import struct
import subprocess
import threading
import time
import xml.etree.ElementTree as ET

import rclpy
import xacro
import yaml
from ament_index_python.packages import get_package_share_directory, get_package_prefix
from geometry_msgs.msg import Twist
from std_msgs.msg import Float64MultiArray
from std_srvs.srv import SetBool
from mr2_action_interface.msg import MissionStatus


class Firmware:
    def __init__(self, xml):
        self.devices = []
        for joint in ET.fromstring(xml).find('ros2_control').findall('joint'):
            p = {e.attrib['name']: e.text for e in joint.findall('param')}
            self.devices.append(p)
        self.sock = socket.socket(socket.PF_CAN, socket.SOCK_RAW, socket.CAN_RAW)
        self.sock.bind(('vcan0',))
        self.sock.settimeout(.002)
        self.frames = []
        self.armed = {}
        self.running = True
        self.thread = threading.Thread(target=self.loop)
        self.thread.start()

    def send(self, ident, data):
        self.sock.send(struct.pack('=IB3x8s', ident, len(data), data))

    def loop(self):
        next_feedback = 0
        while self.running:
            if time.monotonic() >= next_feedback:
                for p in self.devices:
                    if 'motor_id' in p:
                        self.send(0x80002900 + int(p['motor_id']), bytes(8))
                        continue
                    n = int(p['node_id'])
                    profile = {'VelocityOnly': 0, 'As5600': 1, 'TmagLut': 2, 'DirectInput': 3}[p['desired_profile']]
                    mode = 2 if p['command_channel'] == 'velocity' else 1
                    self.send(0x400+n, struct.pack('<i', 0))
                    self.send(0x410+n, struct.pack('<i', 0))
                    self.send(0x420+n, struct.pack('<ii', round(float(p.get('expected_output_min_deg', '-180'))*1000), round(float(p.get('expected_output_max_deg', '180'))*1000)))
                    self.send(0x430+n, struct.pack('<iBBBB', 1000, profile, mode, 3, 0))
                    self.send(0x5f0+n, bytes([0xfb, profile, profile, mode, 15, 0, 0, (1 if profile in (1,2) else 0) | (2 if self.armed.get(n) else 0)]))
                next_feedback = time.monotonic() + .01
            try:
                packet = self.sock.recv(72)
            except socket.timeout:
                continue
            ident, length, data = struct.unpack('=IB3x8s', packet)
            self.frames.append((time.monotonic(), ident, data[:length].hex()))
            if 0x230 <= ident < 0x250:
                self.armed[ident-0x230] = bool(data[0])

    def close(self):
        self.running = False
        self.thread.join()
        self.sock.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--baseline', default='/tmp/mr2-baseline-install')
    parser.add_argument('--output', default='/tmp/mr2-vcan-results')
    args = parser.parse_args()
    # Refuse any interface that is not the requested virtual CAN device.
    links = json.loads(subprocess.check_output(['ip', '-details', '-json', 'link', 'show', 'vcan0']))
    assert links[0]['linkinfo']['info_kind'] == 'vcan'
    out = Path(args.output); out.mkdir(parents=True, exist_ok=True)
    share = Path(get_package_share_directory('mr2_rover_description'))
    xml = xacro.process_file(str(share/'urdf/rover.urdf.xacro'), mappings={'ros2_control_mode':'real_hardware', 'can_iface':'vcan0', 'enable_manipulator_module':'true'}).toxml()
    config_path = share/'config/controllers/rover_controllers.yaml'
    rclpy.init()
    node = rclpy.create_node('fake_xbee_commands')
    manual = node.create_publisher(Twist, '/base/cmd_vel', 10)
    auto = node.create_publisher(Twist, '/cmd_vel', 10)
    arm = node.create_publisher(Float64MultiArray, '/manipulator_controller/commands', 10)
    gripper = node.create_publisher(Float64MultiArray, '/gripper_controller/commands', 10)
    mission = node.create_publisher(MissionStatus, '/mission_status', 10)
    results = {}
    def pump(seconds, send=None):
        end = time.monotonic()+seconds
        while time.monotonic() < end:
            if send: send()
            rclpy.spin_once(node, timeout_sec=.02)
            assert process.poll() is None, logpath.read_text()[-6000:]
    try:
        for backend in ('legacy', 'mr2_control'):
            fw = Firmware(xml)
            logpath = out/(backend+'.log')
            params = {'robot_description':xml}
            if backend == 'legacy':
                params_path = out/'legacy-params.yaml'
                params_path.write_text(yaml.safe_dump({'controller_manager':{'ros__parameters':params}}))
                # Start in a clean environment so plugin resolution is baseline-only.
                command = ['bash', '-c', 'source /opt/ros/humble/setup.bash\nsource "$1/setup.bash"\nexec /opt/ros/humble/lib/controller_manager/ros2_control_node --ros-args --params-file "$2" --params-file "$3"', '_', args.baseline, str(params_path), str(config_path)]
                env = {k:v for k,v in os.environ.items() if k not in ('AMENT_PREFIX_PATH','CMAKE_PREFIX_PATH','COLCON_PREFIX_PATH','LD_LIBRARY_PATH','PYTHONPATH')}
            else:
                params.update(controller_config=str(config_path), start_manipulator_controllers_active=True)
                params_path = out/'new-params.yaml'
                params_path.write_text(yaml.safe_dump({'mr2_control':{'ros__parameters':params}}))
                command = [str(Path(get_package_prefix('mr2_control'))/'lib/mr2_control/mr2_control_node'), '--ros-args', '--params-file', str(params_path)]
                env = os.environ.copy()
            with logpath.open('w') as log:
                process = subprocess.Popen(command, env=env, stdout=log, stderr=subprocess.STDOUT)
                try:
                    if backend == 'legacy':
                        spawn = subprocess.run(['/opt/ros/humble/lib/controller_manager/spawner', 'rover_controller','manipulator_controller','gripper_controller','--controller-manager-timeout','30'], stdout=log, stderr=subprocess.STDOUT, timeout=45)
                        assert spawn.returncode == 0, logpath.read_text()[-6000:]
                    pump(2)
                    samples = {}
                    scenarios = [('idle',0,0,0), ('forward',.3,0,0), ('reverse',-.2,0,0), ('strafe',0,.2,0), ('turn',.2,0,.2), ('saturation',2.,.3,.5), ('autonomous',.25,-.1,.1)]
                    for index, (name,x,y,z) in enumerate(scenarios):
                        twist = Twist(); twist.linear.x=float(x); twist.linear.y=float(y); twist.angular.z=float(z)
                        armmsg = Float64MultiArray(data=[.1*index,.1,-.4,.2,1.,-.1])
                        gripmsg = Float64MultiArray(data=[.1*index])
                        def send():
                            mission.publish(MissionStatus(state=1 if name == "autonomous" else 0))
                            (auto if name == "autonomous" else manual).publish(twist); arm.publish(armmsg); gripper.publish(gripmsg)
                        pump(2.5,send)
                        start = time.monotonic(); pump(.25,send)
                        frames = [f for f in fw.frames if f[0]>=start and (0x200<=f[1]<0x220 or 0x80000400<=f[1]<0x80000500)]
                        values = {}
                        for _,ident,data in frames:
                            values.setdefault(hex(ident),set()).add(data)
                        assert len(values)==15, (backend,name,values)
                        assert all(len(v)==1 for v in values.values()), ('unsettled',backend,name,values)
                        samples[name] = {k:next(iter(v)) for k,v in values.items()}
                    # Wrong-length arrays must leave previous arm/gripper targets intact.
                    arm.publish(Float64MultiArray(data=[.1]))
                    gripper.publish(Float64MultiArray(data=[.1,.2]))
                    pump(.8)
                    start=time.monotonic(); pump(.2)
                    samples['timeout']={hex(i):d for t,i,d in fw.frames if t>=start and (0x200<=i<0x220 or 0x80000400<=i<0x80000500)}
                    for ident,data in samples['autonomous'].items():
                        if int(ident,16) >= 0x80000000 or int(ident,16)==0x209:
                            assert samples['timeout'][ident] == data, ('position hold',backend,ident)
                    assert len(samples['timeout'])==15
                    for ident,data in samples['timeout'].items():
                        if 0x201<=int(ident,16)<=0x208 or 0x215<=int(ident,16)<=0x218:
                            assert data=='00000000', ('timeout nonzero',backend,ident,data)
                    results[backend]=samples
                    (out/(backend+'-samples.json')).write_text(json.dumps(samples,indent=2))
                finally:
                    shutdown_start = time.monotonic()
                    process.send_signal(signal.SIGINT)
                    try: process.wait(timeout=8)
                    except subprocess.TimeoutExpired:
                        process.kill(); process.wait()
                    time.sleep(.1)
                    fw.close()
                    (out/(backend+'-frames.json')).write_text(json.dumps(fw.frames))
                    if backend == 'mr2_control':
                        terminal = [(i,d) for t,i,d in fw.frames if t>=shutdown_start]
                        for p in fw.devices:
                            if 'node_id' not in p: continue
                            ident = int(p['node_id'])
                            assert (0x230+ident, '00') in terminal, ('missing disarm',ident)
                            disarm_index = terminal.index((0x230+ident, '00'))
                            assert not any(i in (0x200+ident,0x210+ident) for i,d in terminal[disarm_index+1:]), ('target after disarm',ident)
                            if p['command_channel']=='velocity':
                                assert (0x210+ident,'00000000') in terminal, ('missing stop',ident)
                        assert process.returncode == 0, (process.returncode, logpath.read_text()[-3000:])
                        assert 'Shutdown CAN write failed' not in logpath.read_text()
            print(backend, 'captured',len(fw.frames),'frames', flush=True)
        differences = {case:{ident:[data,results['mr2_control'][case].get(ident)] for ident,data in values.items() if data!=results['mr2_control'][case].get(ident)} for case,values in results['legacy'].items()}
        (out/'comparison.json').write_text(json.dumps({'samples':results,'differences':differences},indent=2))
        assert not any(differences.values()), differences
        print('PASS: 8 scenarios, all 15 motor CAN IDs and payloads match baseline HEAD')
    finally:
        node.destroy_node(); rclpy.shutdown()

if __name__ == '__main__': main()
