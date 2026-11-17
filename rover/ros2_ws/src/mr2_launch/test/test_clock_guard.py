"""The shared launch guard waits once, and clock warmup has a bounded timeout."""
import json
from unittest.mock import patch

from launch import LaunchContext
from mr2_launch.clock_guard import acquire_clock, wait_for_relative_clock


def test_nested_launches_only_wait_once_for_the_same_session():
    response = dict(ok=True, locked=True, id='test', base_peer='192.168.1.101')
    with patch('mr2_launch.clock_guard.socket.socket') as sock, \
            patch('mr2_launch.clock_guard.wait_for_relative_clock') as wait, \
            patch.dict('os.environ', {}, clear=True):
        sock.return_value.__enter__.return_value.makefile.return_value.readline.return_value = json.dumps(response)
        acquire_clock(LaunchContext())
        acquire_clock(LaunchContext())
        wait.assert_called_once_with('192.168.1.101')


def test_warmup_requires_headroom_and_the_acknowledged_peer():
    status = dict(relative_ready=True, base_locked=True, base_peer='192.168.1.101',
                  clock_error_bound_s=.001, clock_drift_bound_ppm=10,
                  updated_monotonic=99.)
    with patch('mr2_launch.clock_guard.Path.read_text', return_value=json.dumps(status)), \
            patch('mr2_launch.clock_guard.time.monotonic', return_value=100.):
        assert wait_for_relative_clock('192.168.1.101')
    for changes in (dict(base_peer='192.168.0.55'), dict(clock_error_bound_s=.0019),
                    dict(clock_drift_bound_ppm=30), dict(updated_monotonic=0.),
                    dict(relative_ready=False)):
        with patch('mr2_launch.clock_guard.Path.read_text', return_value=json.dumps({**status, **changes})), \
                patch('mr2_launch.clock_guard.time.monotonic', side_effect=[100., 100., 100., 191.]), \
                patch('mr2_launch.clock_guard.time.sleep'):
            assert not wait_for_relative_clock('192.168.1.101')


def test_unavailable_policy_still_allows_start_after_warmup_timeout():
    with patch('mr2_launch.clock_guard.Path.read_text', side_effect=OSError('unavailable')), \
            patch('mr2_launch.clock_guard.time.monotonic', side_effect=[0., 0., 90.]), \
            patch('mr2_launch.clock_guard.time.sleep'):
        assert not wait_for_relative_clock('192.168.1.101')
