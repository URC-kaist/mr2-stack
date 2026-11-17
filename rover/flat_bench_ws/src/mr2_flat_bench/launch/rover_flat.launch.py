"""Reuse normal rover services and control software, replacing bench hardware only."""
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, IncludeLaunchDescription
from launch.conditions import LaunchConfigurationEquals
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration, PathJoinSubstitution
from launch_ros.substitutions import FindPackageShare


def generate_launch_description():
    arguments = [
        DeclareLaunchArgument("connection", default_value="direct", choices=["direct", "base", "none"]),
        DeclareLaunchArgument("real_wheel", default_value="fl", choices=["fl", "fr", "rl", "rr", "none"]),
        DeclareLaunchArgument("motor_id", default_value="127"),
        DeclareLaunchArgument("can_iface", default_value="can0"),
        DeclareLaunchArgument("enable_video_streaming", default_value="true", choices=["true", "false"]),
    ]
    # These overrides are local to this new launch. Production files/profiles
    # are never rewritten; no legacy direct flat-motor owner may be started.
    services = {
        "enable_rover_hardware": "false", "enable_flat_motor": "false",
        "enable_manipulator_module": "false", "enable_autonomous_module": "false",
        "enable_localization": "false", "enable_gnss": "false", "enable_ntrip": "false",
        "enable_science_module": "false", "enable_led": "false", "enable_camera_turret": "false",
        "enable_video_streaming": LaunchConfiguration("enable_video_streaming"),
        "smooth_drive_commands": "true",
        "control_backend": "ros2_control", "can_iface": LaunchConfiguration("can_iface"),
    }
    includes = []
    for connection, filename in [("direct", "rover_direct.launch.py"), ("base", "rover_real.launch.py")]:
        selected_services = dict(services)
        if connection == "direct":
            selected_services["video_base_host"] = "127.0.0.1"
        includes.append(IncludeLaunchDescription(
            PythonLaunchDescriptionSource(PathJoinSubstitution([
                FindPackageShare("mr2_launch"), "launch", filename])),
            launch_arguments=selected_services.items(),
            condition=LaunchConfigurationEquals("connection", connection),
        ))
    hardware = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(PathJoinSubstitution([
            FindPackageShare("mr2_flat_bench"), "launch", "hardware.launch.py"])),
        launch_arguments={key: LaunchConfiguration(key) for key in
                          ("real_wheel", "motor_id", "can_iface")}.items(),
    )
    return LaunchDescription([*arguments, *includes, hardware])
