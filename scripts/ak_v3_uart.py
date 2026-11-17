#!/usr/bin/env python3
"""Read AK V3 UART settings; optionally change only the CAN bitrate.

Uses the CubeMars V3.2.3 tool's getAppConf (0x52) / setAppConf (0x51)
protocol. Writing is restricted to the observed AK60-6 V3.3 firmware and
configuration signature/length. No calibration or motion commands are sent.
Requires no third-party Python modules. Backups live outside the repository.
"""

import argparse
import binascii
import fcntl
import json
import os
from pathlib import Path
import select
import struct
import termios
import time


DEFAULT_PORT = '/dev/serial/by-id/usb-1a86_USB_Single_Serial_5C7B016252-if00'
BITRATES = (125000, 250000, 500000, 1000000, 10000, 20000, 50000, 75000)
SIGNATURE = bytes.fromhex('92a2de2e')


def frame(payload):
    length = len(payload)
    if not 0 < length <= 65535:
        raise ValueError('Invalid payload length')
    header = (b'\xaa' + bytes([length]) if length < 256 else
              b'\xab' + length.to_bytes(2, 'big'))
    return header + payload + binascii.crc_hqx(payload, 0).to_bytes(2, 'big') + b'\xbb'


def unpack_frame(raw):
    if not raw or raw[0] not in (0xaa, 0xab):
        raise ValueError('Missing V3 frame header')
    start = 2 if raw[0] == 0xaa else 3
    length = int.from_bytes(raw[1:start], 'big')
    if len(raw) != start + length + 3 or raw[-1] != 0xbb:
        raise ValueError('Truncated or unexpected UART response')
    payload = raw[start:-3]
    if binascii.crc_hqx(payload, 0) != int.from_bytes(raw[-3:-1], 'big'):
        raise ValueError('UART CRC mismatch')
    return payload


class Port:
    def __enter__(self):
        self.fd = os.open(self.path, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
        self.old = termios.tcgetattr(self.fd)
        try:
            fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            a = termios.tcgetattr(self.fd)
            a[0] = a[1] = a[3] = 0
            a[2] = termios.CS8 | termios.CREAD | termios.CLOCAL
            a[4] = a[5] = termios.B921600
            a[6][termios.VMIN] = a[6][termios.VTIME] = 0
            termios.tcsetattr(self.fd, termios.TCSANOW, a)
            fcntl.ioctl(self.fd, termios.TIOCMBIS,
                        struct.pack('I', termios.TIOCM_DTR | termios.TIOCM_RTS))
        except Exception:
            self.__exit__(None, None, None)
            raise
        return self

    def __init__(self, path):
        self.path = path

    def __exit__(self, *_):
        try:
            termios.tcsetattr(self.fd, termios.TCSANOW, self.old)
        finally:
            os.close(self.fd)

    def request(self, payload):
        termios.tcflush(self.fd, termios.TCIFLUSH)
        remaining = frame(payload)
        deadline = time.monotonic() + 2
        while remaining:
            if time.monotonic() >= deadline:
                raise TimeoutError('UART send timed out')
            _, ready, _ = select.select([], [self.fd], [], .1)
            if ready:
                remaining = remaining[os.write(self.fd, remaining):]
        raw = bytearray()
        while time.monotonic() < deadline:
            ready, _, _ = select.select([self.fd], [], [], .1)
            if ready:
                raw.extend(os.read(self.fd, 4096))
            if len(raw) >= 3 and raw[0] in (0xaa, 0xab):
                start = 2 if raw[0] == 0xaa else 3
                expected = start + int.from_bytes(raw[1:start], 'big') + 3
                if len(raw) >= expected:
                    result = unpack_frame(raw)
                    if result[0] != payload[0]:
                        raise ValueError('Unexpected response command')
                    return result
        raise TimeoutError(f'No complete reply to 0x{payload[0]:02x}: {raw.hex()}')


def app_summary(payload):
    if len(payload) != 390 or payload[0] != 0x52 or payload[1:5] != SIGNATURE:
        raise ValueError('Unsupported app configuration layout; raw backup preserved')
    index = payload[17]
    if index >= len(BITRATES):
        raise ValueError('Unknown CAN bitrate enum')
    return {
        'motor_id': payload[5],
        'can_bitrate': BITRATES[index],
        'can_status_mode': payload[14],
        'can_status_rate_hz': int.from_bytes(payload[15:17], 'big'),
        'uart_baudrate': int.from_bytes(payload[138:142], 'big'),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', default=DEFAULT_PORT)
    parser.add_argument('--can-bitrate', type=int, choices=BITRATES,
                        help='Persist ONLY this CAN bitrate; default is read-only')
    parser.add_argument('--expect-id', type=int,
                        help='Required for writing; must match both status and configuration')
    args = parser.parse_args()
    if args.can_bitrate is not None and args.expect_id is None:
        parser.error('--can-bitrate requires --expect-id')
    root = Path.home() / '.local/state/mr2/ak60-6'
    root.mkdir(parents=True, exist_ok=True)
    backup = root / f'config-{time.time_ns()}.json'
    with Port(args.port) as port:
        firmware = port.request(b'\x41')
        status = port.request(b'\x45')
        before = port.request(b'\x52')
        record = {'port': args.port, 'firmware_payload': firmware.hex(),
                  'status_payload': status.hex(), 'appconf_before_payload': before.hex()}
        # Save the exact CRC-validated bytes before interpreting or changing them.
        with backup.open('x') as f:
            json.dump(record, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        print(f'Backup: {backup}', flush=True)
        summary = app_summary(before)
        print(json.dumps(summary, indent=2), flush=True)
        if args.can_bitrate is None:
            return
        if b'AK60_6_SE_V3.3\0' not in firmware:
            raise ValueError('Writing is not supported for this firmware')
        if len(status) != 81 or status[58] != args.expect_id or before[5] != args.expect_id:
            raise ValueError('Unexpected motor ID or status layout')
        if status[53] != 0 or struct.unpack_from('>i', status, 23)[0] != 0:
            raise ValueError('Motor must be stationary and have no fault before configuration')
        if abs(struct.unpack_from('>i', status, 5)[0]) > 10:
            raise ValueError('Motor output current must be below 0.1 A')
        if summary['can_bitrate'] == args.can_bitrate:
            print('Already configured; no flash write sent.')
            return
        target = bytearray(before)
        target[17] = BITRATES.index(args.can_bitrate)
        outgoing = bytes([0x51]) + bytes(target[1:])
        try:
            ack = port.request(outgoing)
            record['write_ack_payload'] = ack.hex()
        except (TimeoutError, ValueError) as exc:
            # A missing ACK must not trigger another flash write. Read back once.
            record['write_ack_error'] = str(exc)
        time.sleep(.25)
        after = port.request(b'\x52')
        record['appconf_after_payload'] = after.hex()
        record['verified'] = after == bytes(target)
        with backup.open('w') as f:
            json.dump(record, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        if not record['verified']:
            raise RuntimeError('Readback differs from requested settings; inspect backup before retrying')
        print('Verified: CAN bitrate updated; every other configuration byte unchanged.')
        print(json.dumps(app_summary(after), indent=2))


if __name__ == '__main__':
    main()
