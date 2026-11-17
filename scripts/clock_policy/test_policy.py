import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from policy import Locks, process_identity, render, session_alive, peer_sample, field_base_for_addresses
from service import Manager

BASE = dict(role='base', allow=['192.168.1.102/32', '192.168.0.0/24'],
            base_hosts=['192.168.1.101', '192.168.0.62'], ssh_user='mr2')
ROVER = {**BASE, 'role': 'rover'}


class PolicyTests(unittest.TestCase):
    def test_field_detection_and_development_fallback(self):
        config = {**ROVER, 'field_network': dict(interface='enP8p1s0',
                  local_address='192.168.1.102', base_host='192.168.1.101')}
        def address(interface, ip):
            return {'ifname': interface, 'addr_info': [{'family': 'inet', 'local': ip}]}
        self.assertEqual(field_base_for_addresses(config, [address('enP8p1s0', '192.168.1.102'),
                         address('wlan0', '192.168.0.57')]), '192.168.1.101')
        for addresses in ([], [address('enP8p1s0', '192.168.0.59')],
                          [address('wlan0', '192.168.1.102')]):
            self.assertIsNone(field_base_for_addresses(config, addresses))
        self.assertIsNone(field_base_for_addresses({**config, 'role': 'base'},
                          [address('enP8p1s0', '192.168.1.102')]))

    def test_field_idle_uses_only_base_but_locked_still_never_steps(self):
        text = render(ROVER, False, idle_base='192.168.1.101')
        self.assertIn('server 192.168.1.101 ', text)
        self.assertNotIn('192.168.0.62', text)
        self.assertNotIn('pool ', text)
        self.assertIn('makestep 0.1 -1', text)
        subprocess.run(['chronyd', '-p', *text.splitlines()], check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        for peer in (None, '192.168.1.101'):
            self.assertEqual(render(ROVER, True, peer, idle_base='192.168.1.101'),
                             render(ROVER, True, peer))
        self.assertIn('pool ', render(ROVER, False))
        self.assertIn('pool ', render(BASE, False, idle_base='192.168.1.101'))

    def test_manager_rechecks_network_idle_and_keeps_locked_profile(self):
        config = {**ROVER, 'field_network': dict(interface='enP8p1s0',
                  local_address='192.168.1.102', base_host='192.168.1.101')}
        manager = Manager(config)
        addresses = [{'ifname': 'enP8p1s0', 'addr_info': [
            {'family': 'inet', 'local': '192.168.1.102'}]}]
        def fake_run(args):
            return json.dumps(addresses) if args[0] == 'ip' else ''
        with patch('service.run', side_effect=fake_run), patch('service.atomic_write') as write:
            manager.apply(False)
            self.assertNotIn('pool ', write.call_args.args[1])
            addresses.clear()
            manager.apply(False)
            self.assertIn('pool ', write.call_args.args[1])
            manager.apply(True, '192.168.1.101')
            self.assertNotIn('pool ', write.call_args.args[1])
            self.assertNotIn('makestep', write.call_args.args[1])

    def test_stale_large_or_nonselected_peer_sample_blocks_measurement(self):
        line = '^,*,192.168.1.101,8,2,377,4,0.00001,0.00001,0.00013'
        self.assertTrue(peer_sample(line, '192.168.1.101')['relative_ready'])
        for bad in (line.replace(',4,', ',40,'), line.replace(',*,', ',?,'),
                    line.replace('0.00001', '0.1'), line.replace('0.00013', 'nan')):
            self.assertFalse(peer_sample(bad, '192.168.1.101')['relative_ready'])

    def test_profiles_are_valid_chrony_and_running_cannot_step(self):
        for config in (BASE, ROVER):
            for locked in (False, True):
                text = render(config, locked, '192.168.1.101')
                subprocess.run(['chronyd', '-p', *text.splitlines()], check=True,
                               stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
                if locked:
                    self.assertNotIn('pool ', text)
                    self.assertNotIn('makestep', text)
                    self.assertIn('maxchange 0.05 0 -1', text)
                    self.assertIn('maxslewrate 100', text)
                else:
                    self.assertIn('pool ', text)
                    self.assertIn('makestep 0.1 -1', text)
                self.assertNotIn(' trust', text)

    def test_locked_standalone_rover_has_no_sources(self):
        text = render(ROVER, True)
        self.assertNotIn('server ', text)
        self.assertNotIn('pool ', text)

    def test_lock_lifecycle_multiple_pids_and_remote_expiry(self):
        locks = Locks('boot')
        locks.local = {'a': {'pid': 10}, 'b': {'pid': 11}}
        locks.remote = {'c': 30}
        locks.prune(20, lambda s: s['pid'] == 11)
        self.assertTrue(locks.locked)
        locks.prune(25, lambda s: False)
        self.assertTrue(locks.locked)
        locks.prune(30, lambda s: False)
        self.assertFalse(locks.locked)

    def test_restart_keeps_same_boot_locks_but_not_previous_boot(self):
        locks = Locks('one')
        locks.remote = {'a': 123}
        self.assertTrue(Locks('one', locks.data()).locked)
        self.assertFalse(Locks('two', locks.data()).locked)

    def test_pid_reuse_does_not_keep_session(self):
        session = dict(pid=os.getpid(), start='wrong-start-time', uid=os.getuid(), id='test-nonexistent')
        self.assertFalse(session_alive(session))
        session['start'] = process_identity(os.getpid())
        self.assertTrue(session_alive(session))

    def test_orphan_child_keeps_lock_until_exit(self):
        child = subprocess.Popen(['sleep', '10'], env={**os.environ, 'MR2_CLOCK_SESSION': 'orphan-test'})
        session = dict(pid=99999999, start='0', uid=os.getuid(), id='orphan-test')
        try:
            self.assertTrue(session_alive(session))
        finally:
            child.terminate()
            child.wait()
        self.assertFalse(session_alive(session))

    def manager(self):
        manager = Manager(ROVER)
        manager.locks.local = {'x': dict(id='x', pid=os.getpid(), start=process_identity(os.getpid()), uid=os.getuid())}
        manager.save = lambda: None
        manager.publish = lambda: None
        manager.applied_calls = []
        manager.apply = lambda locked, peer=None: manager.applied_calls.append((locked, peer))
        return manager

    def test_base_ack_required_before_enabling_source(self):
        manager = self.manager()
        manager.remote = lambda *a: (_ for _ in ()).throw(RuntimeError('offline'))
        manager.reconcile(force=True)
        self.assertIsNone(manager.peer)
        self.assertTrue(all(peer is None for _, peer in manager.applied_calls))

    def test_renewal_failure_preserves_only_unexpired_acknowledged_lease(self):
        manager = self.manager()
        manager.peer = '192.168.0.62'
        manager.peer_until = time.monotonic() + 60
        manager.remote = lambda *a: (_ for _ in ()).throw(RuntimeError('offline'))
        manager.reconcile(force=True)
        self.assertEqual(manager.applied_calls[-1], (True, '192.168.0.62'))
        manager.peer_until = time.monotonic() + 19
        manager.reconcile(force=True)
        self.assertIsNone(manager.peer)
        self.assertEqual(manager.applied_calls[-1], (True, None))

    def test_peer_offline_online_preserves_daemon_and_source(self):
        manager = Manager(ROVER)
        with patch('service.run') as command, patch('service.atomic_write'):
            manager.apply(True, '192.168.1.101')
            command.reset_mock()
            manager.apply(True, None)
            manager.apply(True, '192.168.1.101')
            calls = [c.args[0] for c in command.call_args_list]
            self.assertIn(['chronyc', 'offline', '192.168.1.101'], calls)
            self.assertIn(['chronyc', 'online', '192.168.1.101'], calls)
            self.assertFalse(any(c[0] == 'systemctl' for c in calls))

    def test_successful_lease_enables_only_acknowledged_base(self):
        manager = self.manager()
        manager.remote = lambda *a: {'ok': True, 'locked': True}
        manager.reconcile(force=True)
        self.assertEqual(manager.applied_calls[0], (True, None))
        self.assertEqual(manager.applied_calls[-1], (True, '192.168.1.101'))


class ExplicitBaseTests(unittest.TestCase):
    def test_prepare_is_requested_only_before_the_first_launch(self):
        manager = Manager(ROVER)
        manager.save = lambda: None
        manager.reconcile = lambda **kwargs: setattr(manager, 'peer', '192.168.1.101')
        with patch.object(manager, 'prepare_clock') as prepare:
            request = {'op': 'acquire', 'prepare': True}
            manager.request(request, os.getpid(), os.getuid())
            manager.request(request, os.getpid(), os.getuid())
            prepare.assert_called_once()

    def test_development_override_is_session_scoped_and_does_not_rewrite_field_defaults(self):
        manager = Manager(ROVER)
        manager.locks.local = {'x': dict(id='x', pid=os.getpid(),
            start=process_identity(os.getpid()), uid=os.getuid(), base_host='192.168.0.54')}
        manager.save = lambda: None
        manager.publish = lambda: None
        manager.apply = lambda *args: None
        seen = []
        manager.remote = lambda host, op: seen.append(host) or {'ok': True, 'locked': True}
        manager.reconcile(force=True)
        self.assertEqual(seen, ['192.168.0.54'])
        self.assertEqual(manager.peer, '192.168.0.54')
        self.assertEqual(ROVER['base_hosts'][0], '192.168.1.101')

    def test_conflicting_destination_is_rejected_without_changing_session(self):
        manager = Manager(ROVER)
        manager.locks.local = {'other': dict(pid=os.getpid() + 1, base_host='192.168.0.54')}
        with self.assertRaisesRegex(ValueError, 'different base'):
            manager.request({'op': 'acquire', 'base_host': '192.168.0.55'}, os.getpid(), os.getuid())
        self.assertEqual(list(manager.locks.local), ['other'])


if __name__ == '__main__':
    unittest.main()
