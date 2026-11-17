"""Bounded per-trial receiver for passive video sender timing probes."""

import json
import os
import socket
import threading


class VideoTimingCollector:
    MAX_SAMPLES = 2000  # Fits the existing 2 MiB metadata upload limit.

    def __init__(self, stream_ids, address=None):
        self.stream_ids = set(stream_ids)
        self.address = address or f'\0mr2-video-timing-{os.getuid()}'
        self.samples = []
        self.dropped_samples = 0
        self.invalid_samples = 0
        self._stop = threading.Event()
        self._socket = None
        self._thread = None

    def __enter__(self):
        self._socket = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        try:
            self._socket.bind(self.address)
            self._socket.settimeout(0.1)
        except OSError:
            self._socket.close()
            raise
        self._thread = threading.Thread(target=self._receive, daemon=True)
        self._thread.start()
        return self

    def _receive(self):
        while not self._stop.is_set():
            try:
                raw = self._socket.recv(2048)
            except socket.timeout:
                continue
            try:
                sample = json.loads(raw)
                if not isinstance(sample, dict) or sample.get('schema_version') != 1:
                    raise ValueError('invalid timing schema')
                if sample.get('stream_id') not in self.stream_ids:
                    continue
                if len(self.samples) >= self.MAX_SAMPLES:
                    self.dropped_samples += 1
                    continue
                self.samples.append(sample)
            except (ValueError, TypeError):
                self.invalid_samples += 1

    def __exit__(self, *_args):
        self._stop.set()
        self._thread.join()
        self._socket.close()

    def metadata(self):
        return {
            'schema_version': 1,
            'ingest_boundary': 'gstreamer_source_output',
            'samples': self.samples,
            'dropped_samples': self.dropped_samples,
            'invalid_samples': self.invalid_samples,
        }
