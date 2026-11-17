"""Validate real launch propagation and xacro without starting hardware."""

from pathlib import Path
import xml.etree.ElementTree as ET

import pytest
import xacro
from launch import LaunchContext, LaunchDescription
from launch.actions import DeclareLaunchArgument, GroupAction, IncludeLaunchDescription, TimerAction
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.utilities import normalize_to_list_of_substitutions, perform_substitutions
from launch_ros.actions import Node
from launch_ros.utilities import evaluate_parameters

from mr2_launch import profile


SOURCE = Path(__file__).resolve().parents[2]
CUSTOM = "mr2_devices_output_actuator/CustomActuatorDevice"
RS = "mr2_devices_robstride/RobstrideDevice"
WHEELS = {"fl_wheel_joint": "7", "fr_wheel_joint": "5",
          "rl_wheel_joint": "8", "rr_wheel_joint": "6"}


@pytest.fixture(autouse=True)
def source_packages(monkeypatch, tmp_path):
    import ament_index_python.packages
    original = ament_index_python.packages.get_package_share_directory

    def share(name):
        if name in {"mr2_launch", "mr2_rover_description"}:
            return str(SOURCE / name)
        return original(name)

    monkeypatch.setattr(ament_index_python.packages, "get_package_share_directory", share)
    monkeypatch.setattr("launch_ros.substitutions.find_package.get_package_share_directory", share)
    monkeypatch.setattr(profile, "repo_root", lambda: tmp_path)
    monkeypatch.setenv("MR2_BASE_IP", "192.0.2.1")
    profile.selected_profile.cache_clear()
    yield
    profile.selected_profile.cache_clear()


def hardware(option=None, arm=True):
    mappings = {"ros2_control_mode": "real_hardware",
                "enable_manipulator_module": str(arm).lower()}
    if option is not None:
        mappings["actuator"] = option
    return ET.fromstring(xacro.process_file(
        str(SOURCE / "mr2_rover_description/urdf/rover.urdf.xacro"),
        mappings=mappings).toxml()).find("ros2_control")


def params(joint):
    return {p.attrib["name"]: p.text for p in joint.findall("param")}


@pytest.mark.parametrize("arm", [True, False])
def test_only_four_wheels_switch_and_custom_is_default(arm):
    default = hardware(arm=arm)
    custom = hardware("custom", arm)
    rs = hardware("rs", arm)
    assert ET.tostring(default) == ET.tostring(custom)
    custom_joints = {j.attrib["name"]: j for j in custom.findall("joint")}
    rs_joints = {j.attrib["name"]: j for j in rs.findall("joint")}
    assert set(custom_joints) == set(rs_joints)
    assert sum(params(j)["device_plugin"] == RS for j in rs_joints.values()) == 4
    for name, joint in rs_joints.items():
        before, after = params(custom_joints[name]), params(joint)
        if name in WHEELS:
            assert before["device_plugin"] == CUSTOM
            assert after["device_plugin"] == RS
            assert before["node_id"] == after["motor_id"] == WHEELS[name]
            assert after["host_id"] == "253"
            assert before["command_channel"] == "velocity"
            assert before["desired_profile"] == "VelocityOnly"
            assert before["actuator"] == after["actuator"]
            assert before["can_iface"] == after["can_iface"] == "can0"
            assert [i.attrib for i in joint.findall("command_interface")] == [{"name": "velocity"}]
        else:
            assert ET.tostring(custom_joints[name]) == ET.tostring(joint)
            if name.endswith("_steering_joint") or name == "arm_gripper":
                assert after["device_plugin"] == CUSTOM
    assert [ET.tostring(t) for t in custom.findall("transmission")] == [
        ET.tostring(t) for t in rs.findall("transmission")]
    reductions = {t.attrib["name"]: t.find("joint/mechanical_reduction").text
                  for t in rs.findall("transmission") if "drive" in t.attrib["name"]}
    assert reductions == {"fl_drive_transmission": "-1.0", "fr_drive_transmission": "1.0",
                          "rl_drive_transmission": "-1.0", "rr_drive_transmission": "1.0"}


def test_xacro_rejects_unknown_actuator():
    with pytest.raises(xacro.XacroException):
        hardware("unknown")


def compose(entrypoint, option, backend="ros2_control"):
    context = LaunchContext()
    context.launch_configurations.update({
        "mode": "real", "control_backend": backend,
        "enable_rover_hardware": "true", "can_iface": "virtual_test_can",
        "enable_manipulator_module": "false", "enable_autonomous_module": "false",
    })
    if option is not None:
        context.launch_configurations["actuator"] = option
    descriptions = []
    arguments_seen = []

    def resolve(value):
        return perform_substitutions(context, normalize_to_list_of_substitutions(value))

    def visit(action):
        condition = getattr(action, "condition", None)
        if condition is not None and not condition.evaluate(context):
            return
        if isinstance(action, DeclareLaunchArgument):
            action.execute(context)
            if action.name == "actuator":
                arguments_seen.append(context.launch_configurations["actuator"])
        elif isinstance(action, IncludeLaunchDescription):
            path = resolve(action.launch_description_source._LaunchDescriptionSource__location)
            if Path(path).name not in {"rover.launch.py", "real.launch.py"}:
                return
            for key, value in action.launch_arguments:
                context.launch_configurations[resolve(key)] = resolve(value)
            visit(action.launch_description_source.get_launch_description(context))
        elif isinstance(action, Node):
            if resolve(action.node_package) in {"robot_state_publisher", "mr2_control"}:
                values = evaluate_parameters(context, action._Node__parameters)
                descriptions.extend(v["robot_description"] for v in values
                                    if isinstance(v, dict) and "robot_description" in v)
        elif isinstance(action, LaunchDescription):
            for child in action.entities:
                visit(child)
        elif isinstance(action, TimerAction):
            for child in action.actions:
                visit(child)
        elif isinstance(action, GroupAction):
            saved = dict(context.launch_configurations)
            for child in action.get_sub_entities():
                visit(child)
            context.launch_configurations.clear()
            context.launch_configurations.update(saved)
        # Processes, clock guards and unrelated launch actions are never run.

    package = "mr2_rover_description" if entrypoint == "real.launch.py" else "mr2_launch"
    path = SOURCE / package / "launch" / entrypoint
    visit(PythonLaunchDescriptionSource(str(path)).get_launch_description(context))
    return arguments_seen, descriptions


@pytest.mark.parametrize("entrypoint", ["rover_real.launch.py", "rover_direct.launch.py",
                                       "rover.launch.py", "real.launch.py"])
@pytest.mark.parametrize("option", [None, "custom", "rs"])
@pytest.mark.parametrize("backend", ["ros2_control", "mr2_control"])
def test_launch_propagates_selection_into_robot_description(entrypoint, option, backend):
    seen, descriptions = compose(entrypoint, option, backend)
    assert seen and all(value == (option or "custom") for value in seen)
    assert descriptions
    for description in descriptions:
        joints = ET.fromstring(description).findall("ros2_control/joint")
        for joint in joints:
            values = params(joint)
            assert values["can_iface"] == "virtual_test_can"
            expected = RS if option == "rs" and joint.attrib["name"] in WHEELS else CUSTOM
            assert values["device_plugin"] == expected


@pytest.mark.parametrize("entrypoint", ["rover_real.launch.py", "rover_direct.launch.py",
                                       "rover.launch.py", "real.launch.py"])
def test_launch_rejects_unknown_actuator(entrypoint):
    with pytest.raises(RuntimeError, match="actuator"):
        compose(entrypoint, "unknown")
