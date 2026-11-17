import copy
import time
import unittest

from scripts.latency.preflight import clock_ready, compare_reports, stream_map, validate_gateway


class PreflightTest(unittest.TestCase):
    def setUp(self):
        self.streams = {f'flat_cam_{i}': 5008 + 2 * i for i in range(1, 5)}
        self.clock = dict(available=True, synchronized=True,
                          sampled_at_epoch_ms=time.time() * 1000,
                          system_time_offset_s=.0001, root_delay_s=.0002,
                          root_dispersion_s=.0001)
        self.status = dict(schema_version=1, configured=True, missing=[],
                           capture_interface='eth0', public_base_url='http://192.0.2.1',
                           streams=[dict(stream_id=k, udp_port=v) for k, v in self.streams.items()],
                           video_streams=[dict(stream_id=k, available=True) for k in self.streams],
                           clock=self.clock)

    def test_flat_runtime_matches(self):
        validate_gateway(self.status, self.streams, 'base', 'eth0')
        # Rover has a different local capture interface from the base.
        validate_gateway(self.status, self.streams, 'rover', 'eth1')

    def test_original_ids_on_same_ports_are_rejected(self):
        self.status['streams'][0]['stream_id'] = 'front_left_cam'
        with self.assertRaisesRegex(ValueError, 'IDs/ports'):
            validate_gateway(self.status, self.streams, 'base', 'eth0')

    def test_wrong_nic_disabled_diagnostics_and_missing_feed(self):
        for key, value in [('capture_interface', 'lo'), ('configured', False), ('video_streams', [])]:
            status = copy.deepcopy(self.status)
            status[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                validate_gateway(status, self.streams, 'base', 'eth0')

    def test_clocks_reject_stale_nan_and_large_error(self):
        for key, value in [('sampled_at_epoch_ms', 0), ('system_time_offset_s', float('nan')),
                           ('system_time_offset_s', .01), ('root_dispersion_s', .003)]:
            clock = {**self.clock, key: value}
            with self.subTest(key=key), self.assertRaises(ValueError):
                clock_ready(clock, 'rover')

    def test_duplicate_ports_rejected(self):
        with self.assertRaisesRegex(ValueError, 'duplicate UDP'):
            stream_map([dict(stream_id='a', udp_port=5010), dict(stream_id='b', udp_port=5010)])

    def test_reports_require_same_revision_and_full_config(self):
        base = dict(role='base', passed=True, commit='abc', video_config_sha256='sha', streams=self.streams)
        rover = {**base, 'role': 'rover'}
        self.assertEqual(compare_reports(base, rover), [])
        for key, value in [('commit', 'def'), ('video_config_sha256', 'other'), ('passed', False)]:
            with self.subTest(key=key):
                self.assertTrue(compare_reports(base, {**rover, key: value}))


if __name__ == '__main__':
    unittest.main()
