import subprocess
import unittest
from unittest.mock import patch

from mr2_system_status.chrony_status import (
    parse_chronyc_tracking,
    read_chrony_status,
    require_measurement_clock,
)


TRACKING = """Reference ID    : C0A8010A (192.168.1.10)
Stratum         : 9
System time     : 0.000250000 seconds fast of NTP time
Last offset     : 0.000020000 seconds
RMS offset      : 0.000030000 seconds
Root delay      : 0.000500000 seconds
Root dispersion : 0.000300000 seconds
Update interval : 4.0 seconds
Leap status     : Normal
"""


class ChronyStatusTest(unittest.TestCase):
    def test_measurement_rejects_external_source_unlocked_or_large_error(self):
        good = dict(available=True, synchronized=True, policy_available=True,
                    policy_locked=True, base_locked=True, relative_ready=True, base_peer='192.168.1.101',
                    selected_source='192.168.1.101', system_time_offset_s=.0001,
                    root_delay_s=.0002, root_dispersion_s=.0001)
        with patch('mr2_system_status.chrony_status.read_chrony_status', return_value=good):
            require_measurement_clock()
        for change in ({'policy_locked': False}, {'base_locked': False},
                       {'selected_source': '1.2.3.4'}, {'system_time_offset_s': .01},
                       {'root_dispersion_s': float('nan')}, {'relative_ready': False}):
            with patch('mr2_system_status.chrony_status.read_chrony_status', return_value={**good, **change}):
                with self.assertRaises(RuntimeError):
                    require_measurement_clock()

        for change in ({'clock_error_bound_s': .003},
                       {'clock_error_bound_s': float('nan')}, {'clock_error_bound_s': -1},
                       {'system_time_offset_s': .001, 'root_dispersion_s': .001}):
            with patch('mr2_system_status.chrony_status.read_chrony_status', return_value={**good, **change}):
                with self.assertRaises(RuntimeError):
                    require_measurement_clock()

    def test_parse_chronyc_tracking(self) -> None:
        status = parse_chronyc_tracking(TRACKING, 1234)
        self.assertIs(status["available"], True)
        self.assertIs(status["synchronized"], True)
        self.assertEqual(status["role"], "rover")
        self.assertEqual(status["reference_id"], "C0A8010A")
        self.assertEqual(status["reference_name"], "192.168.1.10")
        self.assertEqual(status["stratum"], 9)
        self.assertEqual(status["system_time_offset_s"], 0.00025)
        self.assertEqual(status["root_dispersion_s"], 0.0003)
        self.assertEqual(status["sampled_at_epoch_ms"], 1234)

    def test_slow_and_unsynchronized_signs(self) -> None:
        status = parse_chronyc_tracking(
            TRACKING.replace("0.000250000 seconds fast", "0.000125000 seconds slow")
            .replace("Stratum         : 9", "Stratum         : 0")
            .replace("Leap status     : Normal", "Leap status     : Not synchronised")
        )
        self.assertEqual(status["system_time_offset_s"], -0.000125)
        self.assertIs(status["synchronized"], False)

    def test_local_reference_without_hostname(self) -> None:
        for reference in ("7F7F0101 ()", "7F7F0101"):
            status = parse_chronyc_tracking(
                TRACKING.replace("C0A8010A (192.168.1.10)", reference)
                .replace("Stratum         : 9", "Stratum         : 8")
            )
            self.assertIs(status["synchronized"], True)
            self.assertEqual(status["reference_id"], "7F7F0101")
            self.assertIsNone(status["reference_name"])

    def test_read_uses_allow_listed_command(self) -> None:
        calls = []

        def runner(command, **kwargs):
            calls.append((command, kwargs))
            return subprocess.CompletedProcess(command, 0, TRACKING, "")

        status = read_chrony_status(runner=runner)
        self.assertIs(status["available"], True)
        self.assertEqual(calls[0][0], ["chronyc", "-n", "tracking"])
        self.assertEqual(calls[0][1]["timeout"], 1.5)

    def test_missing_chronyc_is_structured(self) -> None:
        def runner(*_args, **_kwargs):
            raise FileNotFoundError

        status = read_chrony_status(runner=runner)
        self.assertIs(status["available"], False)
        self.assertEqual(status["error"], "chronyc is not installed")


if __name__ == "__main__":
    unittest.main()
