"""The production controller manager, controller config and CanHW with bench devices."""
from pathlib import Path

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, LogInfo, OpaqueFunction
from launch.substitutions import LaunchConfiguration
from launch_ros.parameter_descriptions import ParameterValue
from mr2_flat_bench.description import make_description
from mr2_rover_description.launch_common import (
    controller_spawners, robot_state_publisher_node, ros2_control_node,
)


def setup(context):
    wheel = LaunchConfiguration("real_wheel").perform(context)
    motor = int(LaunchConfiguration("motor_id").perform(context))
    iface = LaunchConfiguration("can_iface").perform(context)
    description = {"robot_description": ParameterValue(make_description(wheel, motor, iface), value_type=str)}
    config = str(Path(get_package_share_directory("mr2_rover_description")) /
                 "config/controllers/rover_controllers.yaml")
    return [
        LogInfo(msg=f"Flat bench: production controller YAML={config}; CanHW; real={wheel}, id={motor}; remaining devices are mocks"),
        robot_state_publisher_node(description, False),
        ros2_control_node(config, description, False),
        *controller_spawners(["joint_state_broadcaster", "rover_controller"],
                             start_after=1.0, interval=1.0),
    ]


def generate_launch_description():
    return LaunchDescription([
        DeclareLaunchArgument("real_wheel", default_value="fl", choices=["fl", "fr", "rl", "rr", "none"]),
        DeclareLaunchArgument("motor_id", default_value="127"),
        DeclareLaunchArgument("can_iface", default_value="can0"),
        OpaqueFunction(function=setup),
    ])
