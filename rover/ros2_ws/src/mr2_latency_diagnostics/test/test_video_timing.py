import json
import socket
import time
import unittest
import uuid

from mr2_latency_diagnostics.video_timing import VideoTimingCollector


class VideoTimingCollectorTest(unittest.TestCase):
    def test_filters_bounds_and_releases_socket(self):
        address = '\0mr2-timing-test-' + uuid.uuid4().hex
        with VideoTimingCollector(['front'], address) as collector:
            collector.MAX_SAMPLES = 2
            with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as sender:
                for stream in ['rear', 'front', 'front', 'front']:
                    sender.sendto(json.dumps({'schema_version':1,'stream_id':stream}).encode(), address)
                sender.sendto(b'invalid-json', address)
            deadline = time.monotonic() + 2
            while collector.invalid_samples == 0 and time.monotonic() < deadline:
                time.sleep(.01)
        self.assertEqual(len(collector.samples), 2)
        self.assertEqual(collector.dropped_samples, 1)
        self.assertEqual(collector.invalid_samples, 1)
        with VideoTimingCollector(['front'], address):
            pass

    def test_does_not_steal_active_measurement(self):
        address = '\0mr2-timing-test-' + uuid.uuid4().hex
        with VideoTimingCollector(['front'], address):
            with self.assertRaises(OSError):
                with VideoTimingCollector(['front'], address):
                    pass
