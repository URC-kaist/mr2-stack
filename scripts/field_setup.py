#!/usr/bin/env python3
"""Offline field startup preparation. No downloads and no clock-setting commands."""
import argparse
import json
import math
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts/clock_policy'))
from policy import peer_sample, selected_ip, tracking  # noqa: E402

NETWORK = {
    'base': ('enp2s0', '192.168.1.101'),
    'rover': ('enP8p1s0', '192.168.1.102'),
}


def run(*args, timeout=15):
    return subprocess.run(args, check=True, capture_output=True, text=True,
                          timeout=timeout).stdout.strip()


def status():
    data = json.loads(run('mr2-clock-client', 'status', timeout=30))
    age = time.monotonic() - data['updated_monotonic']
    if not 0 <= age < 20 or data.get('error'):
        raise RuntimeError(f'Clock policy is stale or failed: {data.get("error")}')
    return data


def check(role):
    config = json.loads(Path('/etc/mr2-clock/config.json').read_text())
    if config['role'] != role:
        raise RuntimeError(f'This device is {config["role"]}, not {role}')
    interface, address = NETWORK[role]
    if not Path('/sys/class/net', interface).exists():
        raise RuntimeError(f'Missing field Ethernet interface: {interface}')
    for command in ('nmcli', 'ip', 'chronyc', 'mr2-clock-client', 'tcpdump', 'getcap'):
        if not shutil.which(command):
            raise RuntimeError(f'Missing installed command: {command}')
    for service in ('mr2-clock', 'chrony') + (('nginx',) if role == 'base' else ()):
        run('systemctl', 'is-active', service)
    capabilities = run('getcap', str(Path(shutil.which('tcpdump')).resolve()))
    if not all(c in capabilities for c in ('cap_net_admin', 'cap_net_raw')):
        raise RuntimeError('tcpdump capture permissions are missing')
    if role == 'rover':
        for package, binary in (('mr2_video_streaming', 'video_sender'),
                                ('mr2_latency_diagnostics', 'uplink_agent')):
            if not (ROOT / f'rover/ros2_ws/install/{package}/lib/{package}/{binary}').is_file():
                raise RuntimeError(f'Missing built binary: {binary}')
        if '192.168.1.101' not in config.get('base_hosts', []):
            raise RuntimeError('Field base is missing from clock policy base_hosts')
        if config.get('field_network') != dict(interface=interface, local_address=address,
                                               base_host='192.168.1.101'):
            raise RuntimeError('Clock policy needs the field_network configuration update')
    else:
        for path in ('base/gateway/node_modules/rclnodejs',):
            if not (ROOT / path).exists():
                raise RuntimeError(f'Missing installed dependency: {path}')
        if not Path('/var/www/mr2-dashboard/index.html').is_file():
            raise RuntimeError('Dashboard has not been deployed')
    current = status()
    print(f'[OK] {role}: installed prerequisites; clock locked={current["locked"]}', flush=True)
    print(f'[FIELD] {interface}: {address}/24; profile mr2-field-{role}', flush=True)
    print(run('ip', '-4', '-brief', 'address', 'show', 'dev', interface), flush=True)


def require_idle(role):
    if status()['locked']:
        raise RuntimeError('Stop the running rover ROS launch first (Ctrl+C). '
                           'Wait for its clock lock/remote lease to clear, then retry.')
    if role == 'base':
        with socket.socket() as sock:
            sock.settimeout(1)
            if sock.connect_ex(('127.0.0.1', 8081)) == 0:
                raise RuntimeError('A gateway already uses port 8081. Stop it with Ctrl+C first.')


def render_field_nginx(content):
    pattern = r'(upstream\s+mr2_rover_rosbridge\s*\{)\s*server\s+[^;]+;\s*(\})'
    updated, count = re.subn(pattern, r'\1\n  server 192.168.1.102:9090;\n\2', content)
    if count != 1:
        raise RuntimeError('Expected exactly one mr2_rover_rosbridge nginx upstream')
    if 'location /latency/uplink/' not in updated:
        raise RuntimeError('Missing non-redirecting HTTP upload location')
    return updated


