"""Only hardware substitutions may differ from the installed rover description."""
import hashlib
from pathlib import Path
import xml.etree.ElementTree as ET

from ament_index_python.packages import get_package_share_directory
import pytest
from mr2_flat_bench.description import make_description, production_description, MOCK, RS, WHEELS


@pytest.mark.parametrize("wheel", ["fl", "fr", "rl", "rr", "none"])
def test_common_software_and_all_joint_interfaces_preserved(wheel):
    original = ET.fromstring(production_description())
    bench = ET.fromstring(make_description(wheel))
    before, after = original.find("ros2_control"), bench.find("ros2_control")
    assert ET.tostring(before.find("hardware")) == ET.tostring(after.find("hardware"))
    assert [ET.tostring(t) for t in before.findall("transmission")] == [
        ET.tostring(t) for t in after.findall("transmission")]
    joints = after.findall("joint")
    assert len(joints) == 8
    for old, new in zip(before.findall("joint"), joints):
        assert old.attrib == new.attrib
        assert [i.attrib for i in old.findall("command_interface")] == [i.attrib for i in new.findall("command_interface")]
        assert [i.attrib for i in old.findall("state_interface")] == [i.attrib for i in new.findall("state_interface")]
        selected = new.attrib["name"] == WHEELS.get(wheel)
        old_params = {p.attrib["name"]: p.text for p in old.findall("param")}
        new_params = {p.attrib["name"]: p.text for p in new.findall("param")}
        assert new_params["device_plugin"] == (RS if selected else MOCK)
        permitted = {"device_plugin", "motor_id", "current_limit_a", "max_velocity_rad_s", "acceleration_rad_s2"} if selected else {"device_plugin"}
        assert {k: v for k, v in old_params.items() if k not in permitted} == {
            k: v for k, v in new_params.items() if k not in permitted}
        if selected:
            assert new_params["motor_id"] == "127"
            assert new_params["max_velocity_rad_s"] == "0.2"
    # Remove only ros2_control from both: geometry, kinematics and URDF stay identical.
    original.remove(before); bench.remove(after)
    assert ET.tostring(original) == ET.tostring(bench)


def test_production_files_not_written():
    share = Path(get_package_share_directory("mr2_rover_description"))
    files = [share / "urdf/rover.urdf.xacro", share / "ros2_control/rover_can.ros2_control.xacro",
             share / "config/controllers/rover_controllers.yaml"]
    before = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
    make_description()
    assert before == {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in files}


@pytest.mark.parametrize("wheel,motor", [("bad", 127), ("fl", 0), ("fl", 128)])
def test_invalid_selection_rejected(wheel, motor):
    with pytest.raises(ValueError):
        make_description(wheel, motor)
