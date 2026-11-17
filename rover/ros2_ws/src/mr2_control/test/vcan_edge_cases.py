#!/usr/bin/env python3
"""Adversarial real-runtime tests on private vcan0; never physical CAN.
Uses the existing wire firmware fixture with feedback/diagnostic fault injection.
"""
import json
import math
import os
from pathlib import Path
import signal
import struct
import subprocess
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
from vcan_comparison import Firmware

WHEELS = set(range(0x215, 0x219))
AK = set(range(0x80000465, 0x8000046b))
POSITIONS = AK | {0x209}


class FaultFirmware(Firmware):
    def __init__(self, xml, *, absent=(), delay=0, fault=0, armed=False, no_ack=False, angle=0, ak_angle=0):
        self.absent = set(absent)
        self.start_at = time.monotonic() + delay
        self.fault = fault
        self.no_ack = no_ack
        self.initial_armed = armed
        self.angle = angle
        self.ak_angle = ak_angle
        super().__init__(xml)

    def send(self, ident, data):
        if time.monotonic() < self.start_at:
            return
        if ident & 0x80000000:
            n = ident & 0xff
        else:
            n = ident & 0xf
        if n in self.absent:
            return
        if ident & 0x80000000:
            data = struct.pack(">h", self.ak_angle) + bytes(6)
        if 0x5f0 <= ident < 0x600:
            packet = bytearray(data)
            packet[5] = self.fault
            if self.no_ack:
                packet[7] &= ~2
            elif n not in self.armed and self.initial_armed:
                packet[7] |= 2
            data = bytes(packet)
        if 0x401 <= ident <= 0x404:
            data = struct.pack('<i', self.angle)
        super().send(ident, data)


