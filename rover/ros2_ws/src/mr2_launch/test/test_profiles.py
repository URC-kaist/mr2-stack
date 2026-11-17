"""Validate profile selection and launch composition without starting processes."""

import json
from pathlib import Path
import shutil
import subprocess

import pytest
from launch import LaunchContext, LaunchDescription
from launch.actions import (
    DeclareLaunchArgument, ExecuteProcess, GroupAction, IncludeLaunchDescription,
    OpaqueFunction, TimerAction,
)
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.utilities import normalize_to_list_of_substitutions, perform_substitutions
from launch_ros.actions import Node, SetParameter

from mr2_launch import profile


PACKAGE = Path(__file__).resolve().parents[1]


@pytest.fixture
def selection(tmp_path, monkeypatch):
    import ament_index_python.packages
    original_share = ament_index_python.packages.get_package_share_directory
    def package_share(name):
        if name in {'mr2_launch', 'mr2_rover_description'}:
            return str(PACKAGE.parent / name)
        return original_share(name)
    monkeypatch.setattr(ament_index_python.packages, "get_package_share_directory", package_share)
    monkeypatch.setattr('launch_ros.substitutions.find_package.get_package_share_directory', package_share)
    monkeypatch.setenv('MR2_BASE_IP', '192.0.2.1')
    monkeypatch.setattr(profile, "repo_root", lambda: tmp_path)
    profile.selected_profile.cache_clear()
    yield tmp_path
    profile.selected_profile.cache_clear()


def test_selector_only_persists_selection(selection):
    scripts = selection / "scripts"
    scripts.mkdir()
    source = next(p / "scripts/rover.bash" for p in PACKAGE.parents if (p / "scripts/rover.bash").exists())
    script = scripts / "rover.bash"
    shutil.copyfile(source, script)
    for name in ("flat", "original", "flat"):
        subprocess.run(["bash", str(script), "start", name], check=True)
        assert (selection / ".mr2-rover-profile").read_text().strip() == name
    assert subprocess.run(["bash", str(script), "start", "typo"]).returncode == 2
    assert (selection / ".mr2-rover-profile").read_text().strip() == "flat"
    assert sorted(p.name for p in selection.iterdir()) == [".mr2-rover-profile", "scripts"]


def test_default_original_invalid_selection_and_explicit_override(selection):
    assert profile.selected_profile()[0] == "original"
    (selection / ".mr2-rover-profile").write_text("flat\n")
    assert profile.selected_profile()[0] == "original"  # Running launch stays pinned.
    profile.selected_profile.cache_clear()
    declare = profile.profile_arguments("rover")
    context = LaunchContext()
    context.launch_configurations["headless"] = "false"
    declare("headless", default_value="true").execute(context)
    assert context.launch_configurations["headless"] == "false"
    (selection / ".mr2-rover-profile").write_text("typo\n")
    with pytest.raises(RuntimeError, match="Invalid rover profile"):
        profile.read_profile(selection)


def composition(entrypoint, overrides=None):
    context = LaunchContext()
    context.launch_configurations.update(overrides or {})
    packages = []
    node_config = {}

    def resolve(value):
        return perform_substitutions(context, normalize_to_list_of_substitutions(value))

    def visit(action):
        condition = getattr(action, "condition", None)
        if condition is not None and not condition.evaluate(context):
            return
        if isinstance(action, DeclareLaunchArgument):
            action.execute(context)
        elif isinstance(action, Node):
            packages.append(resolve(action.node_package))
            node_config.update(context.launch_configurations)
        elif isinstance(action, (ExecuteProcess, OpaqueFunction, SetParameter)):
            pass  # Never execute processes, systemctl, or hardware initialization.
        elif isinstance(action, IncludeLaunchDescription):
            arguments = {resolve(k): resolve(v) for k, v in action.launch_arguments}
            context.launch_configurations.update(arguments)
            visit(action.launch_description_source.get_launch_description(context))
        elif isinstance(action, GroupAction):
            saved = dict(context.launch_configurations)
            for child in action.get_sub_entities():
                # Group scope actions are handled by the explicit snapshot here.
                if child.__class__.__name__ not in {"PushLaunchConfigurations", "PopLaunchConfigurations", "PushEnvironment", "PopEnvironment", "SetLaunchConfiguration", "ResetEnvironment"}:
                    visit(child)
            context.launch_configurations.clear()
            context.launch_configurations.update(saved)
        elif isinstance(action, (TimerAction, LaunchDescription)):
            for child in action.actions if isinstance(action, TimerAction) else action.entities:
                visit(child)
        else:
            raise AssertionError(f"Unexpected launch action: {type(action)}")

    visit(PythonLaunchDescriptionSource(str(PACKAGE / "launch" / entrypoint)).get_launch_description(context))
    # Nested launches may correctly scope their private arguments. Keep the
    # configuration actually seen by nodes for assertions about nested defaults.
    return packages, {**node_config, **context.launch_configurations}