def configure_nginx():
    path = Path('/etc/nginx/sites-available/mr2')
    original = path.read_text()
    updated = render_field_nginx(original)
    if updated != original:
        backup = path.with_name(f'mr2.before-field-{time.time_ns()}.bak')
        shutil.copy2(path, backup)
        path.write_text(updated)
        try:
            run('nginx', '-t')
        except Exception:
            path.write_text(original)
            raise
        print(f'[OK] nginx rover -> 192.168.1.102; backup {backup}', flush=True)
    else:
        run('nginx', '-t')
    run('systemctl', 'reload', 'nginx')


def prepare(role):
    if os.geteuid() != 0:
        raise RuntimeError('Network preparation needs sudo')
    check(role)
    require_idle(role)
    interface, address = NETWORK[role]
    name = f'mr2-field-{role}'
    names = run('nmcli', '-t', '-f', 'NAME', 'connection', 'show').splitlines()
    if name not in names:
        run('nmcli', 'connection', 'add', 'type', 'ethernet', 'ifname', interface,
            'con-name', name, 'connection.autoconnect', 'no',
            'ipv4.method', 'manual', 'ipv4.addresses', address + '/24',
            'ipv4.never-default', 'yes', 'ipv6.method', 'disabled')
    else:
        run('nmcli', 'connection', 'modify', name, 'connection.interface-name', interface,
            'connection.autoconnect', 'no', 'ipv4.method', 'manual',
            'ipv4.addresses', address + '/24', 'ipv4.gateway', '', 'ipv4.dns', '',
            'ipv4.routes', '', 'ipv4.never-default', 'yes', 'ipv6.method', 'disabled')
    if role == 'base':
        configure_nginx()
    # Activating this explicit profile replaces development DHCP. No DHCP server,
    # default gateway or Internet connection is needed on the field LAN.
    print('[FIELD] Activating static Ethernet. A development-address SSH session may disconnect.', flush=True)
    run('nmcli', '--wait', '20', 'connection', 'up', name, 'ifname', interface, timeout=25)
    print(run('ip', '-4', '-brief', 'address', 'show', 'dev', interface), flush=True)


def idle_base_ready(policy, track, sources):
    if policy.get('locked'):
        raise RuntimeError('ROS acquired a clock lock while preparing; stop it before retrying')
    if selected_ip(track) != '192.168.1.101' or track.get('Leap status') != 'Normal':
        return False
    try:
        offset = abs(float(track['System time'].split()[0]))
        bound = offset + abs(float(track['Root delay'].split()[0])) / 2 + float(track['Root dispersion'].split()[0])
        return (all(math.isfinite(v) and 0 <= v <= .002 for v in (offset, bound))
                and peer_sample(sources, '192.168.1.101')['relative_ready'])
    except (KeyError, ValueError):
        return False


def wait_clock(seconds):
    deadline = time.monotonic() + seconds
    next_log = 0
    detail = 'No clock sample yet'
    print(f'[WAIT] Synchronizing to field base BEFORE ROS launch (up to {seconds} seconds)...', flush=True)
    while time.monotonic() < deadline:
        policy = status()
        track = tracking()
        sources = run('chronyc', '-c', '-n', 'sources')
        if idle_base_ready(policy, track, sources):
            print('[OK] Recent base sample, clock offset/error <= 2 ms. Starting ROS will hold UTC.', flush=True)
            return
        detail = (f"selected={selected_ip(track)}, system_offset={track.get('System time', 'unknown')}, "
                  f"field_base_only={policy.get('idle_base_only')}")
        if time.monotonic() >= next_log:
            print('[WAIT] ' + detail, flush=True)
            next_log = time.monotonic() + 10
        time.sleep(2)
    raise RuntimeError('Field base clock is not ready. Check base power, Ethernet and '
                       'ping 192.168.1.101; inspect chronyc -n sources. No ROS launch started. '
                       'Fix the connection and rerun this same command. Last status: ' + detail)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('role', choices=NETWORK)
    parser.add_argument('action', choices=('check', 'prepare', 'clock'))
    args = parser.parse_args()
    if args.action == 'prepare':
        prepare(args.role)
    elif args.action == 'check':
        check(args.role)
    elif args.role == 'rover':
        wait_clock(120)
    else:
        raise RuntimeError('Only the rover waits for base synchronization')


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        print(f'[ERROR] {exc}', file=sys.stderr)
        if isinstance(exc, subprocess.CalledProcessError):
            print(exc.stderr, file=sys.stderr)
        sys.exit(1)
