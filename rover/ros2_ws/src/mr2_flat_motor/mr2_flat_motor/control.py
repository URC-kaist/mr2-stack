"""CAN-independent continuous-speed controller. Times use a monotonic clock."""

import math
import struct


# AK60-6 V3: 14 pole pairs, 6:1 gearbox. Output deg/s -> electrical RPM.
ERPM_PER_DEG_S = 14.0 * 6.0 / 6.0


def velocity_frame(motor_id, degrees_per_second):
    if not math.isfinite(degrees_per_second):
        raise ValueError('Velocity must be finite')
    erpm = round(degrees_per_second * ERPM_PER_DEG_S)
    if abs(erpm) > 100000:
        raise ValueError('Velocity outside the documented servo ERPM range')
    return struct.pack('=IB3x8s', 0x80000300 | motor_id, 4,
                       struct.pack('>i', erpm))


class MotorControl:
    def __init__(self, *, speed=180.0, clockwise_sign=-1, acceleration=720.0):
        if (not all(math.isfinite(x) for x in (speed, acceleration))
                or not 0 < speed <= 720 or not 0 < acceleration <= 10000
                or clockwise_sign not in (-1, 1)):
            raise ValueError('Invalid speed, acceleration or direction')
        self.speed, self.clockwise_sign = speed, clockwise_sign
        self.acceleration = acceleration
        self.position = None
        self.measured_velocity = 0.0
        self.current = self.temperature = self.motor_error = 0
        self.last_feedback = self.last_command = -math.inf
        self.stable_since = None
        self.stable_position = None
        self.initialized = self.armed = self.engaged = False
        self.fault = ''
        self.velocity = self.output_velocity = 0.0
        self.samples = 0
        self.neutral_since = None
        self.stop_latched = False

    def ready(self, now):
        return self.initialized and now - self.last_feedback <= 0.15 and not self.fault

    def stop(self):
        self.armed = False
        self.velocity = self.output_velocity = 0.0
        self.neutral_since = None

    def fail(self, reason):
        self.stop()
        self.fault = reason

    def feedback(self, data, now):
        p, v, c, temp, error = struct.unpack('>hhhBB', data)
        position = p / 10.0
        if now - self.last_feedback > 0.15:
            if self.initialized or self.engaged:
                self.fail('Motor feedback timeout')
            self.initialized = False
            self.stable_since = None
            self.samples = 0
            self.stop()
        self.position, self.current = position, c / 100.0
        self.measured_velocity = v * 10.0 / ERPM_PER_DEG_S
        self.temperature, self.motor_error = temp, error
        self.last_feedback = now
        if error:
            self.fail(f'Motor fault {error}')
        elif abs(self.current) > 3.0:
            self.fail('Current exceeds 3 A test limit')
        # Raw angle is telemetry only. It may wrap over multiple revolutions.
        if not self.initialized:
            if (self.stable_since is None or abs(position - self.stable_position) > 0.3
                    or abs(self.measured_velocity) > 1.0):
                self.stable_since, self.stable_position, self.samples = now, position, 0
            self.samples += 1
            if self.samples >= 20 and now - self.stable_since >= 0.4:
                self.initialized = True

    def command(self, value, now):
        # Signed normalized drive component: positive=RT, negative=LT.
        if self.stop_latched:
            return
        if not math.isfinite(value):
            self.fail('Invalid velocity command')
            return
        if now - self.last_command > 0.25:
            self.stop()
        self.last_command = now
        if abs(value) <= 0.025:
            # Stops bypass acceleration limiting; never leave a deceleration tail.
            self.velocity = self.output_velocity = 0.0
            if self.neutral_since is None:
                self.neutral_since = now
            healthy = (self.initialized and now - self.last_feedback <= 0.15
                       and not self.motor_error and abs(self.current) <= 3)
            if healthy and now - self.neutral_since >= 0.3:
                self.fault = ''
                self.armed = True
            return
        self.neutral_since = None
        if not self.ready(now) or not self.armed:
            self.stop()
            return
        self.velocity = max(-1.0, min(1.0, value)) * self.speed * self.clockwise_sign

    def emergency_stop(self):
        self.stop_latched = True
        self.fail('Dashboard E-Stop latched; restart flat motor to reset')

    def step(self, now, dt):
        if now - self.last_feedback > 0.15:
            if self.initialized or self.engaged:
                self.fail('Motor feedback timeout')
            self.initialized = False
        if not self.ready(now):
            # An earlier speed command persists in the motor. Actively send zero
            # even on RX loss/fault; a later CAN recovery must not resend speed.
            return 0.0 if self.engaged else None
        if now - self.last_command > 0.25:
            self.stop()
        if self.armed and self.velocity:
            self.engaged = True
            increment = self.acceleration * min(max(dt, 0.0), 0.04)
            change = max(-increment, min(increment, self.velocity - self.output_velocity))
            self.output_velocity += change
        else:
            self.output_velocity = 0.0
        return self.output_velocity if self.engaged else None

    def status(self, now):
        return dict(ready=self.ready(now), armed=self.armed, fault=self.fault,
                    stop_latched=self.stop_latched, control_mode='velocity',
                    position_deg=self.position, measured_velocity_deg_s=self.measured_velocity,
                    requested_velocity_deg_s=self.velocity,
                    command_velocity_deg_s=self.output_velocity,
                    current_amp=self.current, temperature_c=self.temperature,
                    motor_error=self.motor_error, max_speed_deg_s=self.speed,
                    max_accel_deg_s2=self.acceleration,
                    clockwise_sign=self.clockwise_sign, angle_limits_enabled=False)
