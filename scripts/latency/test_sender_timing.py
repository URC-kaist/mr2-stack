"""Same-frame sender timing must never be inferred from unrelated frames."""
import unittest

from scripts.latency.analyze_rtp_latency import (
    MatchedPacket, build_automated_uplink_report, sender_timing_by_key,
)


class SenderTimingTest(unittest.TestCase):
    def sample(self, sequence=10, **overrides):
        return dict(schema_version=1, stream_id='front', ssrc=42,
                    marker_sequence=sequence, rtp_timestamp=1000,
                    ingest_epoch_us=980_000, pay_epoch_us=999_900,
                    ingest_to_pay_us=19_900, encode_us=5_000, **overrides)

    def report(self, samples=None):
        link = {'clock_offset_us': 100, 'warnings': [], 'captures': {}, 'streams': [{
            'stream_id': 'front', 'udp_port': 5000, 'matched_packets': 1,
            'loss_percent': 0, 'rover_offered_bitrate_bps': 1000,
            'base_delivered_bitrate_bps': 1000}]}
        packets = [MatchedPacket('front',5000,42,10,1000,True,1_000_000,1_001_100,1000,100)]
        browser = [dict(stream_id='front',ssrc=42,marker_sequence=10,rtp_timestamp=1000,
                        browser_receive_epoch_us=1_009_000,browser_render_epoch_us=1_010_000)]
        timing = None if samples is None else {'schema_version':1,'samples':samples}
        return build_automated_uplink_report(link, packets, browser, 100, 'test', 1, timing)

    def test_same_frame_segments_and_encoder_is_a_subset(self):
        report = self.report([self.sample()])
        segments = report['aggregate']
        self.assertEqual(segments['total']['p50'], 10_000)
        self.assertEqual(segments['source_to_rtp']['p50'], 20_000)
        self.assertEqual(segments['h264_encode']['p50'], 5_000)
        self.assertEqual(segments['source_to_render']['p50'], 30_000)
        self.assertEqual(segments['source_to_render']['count'], 1)

    def test_missing_wrong_or_duplicate_key_is_unavailable_not_zero(self):
        for samples in (None, [], [self.sample(11)], [self.sample(), self.sample()]):
            with self.subTest(samples=samples):
                report = self.report(samples)
                self.assertEqual(report['aggregate']['total']['count'], 1)
                self.assertEqual(report['aggregate']['source_to_render']['count'], 0)
                self.assertIsNone(report['aggregate']['source_to_render']['p50'])
                self.assertTrue(report['warnings'])

    def test_rejects_nonfinite_negative_clock_jump_and_wrong_order(self):
        for field, value in [('encode_us',float('nan')), ('encode_us',-1),
                             ('encode_us',30_000), ('pay_epoch_us',1_100_000),
                             ('marker_sequence',65536), ('ssrc',True)]:
            sample = self.sample()
            sample[field] = value
            result, warnings = sender_timing_by_key({'schema_version':1,'samples':[sample]})
            self.assertFalse(result)
            self.assertTrue(warnings)

    def test_rejects_probe_after_actual_transmission(self):
        sample = self.sample()
        sample.update(pay_epoch_us=1_000_100, ingest_to_pay_us=20_100)
        self.assertEqual(self.report([sample])['aggregate']['source_to_rtp']['count'], 0)


if __name__ == '__main__':
    unittest.main()
