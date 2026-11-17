"""GNSS-only launch actions, independent of navigation and CAN hardware."""

from launch.actions import GroupAction, IncludeLaunchDescription, TimerAction
from launch.conditions import IfCondition
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration, PathJoinSubstitution, PythonExpression
from launch_ros.actions import Node
from launch_ros.substitutions import FindPackageShare


def gnss_actions(declare):
    declarations = [
        declare("enable_gnss", default_value="false", description="Start the two GNSS receivers without navigation"),
        declare("gnss_managed_externally", default_value="false",
                description="The real wrapper already owns the GNSS receivers"),
        declare("left_gnss_serial", default_value="TowerRx_"),
        declare("right_gnss_serial", default_value="NorthRx_"),
        declare("left_gnss_frame_id", default_value="left_gnss"),
        declare("right_gnss_frame_id", default_value="right_gnss"),
        declare("rtcm_input_topic", default_value="/base/rtcm"),
    ]
    actions = []
    for side, delay in (("left", 0.0), ("right", 3.0)):
        receiver = IncludeLaunchDescription(
            PythonLaunchDescriptionSource(PathJoinSubstitution([
                FindPackageShare("mr2_launch"), "launch", "ublox_fb_r_rover_named.launch.py",
            ])),
            launch_arguments={
                "namespace": f"{side}_gnss",
                "device_family": "F9P",
                "device_serial_string": LaunchConfiguration(f"{side}_gnss_serial"),
                "frame_id": LaunchConfiguration(f"{side}_gnss_frame_id"),
                "rtcm_input_topic": LaunchConfiguration("rtcm_input_topic"),
            }.items(),
        )
        actions.extend([
            TimerAction(period=delay, actions=[receiver]),
            Node(
                package="topic_tools", executable="relay", name=f"{side}_gnss_navsat_relay",
                arguments=[f"/{side}_gnss/fix", f"/{side}_gnss/navsat"], output="screen",
            ),
        ])
    return [*declarations, GroupAction(actions=actions, condition=IfCondition(PythonExpression([
        "'", LaunchConfiguration("enable_gnss"), "' == 'true' and '",
        LaunchConfiguration("gnss_managed_externally"), "' != 'true'",
    ])))]
