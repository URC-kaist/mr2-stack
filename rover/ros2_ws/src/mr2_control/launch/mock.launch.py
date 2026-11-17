"""Hardware-free full rover control validation; no SocketCAN users are launched."""
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration, PathJoinSubstitution
from launch_ros.actions import Node
from launch_ros.substitutions import FindPackageShare
from launch_ros.descriptions import ParameterValue
from mr2_rover_description.launch_common import robot_description_from_xacro


def generate_launch_description():
    share = FindPackageShare("mr2_rover_description")
    config = PathJoinSubstitution([share, "config", "controllers", "rover_controllers.yaml"])
    enabled = LaunchConfiguration("enable_manipulator_module")
    description = robot_description_from_xacro(
        PathJoinSubstitution([share, "urdf", "rover.urdf.xacro"]),
        {"ros2_control_mode": "real_hardware", "can_iface": "unused_mock",
         "enable_manipulator_module": enabled, "ros2_control_config": config},
    )
    return LaunchDescription([
        DeclareLaunchArgument("enable_manipulator_module", default_value="true"),
        Node(package="mr2_control", executable="mr2_control_node", name="mr2_control",
             parameters=[description, {"controller_config": config, "mock_devices": True,
                         "enable_manipulator_module": ParameterValue(enabled, value_type=bool)}],
             output="screen"),
    ])
