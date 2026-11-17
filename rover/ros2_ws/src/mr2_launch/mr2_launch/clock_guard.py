"""Acquire clock policy before real launch actions; process lifetime releases it."""
import json
import os
import socket
import time
from pathlib import Path


def acquire_clock(context, *args, **kwargs):
    # Included launch files run in the same process and share one session.
    from launch.actions import LogInfo, SetEnvironmentVariable, SetLaunchConfiguration
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(25)
            sock.connect('/run/mr2-clock/control.sock')
            request = {"op": "acquire", "prepare": True}
            # CLI values exist before DeclareLaunchArgument actions execute.
            # Reuse an explicit destination for the clock lease without writing
            # development addresses into the field configuration.
            explicit = context.launch_configurations.get("video_base_host")
            if explicit:
                request["base_host"] = explicit
            sock.sendall((json.dumps(request) + "\n").encode())
            response = json.loads(sock.makefile('r').readline(8192))
        if not response.get('ok') or not response.get('locked'):
            raise RuntimeError(response.get('error', 'Clock lock not acknowledged'))
    except (OSError, ValueError, RuntimeError) as error:
        raise RuntimeError(
            'Cannot start real rover launch without its clock lock. '
            'Check systemctl status mr2-clock.service: ' + str(error)
        ) from error
    first_acquisition = os.environ.get('MR2_CLOCK_SESSION') != response['id']
    os.environ['MR2_CLOCK_SESSION'] = response['id']
    # Allow the locked local estimator to warm up before camera/network load.
    # A missing clock must never prevent viewing video or operating the rover.
    if first_acquisition and response.get('base_peer'):
        wait_for_relative_clock(response['base_peer'])
    actions = [SetEnvironmentVariable('MR2_CLOCK_SESSION', response['id'])]
    if response.get('base_peer'):
        actions.append(SetLaunchConfiguration('mr2_clock_base_peer', response['base_peer']))
    actions.append(LogInfo(msg='Clock locked for ROS launch; base=' + str(response.get('base_peer') or 'unavailable')))
    return actions


def wait_for_relative_clock(peer):
    deadline = time.monotonic() + 90
    print('[clock] Preparing relative time before video load (up to 90 s)', flush=True)
    while time.monotonic() < deadline:
        try:
            status = json.loads(Path('/run/mr2-clock/status.json').read_text())
            if (status.get('relative_ready') and status.get('base_locked')
                    and status.get('base_peer') == peer
                    and isinstance(status.get('clock_error_bound_s'), (int, float))
                    and 0 <= status['clock_error_bound_s'] <= .0012
                    and status.get('clock_drift_bound_ppm', float('inf')) <= 15
                    and 0 <= time.monotonic()-status['updated_monotonic'] < 5):
                print('[clock] Relative time ready', flush=True)
                return True
        except (OSError, ValueError, KeyError, TypeError):
            pass
        time.sleep(.5)
    print('[clock] Warmup ended; measurement clock will continue to be checked', flush=True)
    return False


def acquire_if_real(context):
    from launch.substitutions import LaunchConfiguration
    if LaunchConfiguration('mode').perform(context) != 'real':
        return []
    return acquire_clock(context)


def video_base_host_default():
    """Use this launch's acknowledged base; explicit video_base_host still wins."""
    from launch.substitutions import EnvironmentVariable, LaunchConfiguration
    return LaunchConfiguration(
        'mr2_clock_base_peer',
        default=EnvironmentVariable('MR2_BASE_IP', default_value='127.0.0.1'),
    )
