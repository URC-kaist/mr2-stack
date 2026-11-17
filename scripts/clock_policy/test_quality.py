import unittest
from policy import ClockQuality, clock_error, can_prepare_step


class QualityTests(unittest.TestCase):
    def track(self, **changes):
        return {'System time': '0.0001 seconds fast', 'Root delay': '0.001 seconds',
                'Root dispersion': '0.0002 seconds', 'Skew': '1 ppm',
                'Residual freq': '0 ppm', 'Leap status': 'Normal',
                'Ref time (UTC)': 'one', **changes}

    def established(self):
        quality = ClockQuality()
        for now in [0., 2., 4.]:
            value = quality.update(self.track(), {'peer_sample_age_s': now}, True, now, now+1000)
        self.assertTrue(value['relative_ready'])
        return quality

    def test_no_recent_sample_can_establish_clock(self):
        value = ClockQuality().update(self.track(), {'peer_sample_age_s': None}, True, 0, 1000)
        self.assertFalse(value['relative_ready'])

    def test_holdover_expires_from_error_not_a_fixed_twenty_seconds(self):
        quality = self.established()
        value = quality.update(self.track(), {'peer_sample_age_s': 43}, True, 43, 1043)
        self.assertTrue(value['relative_ready'])
        self.assertEqual(value['clock_state'], 'holdover')
        self.assertAlmostEqual(value['clock_error_bound_s'], .00123)
        value = quality.update(self.track(), {'peer_sample_age_s': 150}, True, 150, 1150)
        self.assertFalse(value['relative_ready'])
        self.assertGreater(value['clock_error_bound_s'], .002)

    def test_lost_lease_large_error_nan_and_unsynchronized_fail_closed(self):
        for track, follows in [(self.track(), False),
                (self.track(**{'System time': '0.003 seconds fast'}), True),
                (self.track(**{'Root dispersion': 'nan'}), True),
                (self.track(**{'Leap status': 'Not synchronised'}), True)]:
            value = self.established().update(track, {'peer_sample_age_s': 5}, follows, 5, 1005)
            self.assertFalse(value['relative_ready'])

    def test_step_invalidates_old_fit_until_new_reference(self):
        quality = self.established()
        value = quality.update(self.track(), {'peer_sample_age_s': 5}, True, 5, 1005.01)
        self.assertFalse(value['relative_ready'])
        self.assertTrue(value['clock_jump'])
        value = quality.update(self.track(), {'peer_sample_age_s': 6}, True, 6, 1006.01)
        self.assertFalse(value['relative_ready'])
        for now in [7, 8, 9]:
            value = quality.update(self.track(**{'Ref time (UTC)': 'two'}),
                                   {'peer_sample_age_s': now-7}, True, now, 1000.01+now)
        self.assertTrue(value['relative_ready'])

    def test_total_error_includes_system_correction(self):
        self.assertAlmostEqual(clock_error(self.track()), .0008)
        self.assertIsNone(clock_error(self.track(**{'Root dispersion': '-1'})))
        self.assertIsNone(clock_error(self.track(**{'Root delay': 'nan'})))

    def test_fresh_snapshot_does_not_count_existing_sample_age_twice(self):
        q = ClockQuality()
        track = self.track(**{'Root dispersion': '0.0012 seconds'})
        result = q.update(track, {'peer_sample_age_s': 8}, True, 100, 1100)
        self.assertAlmostEqual(result['clock_error_bound_s'], .0018)
        for now in [102, 104]:
            result = q.update(track, {'peer_sample_age_s': 8}, True, now, now+1000)
        self.assertTrue(result['relative_ready'])
        self.assertLess(result['clock_error_bound_s'], .002)

    def test_new_filtered_fit_is_usable_even_if_its_packet_is_older(self):
        q = self.established()
        for now in [30, 32, 34]:
            value = q.update(self.track(**{'Ref time (UTC)': 'filtered'}),
                             {'peer_sample_age_s': 25+now-30}, True, now, now+1000)
        self.assertTrue(value['relative_ready'])
        self.assertEqual(value['clock_state'], 'fresh')

    def test_preparation_requires_correct_peer_bounded_offset_and_precise_sample(self):
        track = self.track(**{'Reference ID': 'C0A80165 (192.168.1.101)',
                             'System time': '0.005 seconds fast'})
        sample = {'peer_sample_age_s': 1, 'peer_sample_error_s': .001}
        self.assertTrue(can_prepare_step(track, sample, '192.168.1.101'))
        for changed, data, peer in [
            (track, sample, '192.168.0.55'),
            ({**track, 'System time': '.1 seconds fast'}, sample, '192.168.1.101'),
            (track, {**sample, 'peer_sample_age_s': 30}, '192.168.1.101'),
            (track, {**sample, 'peer_sample_error_s': .01}, '192.168.1.101')]:
            self.assertFalse(can_prepare_step(changed, data, peer))


if __name__ == '__main__':
    unittest.main()
