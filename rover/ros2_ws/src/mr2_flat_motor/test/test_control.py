"""Exercise continuous rotation, neutral gating and active stop behavior."""
import math
import struct

import pytest

from mr2_flat_motor.control import MotorControl, velocity_frame


def feedback(control, now, position=146.3, current=0, error=0, velocity=0):
    control.feedback(struct.pack('>hhhBB', round(position * 10), round(velocity * 14 / 10),
                                round(current * 100), 30, error), now)


def initialized(**kwargs):
    control = MotorControl(**kwargs)
    for i in range(26):
        feedback(control, i * .02)
    assert control.ready(.5)
    return control


def arm(control, start=.5):
    for i in range(17):
        now = start + i * .02
        feedback(control, now)
        control.command(0, now)
    assert control.armed


def moving():
    control = initialized()
    arm(control)
    control.command(1, .82)
    assert control.step(.84, .02) < 0
    return control


def test_startup_and_held_trigger_cannot_start_motor():
    control = MotorControl()
    control.command(1, 0)
    assert control.step(0, .02) is None
    control = initialized()
    control.command(1, .5)
    assert not control.armed
    assert control.step(.5, .02) is None
    arm(control)
    assert control.step(.82, .02) is None  # Neutral alone does not drive the motor.


@pytest.mark.parametrize('value,sign,expected', [
    (1, -1, -180), (-1, -1, 180), (1, 1, 180), (-1, 1, -180),
    (10, -1, -180), (.5, -1, -90), (.25, -1, -45),
])
def test_speeds_and_direction_match_existing_dashboard(value, sign, expected):
    control = initialized(clockwise_sign=sign)
    arm(control)
    for i in range(1, 21):
        now = .82 + i * .02
        feedback(control, now)
        control.command(value, now)
        output = control.step(now, .02)
        assert abs(output) <= abs(expected)
    assert output == pytest.approx(expected)


@pytest.mark.parametrize('value', [0, .02, -.02])
def test_deadzone_does_not_engage(value):
    control = initialized()
    arm(control)
    control.command(value, .82)
    assert control.step(.84, .02) is None


def test_rotation_passes_old_bounds_and_feedback_wraps_without_position_command():
    control = moving()
    for index, position in enumerate([199, 201, 359, 361, 720, 3200, 3276.7, -3276.8, -201]):
        now = .86 + index * .02
        feedback(control, now, position=position)
        control.command(1, now)
        assert control.step(now, .02) < 0
        assert control.ready(now)
    assert control.status(now)['angle_limits_enabled'] is False


def test_neutral_stops_immediately_without_acceleration_tail_or_position_hold():
    control = moving()
    control.command(0, .86)
    assert control.step(.86, .02) == 0
    feedback(control, .88, position=800)
    control.command(0, .88)
    assert control.step(.88, .02) == 0


def test_watchdog_with_fresh_feedback_disarms_and_repeats_zero_speed():
    control = moving()
    for now in (.92, 1.02, 1.12):
        feedback(control, now)
    assert control.step(1.12, .02) == 0
    assert not control.armed
    control.command(1, 1.13)
    assert control.step(1.13, .02) == 0
    assert not control.armed


def test_late_command_before_timer_cannot_bypass_timeout():
    control = initialized()
    arm(control)
    for now in (.92, 1.02, 1.12):
        feedback(control, now)
    control.command(1, 1.12)
    assert not control.armed
    assert control.step(1.12, .02) is None


def test_feedback_loss_sends_stop_and_recovery_cannot_resume_old_speed():
    control = moving()
    assert control.step(1.04, .02) == 0
    assert 'timeout' in control.fault
    assert control.step(1.06, .02) == 0
    for i in range(26):
        feedback(control, 1.1 + i * .02)
    control.command(1, 1.6)
    assert control.step(1.6, .02) == 0
    arm(control, start=1.6)
    assert control.ready(1.92)
    assert control.step(1.92, .02) == 0


@pytest.mark.parametrize('kwargs', [{'current': 3.1}, {'error': 2}])
def test_fault_during_motion_sends_zero(kwargs):
    control = moving()
    feedback(control, .86, **kwargs)
    assert control.step(.86, .02) == 0
    assert control.step(.88, .02) == 0
    assert control.fault and not control.armed


@pytest.mark.parametrize('value', [math.nan, math.inf, -math.inf])
def test_nonfinite_commands_stop(value):
    control = moving()
    control.command(value, .86)
    assert control.fault
    assert control.step(.86, .02) == 0


def test_slew_and_late_tick_are_bounded_and_reversal_crosses_zero():
    control = initialized()
    arm(control)
    control.command(1, .82)
    assert control.step(.84, 10) == pytest.approx(-720 * .04)
    control.command(-1, .84)
    assert control.step(.86, .02) == pytest.approx(-14.4)
    assert control.step(.88, .02) == pytest.approx(0)
    assert control.step(.90, .02) == pytest.approx(14.4)


def test_moving_feedback_cannot_establish_startup_reference():
    control = MotorControl()
    for i in range(40):
        feedback(control, i * .02, velocity=10)
    assert not control.ready(.8)
    assert control.step(.8, .02) is None


@pytest.mark.parametrize('speed,erpm', [(180,2520),(-180,-2520),(0,0),(45,630)])
def test_velocity_wire_format_and_ak606_electrical_rpm_conversion(speed, erpm):
    cid, dlc, data = struct.unpack('=IB3x8s', velocity_frame(104, speed))
    assert cid == 0x80000368 and dlc == 4
    assert struct.unpack('>i', data[:4])[0] == erpm


def test_dashboard_estop_latches_despite_neutral_and_new_commands():
    control = moving()
    control.emergency_stop()
    assert control.step(.84, .02) == 0
    for i in range(30):
        now = .86 + i * .02
        feedback(control, now)
        control.command(0, now)
        assert control.step(now, .02) == 0
    control.command(1, 1.44)
    assert not control.armed and control.stop_latched


@pytest.mark.parametrize('kwargs', [{'speed':0},{'speed':721},{'speed':math.nan},{'acceleration':0},{'clockwise_sign':0}])
def test_configuration_rejects_invalid_motion_parameters(kwargs):
    with pytest.raises(ValueError):
        MotorControl(**kwargs)
