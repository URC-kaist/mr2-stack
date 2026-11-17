#!/usr/bin/python3
"""Restricted SSH entrypoint, local status, and hardware-free lifetime test."""
import json
import socket
import sys
import time


def request(message):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        sock.settimeout(25)
        sock.connect('/run/mr2-clock/control.sock')
        sock.sendall((json.dumps(message) + '\n').encode())
        response = sock.makefile('r').readline(8192)
    value = json.loads(response)
    if not value.get('ok'):
        raise RuntimeError(value.get('error', 'Clock policy unavailable'))
    return value


def main():
    if sys.argv[1:] == ['remote']:
        data = json.loads(sys.stdin.readline(4096))
        if data.get('op') not in ('remote-acquire', 'remote-release'):
            raise ValueError('Only clock leases are permitted by this SSH key')
        print(json.dumps(request(data)))
    elif sys.argv[1:] == ['status']:
        print(json.dumps(request({'op': 'status'}), indent=2))
    elif len(sys.argv) == 3 and sys.argv[1] == 'hold':
        print(json.dumps(request({'op': 'acquire'})), flush=True)
        time.sleep(min(3600, max(1, int(sys.argv[2]))))
    else:
        raise ValueError('Usage: mr2-clock-client {status|remote|hold SECONDS}')


if __name__ == '__main__':
    main()