@pytest.mark.parametrize("entrypoint", ["rover.launch.py", "rover_real.launch.py", "rover_direct.launch.py"])
def test_flat_has_two_gnss_video_and_only_single_motor_can(selection, entrypoint):
    (selection / ".mr2-rover-profile").write_text("flat\n")
    packages, config = composition(entrypoint)
    assert packages.count("ublox_dgnss_node") == 2
    assert packages.count("ublox_nav_sat_fix_hp_node") == 2
    assert packages.count("mr2_video_streaming") == 1
    assert packages.count("mr2_flat_motor") == 1
    assert packages.count("mr2_xbee_bridge") == 1
    assert config['smooth_drive_commands'] == 'false'
    assert config['flat_motor_max_speed_deg_s'] == '180.0'
    assert not set(packages) & {
        "controller_manager", "mr2_control", "mr2_battery_monitor", "mr2_led",
        "mr2_camera_turret", "mr2_science_module", "realsense2_camera", "robot_localization",
    }
    streams = json.loads(Path(config["video_config"]).read_text())["streams"]
    assert len(streams) == 4
    assert len({s["source"]["device"] for s in streams}) == 4


@pytest.mark.parametrize('entrypoint', ['rover.launch.py', 'rover_real.launch.py', 'rover_direct.launch.py'])
@pytest.mark.parametrize('enabled', ['false', 'true'])
def test_flat_latency_agent_preserves_video_and_motor_profile(selection, entrypoint, enabled):
    (selection / '.mr2-rover-profile').write_text('flat\n')
    packages, config = composition(entrypoint, {'enable_latency_diagnostics': enabled})
    assert packages.count('mr2_latency_diagnostics') == int(enabled == 'true')
    assert packages.count('mr2_flat_motor') == 1
    assert config['enable_rover_hardware'] == 'false'
    assert config['smooth_drive_commands'] == 'false'
    assert Path(config['video_config']).name == 'video_streams_flat.json'


@pytest.mark.parametrize('entrypoint', ['rover.launch.py', 'rover_real.launch.py', 'rover_direct.launch.py'])
def test_flat_camera_only_diagnostics_exclude_gnss_and_motor(selection, entrypoint):
    (selection / '.mr2-rover-profile').write_text('flat\n')
    packages, _ = composition(entrypoint, {'enable_gnss': 'false',
                                         'enable_flat_motor': 'false',
                                         'enable_latency_diagnostics': 'true'})
    assert 'mr2_latency_diagnostics' in packages
    assert 'mr2_video_streaming' in packages
    assert not {'ublox_dgnss_node', 'ublox_nav_sat_fix_hp_node', 'mr2_flat_motor'} & set(packages)


def test_original_profile_does_not_enable_flat_motor(selection):
    from mr2_launch.flat_motor import flat_motor_actions
    context = LaunchContext()
    actions = flat_motor_actions(profile.profile_arguments('rover'))
    actions[0].execute(context)
    assert context.launch_configurations['enable_flat_motor'] == 'false'
    assert not actions[-1].condition.evaluate(context)
    profile.profile_arguments('rover')('smooth_drive_commands', default_value='true').execute(context)
    assert context.launch_configurations['smooth_drive_commands'] == 'true'


@pytest.mark.parametrize('entrypoint', ['rover.launch.py', 'rover_real.launch.py', 'rover_direct.launch.py'])
def test_flat_motor_can_be_explicitly_disabled(selection, entrypoint):
    (selection / '.mr2-rover-profile').write_text('flat\n')
    packages, _ = composition(entrypoint, {'enable_flat_motor': 'false'})
    assert 'mr2_flat_motor' not in packages


@pytest.mark.parametrize('entrypoint', ['rover.launch.py', 'rover_real.launch.py', 'rover_direct.launch.py'])
def test_flat_speed_override_reaches_nested_launch(selection, entrypoint):
    (selection / '.mr2-rover-profile').write_text('flat\n')
    _, config = composition(entrypoint, {'flat_motor_max_speed_deg_s': '360.0'})
    assert config['flat_motor_max_speed_deg_s'] == '360.0'


@pytest.mark.parametrize('changes', [
    {'mode': 'sim'}, {'enable_rover_hardware': 'true'},
    {'enable_manipulator_module': 'true'},
])
def test_flat_rejects_conflicting_hardware_owners(changes):
    from mr2_launch.flat_motor import validate_flat_motor
    context = LaunchContext()
    context.launch_configurations.update(mode='real', enable_rover_hardware='false',
                                         enable_manipulator_module='false')
    validate_flat_motor(context)
    context.launch_configurations.update(changes)
    with pytest.raises(RuntimeError, match='ownership'):
        validate_flat_motor(context)
