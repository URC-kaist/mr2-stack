"""Resolve the destination at launch execution, after acquiring the clock lock."""
from unittest.mock import patch

from launch import LaunchContext
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from mr2_launch.clock_guard import video_base_host_default


def resolve(peer=None, explicit=None):
    context = LaunchContext()
    if peer:
        context.launch_configurations['mr2_clock_base_peer'] = peer
    if explicit:
        context.launch_configurations['video_base_host'] = explicit
    DeclareLaunchArgument('video_base_host', default_value=video_base_host_default()).execute(context)
    return LaunchConfiguration('video_base_host').perform(context)


def test_development_base_overrides_field_fallback():
    with patch.dict('os.environ', {'MR2_BASE_IP': '192.168.1.101'}):
        assert resolve('192.168.0.62') == '192.168.0.62'
        assert resolve('192.168.1.101') == '192.168.1.101'
        assert resolve() == '192.168.1.101'


def test_explicit_destination_including_direct_loopback_wins():
    assert resolve('192.168.0.62', '127.0.0.1') == '127.0.0.1'
    assert resolve('192.168.0.62', '192.168.1.101') == '192.168.1.101'
