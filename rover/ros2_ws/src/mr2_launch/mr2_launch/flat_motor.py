"""Optional flat-only motor path; never shares a motor with the original stack."""

from launch.actions import GroupAction, OpaqueFunction
from launch.conditions import IfCondition
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node
from launch_ros.parameter_descriptions import ParameterValue


def validate_flat_motor(context):
    values = context.launch_configurations
    if (values.get('mode') != 'real'
            or values.get('enable_rover_hardware') != 'false'
            or values.get('enable_manipulator_module') != 'false'):
        raise RuntimeError('Flat motor requires real mode and both enable_rover_hardware=false '
                           'and enable_manipulator_module=false; motor ownership must not overlap')
    return []


def flat_motor_actions(declare):
    return [
        declare('enable_flat_motor', default_value='false',
                description='Start the isolated single-motor trigger controller'),
        declare('flat_motor_clockwise_sign', default_value='-1', choices=['-1', '1'],
                description='CAN angle sign for clockwise rotation, viewed facing the output shaft'),
        declare('flat_motor_command_topic', default_value='/base/cmd_vel',
                description='Existing rover-side Twist command topic'),
        declare('flat_motor_command_component', default_value='linear.x',
                choices=['linear.x', 'linear.y', 'angular.z'],
                description='Twist component mapped to flat motor speed'),
        declare('flat_motor_max_speed_deg_s', default_value='180.0',
                description='Output shaft speed at full drive sensitivity/input (degrees/s)'),
        declare('flat_motor_max_accel_deg_s2', default_value='720.0',
                description='Acceleration/reversal slew limit; stop commands bypass this ramp'),
        GroupAction(condition=IfCondition(LaunchConfiguration('enable_flat_motor')), actions=[
            OpaqueFunction(function=validate_flat_motor),
            Node(package='mr2_flat_motor', executable='flat_motor', output='screen', parameters=[{
                'can_iface': LaunchConfiguration('can_iface'),
                'command_topic': LaunchConfiguration('flat_motor_command_topic'),
                'command_component': LaunchConfiguration('flat_motor_command_component'),
                'clockwise_sign': ParameterValue(LaunchConfiguration('flat_motor_clockwise_sign'), value_type=int),
                'max_speed_deg_s': ParameterValue(LaunchConfiguration('flat_motor_max_speed_deg_s'), value_type=float),
                'max_accel_deg_s2': ParameterValue(LaunchConfiguration('flat_motor_max_accel_deg_s2'), value_type=float),
            }]),
        ]),
    ]
