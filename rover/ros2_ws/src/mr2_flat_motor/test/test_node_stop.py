"""Shutdown must send zero velocity even after all receive feedback is lost."""
from types import SimpleNamespace
from unittest.mock import Mock
import struct

import pytest

pytest.importorskip('rclpy')
from mr2_flat_motor.node import FlatMotor


def test_shutdown_sends_zero_after_feedback_loss():
    node = SimpleNamespace(control=SimpleNamespace(engaged=True),
                           motor_id=104, can=Mock(), lock=Mock())
    FlatMotor.close(node)
    cid, dlc, data = struct.unpack('=IB3x8s', node.can.send.call_args.args[0])
    assert cid == 0x80000368 and dlc == 4 and data == bytes(8)
    node.can.close.assert_called_once()
    node.lock.close.assert_called_once()


def test_shutdown_before_any_motion_sends_nothing():
    node = SimpleNamespace(control=SimpleNamespace(engaged=False),
                           motor_id=104, can=Mock(), lock=Mock())
    FlatMotor.close(node)
    node.can.send.assert_not_called()