class Suite:
    def __init__(self):
        self.out = Path('/tmp/mr2-vcan-edge-results')
        self.out.mkdir(exist_ok=True)
        share = Path(get_package_share_directory('mr2_rover_description'))
        self.xml = xacro.process_file(str(share/'urdf/rover.urdf.xacro'), mappings={
            'ros2_control_mode': 'real_hardware', 'can_iface': 'vcan0',
            'enable_manipulator_module': 'true'}).toxml()
        self.config = str(share/'config/controllers/rover_controllers.yaml')
        self.exe = str(Path(get_package_prefix('mr2_control'))/'lib/mr2_control/mr2_control_node')
        self.node = rclpy.create_node('edge_case_fake_xbee')
        self.manual = self.node.create_publisher(Twist, '/base/cmd_vel', 10)
        self.autonomous = self.node.create_publisher(Twist, '/cmd_vel', 10)
        self.mission = self.node.create_publisher(MissionStatus, '/mission_status', 10)
        self.arm = self.node.create_publisher(Float64MultiArray, '/manipulator_controller/commands', 10)
        self.grip = self.node.create_publisher(Float64MultiArray, '/gripper_controller/commands', 10)
        self.results = []

    def start(self, name, active=False, timeout=1500, **faults):
        self.name = name
        root = ET.fromstring(self.xml)
        for joint in root.find('ros2_control').findall('joint'):
            if joint.find("param[@name='node_id']") is not None:
                ET.SubElement(joint, 'param', name='activation_timeout_ms').text = str(timeout)
                ET.SubElement(joint, 'param', name='activation_arm_attempts').text = '1'
        xml = ET.tostring(root, encoding='unicode')
        self.fw = FaultFirmware(xml, **faults)
        params = {'robot_description': xml, 'controller_config': self.config,
                  'start_manipulator_controllers_active': active}
        path = self.out/(name+'.yaml')
        path.write_text(yaml.safe_dump({'mr2_control': {'ros__parameters': params}}))
        self.logpath = self.out/(name+'.log')
        self.log = self.logpath.open('w')
        self.process = subprocess.Popen([self.exe, '--ros-args', '--params-file', str(path)], stdout=self.log, stderr=subprocess.STDOUT)

    def spin(self, seconds, send=None, alive=True):
        end = time.monotonic()+seconds
        while time.monotonic()<end:
            if send: send()
            rclpy.spin_once(self.node, timeout_sec=.01)
            if alive:
                assert self.process.poll() is None, self.logpath.read_text()[-3000:]

    def drive(self, value=.3):
        msg = Twist(); msg.linear.x = float(value)
        self.manual.publish(msg)

    def window(self, seconds=.15, send=None):
        start = time.monotonic()
        self.spin(seconds, send)
        return [(i,d) for t,i,d in self.fw.frames if t>=start]

    @staticmethod
    def values(frames, ids):
        return {i:d for i,d in frames if i in ids}

    def zeros(self, frames):
        selected = [(i,d) for i,d in frames if i in WHEELS]
        assert set(i for i,d in selected)==WHEELS, ('missing wheel frames', selected)
        assert all(d=='00000000' for i,d in selected), ('nonzero wheels', selected[:20])

    def activate(self, component, value):
        client = self.node.create_client(SetBool, '/mr2_control/set_'+component+'_active')
        assert client.wait_for_service(timeout_sec=3)
        future = client.call_async(SetBool.Request(data=value))
        rclpy.spin_until_future_complete(self.node, future, timeout_sec=3)
        assert future.done() and future.result().success
        self.node.destroy_client(client)
        self.spin(.1)

    def finish(self, expected=0):
        if self.process.poll() is None:
            self.process.send_signal(signal.SIGINT)
        try:
            code = self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.process.kill(); self.process.wait()
            raise AssertionError('shutdown hung')
        finally:
            self.fw.close(); self.log.close()
            (self.out/(self.name+'-frames.json')).write_text(json.dumps(self.fw.frames))
        assert code==expected, (code,self.logpath.read_text()[-3000:])

    def check(self, name, fn):
        try:
            fn()
            self.results.append({'case': name, 'result':'PASS'})
            print('PASS',name,flush=True)
        except Exception as error:
            self.results.append({'case':name,'result':'FAIL','error':str(error)})
            print('FAIL',name,str(error)[:700],flush=True)
            if getattr(self,'process',None) and self.process.poll() is None:
                self.process.send_signal(signal.SIGINT)
                try: self.process.wait(timeout=4)
                except subprocess.TimeoutExpired: self.process.kill(); self.process.wait()
            if getattr(self,'fw',None): self.fw.close()
            if getattr(self,'log',None): self.log.close()
        (self.out/'results.json').write_text(json.dumps(self.results,indent=2))

    def idle(self):
        self.start('cold_boot_nonzero_steering', angle=30000, ak_angle=300)
        self.spin(1.5)
        frames=self.window()
        self.zeros(frames)
        steering=self.values(frames,set(range(0x201,0x205)))
        assert len(steering)==4 and all(d=='00000000' for d in steering.values())
        arm=self.values(frames,AK)
        assert len(arm)==6 and all(abs(struct.unpack('>i',bytes.fromhex(d))[0]-300000)<=1 for d in arm.values()), arm
        # Current contract intentionally centers steering at idle, even at boot.
        self.finish()

    def delayed(self):
        self.start('delayed_feedback',delay=.6)
        self.spin(.4)
        assert not any(i in WHEELS and d!='00000000' for _,i,d in self.fw.frames)
        self.spin(1.5)
        self.zeros(self.window())
        self.finish()

    def absent(self, optional):
        self.start('optional_gripper_absent' if optional else 'required_steering_absent',absent={9 if optional else 3},timeout=250)
        self.spin(1.7,alive=optional)
        if optional:
            self.zeros(self.window())
            assert not any(i==0x209 for _,i,d in self.fw.frames)
            # A gripper powered later is not silently armed: restart is required.
            self.fw.absent=set()
            self.spin(.3)
            assert not any(i==0x209 or (i==0x239 and d=='01') for i,d in self.window())
        else:
            assert self.process.poll()==1
            assert not any(0x230<=i<=0x239 and d=='01' for _,i,d in self.fw.frames)
        self.finish(0 if optional else 1)

    def partial_activation(self):
        self.start('failure_after_steering_armed',absent={7},timeout=250)
        self.spin(1.7,alive=False)
        assert self.process.poll()==1
        for ident in (1,2,3,4):
            power=[d for _,i,d in self.fw.frames if i==0x230+ident]
            assert '01' in power and power[-1]=='00',(ident,power)
        assert not any(i in WHEELS and d!='00000000' for _,i,d in self.fw.frames)
        self.finish(1)

    def bad_activation(self, no_ack=False):
        self.start('arm_ack_missing' if no_ack else 'firmware_fault_at_boot',timeout=250,no_ack=no_ack,fault=0 if no_ack else 1)
        self.spin(1.3,alive=False)
        assert self.process.poll()==1
        assert not any(i in WHEELS and d!='00000000' for _,i,d in self.fw.frames)
        self.finish(1)

    def already_armed(self):
        self.start('already_armed_at_boot',armed=True)
        self.spin(1.7)
        for n in range(1,10):
            power=[d for _,i,d in self.fw.frames if i==0x230+n]
            assert power[:2]==['00','01'],(n,power)
        self.zeros(self.window()); self.finish()

    def cancel(self):
        self.start('cancel_during_activation',delay=10)
        self.spin(.25)
        self.finish(1)
        assert not any(0x230<=i<=0x239 and d=='01' for _,i,d in self.fw.frames)

    def startup_command(self):
        self.start('command_sent_only_during_startup',active=True,delay=1.2,timeout=2500)
        def send():
            self.drive()
            self.arm.publish(Float64MultiArray(data=[.2,.2,-.4,.3,1.,-.2]))
            self.grip.publish(Float64MultiArray(data=[.4]))
        self.spin(.7, send)
        # Sender stops before feedback/activation succeeds, beyond twist timeout.
        self.spin(1.2)
        moving=[(t,i,d) for t,i,d in self.fw.frames if i in WHEELS and d!='00000000']
        assert not moving, ('startup command replayed',moving[:3])
        positions=self.values(self.window(),POSITIONS)
        assert len(positions)==7 and all(d=='00000000' for d in positions.values()), positions
        # Fresh post-start commands must still work after DDS discovery.
        self.spin(.9,self.drive)
        assert any(i in WHEELS and d!='00000000' for i,d in self.window(send=self.drive))
        self.finish()

    def invalid_and_loss(self):
        self.start('invalid_commands_and_feedback_loss',active=True)
        self.spin(1.5)
        target=Float64MultiArray(data=[.1,.2,-.4,.3,1.,-.2])
        grip=Float64MultiArray(data=[.3])
        def send(): self.drive(); self.arm.publish(target); self.grip.publish(grip)
        self.spin(.9,send)
        good=self.values(self.window(send=send),POSITIONS)
        assert len(good)==7
        for value in (float('nan'),float('inf'),-float('inf')):
            def invalid():
                self.drive(value)
                self.arm.publish(Float64MultiArray(data=[value]*6))
                self.grip.publish(Float64MultiArray(data=[value]))
            self.spin(.12,invalid)
            frames=self.window(send=invalid)
            self.zeros(frames)
            assert self.values(frames,POSITIONS)==good
        self.spin(.8,self.drive)
        self.fw.absent=set(range(1,10))|set(range(101,107))
        self.spin(.7,self.drive)
        def malformed_feedback():
            self.drive()
            for motor in range(101,107):
                self.fw.sock.send(struct.pack('=IB3x8s',0x80002900+motor,2,bytes(8)))
        frames=self.window(.2,send=malformed_feedback)
        assert not any(i in AK for i,d in frames), 'AK commands continued past feedback timeout'
        # Characterize retained legacy behavior: OutputActuator has no RX timeout.
        assert any(i in WHEELS and d!='00000000' for i,d in frames), 'expected legacy output timeout behavior changed'
        self.fw.absent=set()
        self.spin(.15,self.drive)
        assert len(self.values(self.window(send=self.drive),AK))==6
        self.spin(.7)
        self.zeros(self.window())
        self.finish()

    def extreme_positions(self):
        self.start('extreme_positions_clamp',active=True)
        self.spin(1.5)
        for sign in (1.,-1.):
            samples=[]
            for magnitude in (1e6,1e308):
                def send():
                    self.arm.publish(Float64MultiArray(data=[sign*magnitude]*6))
                    self.grip.publish(Float64MultiArray(data=[sign*magnitude]))
                self.spin(.2,send)
                samples.append(self.values(self.window(send=send),POSITIONS))
            assert len(samples[0])==7 and samples[0]==samples[1],samples
        self.finish()

    def source_switch(self):
        self.start('fresh_and_stale_source_switch')
        self.spin(1.5)
        reverse=Twist(); reverse.linear.x=-.3
        def both():
            self.drive(); self.autonomous.publish(reverse)
        self.spin(.8,both)
        forward=self.values(self.window(send=both),WHEELS)
        self.mission.publish(MissionStatus(state=1))
        self.spin(.9,both)
        backward=self.values(self.window(send=both),WHEELS)
        for ident in WHEELS:
            f=struct.unpack('<i',bytes.fromhex(forward[ident]))[0]
            b=struct.unpack('<i',bytes.fromhex(backward[ident]))[0]
            assert f*b<0,(ident,f,b)
        self.spin(.8,lambda:self.autonomous.publish(reverse))
        self.mission.publish(MissionStatus(state=0))
        self.spin(.15,lambda:self.autonomous.publish(reverse))
        self.zeros(self.window(send=lambda:self.autonomous.publish(reverse)))
        self.finish()

    def reboot(self):
        self.start('actuator_disarms_while_driving')
        self.spin(1.5)
        self.spin(.8,self.drive)
        self.fw.no_ack=True
        self.spin(.12,self.drive)
        frames=self.window(send=self.drive)
        assert not any(i in WHEELS for i,d in frames), 'commands continued to disarmed actuators'
        assert not any(0x231<=i<=0x239 and d=='01' for i,d in frames), 'unexpected automatic rearm'
        self.finish()
        self.start('runtime_restart_without_new_commands')
        self.spin(1.5)
        self.zeros(self.window())
        self.finish()

    def lifecycle(self):
        self.start('position_activation_requires_fresh_command')
        self.spin(1.5)
        before=self.values(self.window(),POSITIONS)
        target=Float64MultiArray(data=[.2,.2,-.4,.3,1.,-.2])
        def send(): self.arm.publish(target); self.grip.publish(Float64MultiArray(data=[.4]))
        self.spin(.2,send)
        assert self.values(self.window(),POSITIONS)==before
        self.activate('arm',True); self.activate('gripper',True)
        assert self.values(self.window(),POSITIONS)==before
        self.spin(.2,send)
        after=self.values(self.window(),POSITIONS)
        assert after!=before
        self.activate('arm',False); self.activate('gripper',False)
        self.spin(.15,lambda:self.arm.publish(Float64MultiArray(data=[0.]*6)))
        assert self.values(self.window(),POSITIONS)==after
        self.finish()


def main():
    links=json.loads(subprocess.check_output(['ip','-details','-json','link','show','vcan0']))
    assert links[0]['linkinfo']['info_kind']=='vcan'
    rclpy.init(); suite=Suite()
    try:
        for name,fn in [('cold_boot',suite.idle),('delayed_feedback',suite.delayed),
                        ('missing_required',lambda:suite.absent(False)),('missing_optional',lambda:suite.absent(True)),('partial_activation_cleanup',suite.partial_activation),
                        ('fault_at_boot',suite.bad_activation),('missing_arm_ack',lambda:suite.bad_activation(True)),
                        ('already_armed',suite.already_armed),('cancel_activation',suite.cancel),
                        ('startup_command_replay',suite.startup_command),('invalid_and_loss',suite.invalid_and_loss),
                        ('position_lifecycle',suite.lifecycle),('extreme_positions',suite.extreme_positions),('source_switch',suite.source_switch),('actuator_disarm_and_restart',suite.reboot)]:
            suite.check(name,fn)
    finally:
        suite.node.destroy_node(); rclpy.shutdown()
    assert all(r['result']=='PASS' for r in suite.results), 'See /tmp/mr2-vcan-edge-results/results.json'

if __name__=='__main__': main()
