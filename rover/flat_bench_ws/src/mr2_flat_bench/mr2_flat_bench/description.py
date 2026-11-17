"""Derive a bench hardware description from the installed production xacro."""
from pathlib import Path
import xml.etree.ElementTree as ET

from ament_index_python.packages import get_package_share_directory
import xacro

MOCK = "mr2_flat_bench/MockCanDevice"
RS = "mr2_devices_robstride/RobstrideDevice"
WHEELS = {key: key + "_wheel_joint" for key in ("fl", "fr", "rl", "rr")}


def production_description(can_iface="can0"):
    package = Path(get_package_share_directory("mr2_rover_description"))
    return xacro.process_file(str(package / "urdf/rover.urdf.xacro"), mappings={
        "ros2_control_mode": "real_hardware", "actuator": "rs", "can_iface": can_iface,
        "enable_manipulator_module": "false", "enable_autonomous_module": "false",
    }).toxml()


def make_description(real_wheel="fl", motor_id=127, can_iface="can0"):
    if real_wheel not in (*WHEELS, "none"):
        raise ValueError("real_wheel must be fl/fr/rl/rr/none")
    if not 1 <= int(motor_id) <= 127:
        raise ValueError("motor_id must be 1..127")
    robot = ET.fromstring(production_description(can_iface))
    controls = robot.findall("ros2_control")
    if len(controls) != 1 or controls[0].findtext("hardware/plugin") != "mr2_can_hardware_interface/CanHW":
        raise RuntimeError("Unexpected production CanHW description")
    joints = controls[0].findall("joint")
    expected = set(WHEELS.values()) | {key + "_steering_joint" for key in WHEELS}
    if {j.attrib["name"] for j in joints} != expected:
        raise RuntimeError("Production description must expose the eight drive/steering joints")
    selected = WHEELS.get(real_wheel)
    for joint in joints:
        parameters = {p.attrib["name"]: p for p in joint.findall("param")}
        if joint.attrib["name"] == selected:
            if parameters["device_plugin"].text != RS:
                raise RuntimeError("Selected wheel is not the production RS plugin")
            # Physical fixture overrides only; controller YAML/transmissions stay original.
            for key, value in {"motor_id": str(int(motor_id)), "max_velocity_rad_s": "0.2",
                               "current_limit_a": "1.0", "acceleration_rad_s2": "0.5"}.items():
                parameters[key].text = value
        else:
            parameters["device_plugin"].text = MOCK
    return ET.tostring(robot, encoding="unicode")
