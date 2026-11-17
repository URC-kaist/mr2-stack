"""Inspect launch ownership without executing nodes or opening any device."""
import importlib.util
import sys
from pathlib import Path
from launch import LaunchContext
from launch.actions import GroupAction, TimerAction
from launch_ros.actions import Node
from launch.utilities import perform_substitutions, normalize_to_list_of_substitutions

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'mr2_rover_description'))
sys.path.insert(0, str(ROOT / 'mr2_launch'))


def load(path):
    spec = importlib.util.spec_from_file_location('launch_under_test', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def active_nodes(entities, context):
    result = []
    for entity in entities:
        condition = entity.condition if hasattr(entity, 'condition') else None
        if condition is not None and not condition.evaluate(context):
            continue
        if isinstance(entity, Node):
            result.append((perform_substitutions(context, normalize_to_list_of_substitutions(entity.node_package)),
                           perform_substitutions(context, normalize_to_list_of_substitutions(entity.node_executable))))
        elif isinstance(entity, TimerAction):
            result.extend(active_nodes(entity.actions, context))
        elif isinstance(entity, GroupAction):
            result.extend(active_nodes(entity.get_sub_entities(), context))
    return result


def test_exclusive_real_backends():
    module = load(ROOT / 'mr2_rover_description/launch/real.launch.py')
    for backend in ['ros2_control', 'mr2_control']:
        for enabled in ['false', 'true']:
            for active in ['false', 'true']:
                context = LaunchContext()
                context.launch_configurations.update(
                    control_backend=backend, enable_manipulator_module=enabled,
                    start_manipulator_controllers_active=active,
                    use_mock_servos='false', controller_spawn_delay='2.0')
                nodes = active_nodes(module.generate_launch_description().entities, context)
                assert nodes.count(('controller_manager', 'ros2_control_node')) == (backend == 'ros2_control')
                assert nodes.count(('mr2_control', 'mr2_control_node')) == (backend == 'mr2_control')
                if backend == 'mr2_control':
                    assert not any(package == 'controller_manager' for package, _ in nodes)
                else:
                    assert nodes.count(('controller_manager', 'spawner')) == (3 if enabled == 'true' else 2)


def test_standalone_backend_exclusion():
    module = load(ROOT / 'mr2_rover_description/launch/manipulator_ak_can.launch.py')
    for backend in ['ros2_control', 'mr2_control']:
        context = LaunchContext()
        context.launch_configurations.update(control_backend=backend, use_mock_servos='false')
        nodes = active_nodes(module.generate_launch_description().entities, context)
        assert nodes.count(('mr2_control', 'mr2_control_node')) == (backend == 'mr2_control')
        assert nodes.count(('controller_manager', 'spawner')) == (3 if backend == 'ros2_control' else 0)


def test_simulation_rejects_unsupported_new_backend():
    module = load(ROOT / 'mr2_launch/launch/rover.launch.py')
    context = LaunchContext()
    context.launch_configurations.update(mode='sim', control_backend='mr2_control')
    try:
        module._validate_control_backend(context)
    except RuntimeError:
        pass
    else:
        raise AssertionError('Simulation silently accepted unsupported backend')
    context.launch_configurations['control_backend'] = 'ros2_control'
    assert module._validate_control_backend(context) == []
