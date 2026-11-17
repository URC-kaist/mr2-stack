#!/usr/bin/python3
"""Install the clock manager; requires root, preserves a complete rollback backup."""
import argparse
from datetime import datetime, timezone
import grp
import ipaddress
import json
import os
from pathlib import Path
import pwd
import shutil
import subprocess
import uuid

from policy import atomic_json, atomic_write, read_json, render, run


def install_file(path, content, mode=0o644):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(path, content, mode)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--role', required=True, choices=['base', 'rover'])
    parser.add_argument('--user', default='mr2')
    parser.add_argument('--base-ip', default='192.168.1.101')
    parser.add_argument('--rover-ip', default='192.168.1.102')
    parser.add_argument('--field-interface', default='enP8p1s0')
    parser.add_argument('--dev-base-ip', action='append', default=[])
    parser.add_argument('--dev-subnet', action='append', default=[])
    parser.add_argument('--known-hosts-file', type=Path)
    parser.add_argument('--rover-public-key', type=Path)
    args = parser.parse_args()
    if os.geteuid() != 0:
        parser.error('Run this installer with sudo')
    pwd.getpwnam(args.user)
    grp.getgrnam(args.user)
    if read_json('/run/mr2-clock/status.json').get('locked'):
        parser.error('Stop active ROS launches and wait for base leases to expire before upgrading')
    hosts = list(dict.fromkeys(str(ipaddress.IPv4Address(x)) for x in [args.base_ip, *args.dev_base_ip]))
    allow = [str(ipaddress.ip_network(args.rover_ip + '/32'))]
    allow += [str(ipaddress.ip_network(x, strict=False)) for x in args.dev_subnet]
    config = dict(role=args.role, group=args.user, ssh_user=args.user,
                  base_hosts=hosts, allow=allow)
    if args.role == 'rover':
        config['field_network'] = dict(interface=args.field_interface,
                                       local_address=str(ipaddress.IPv4Address(args.rover_ip)),
                                       base_host=hosts[0])
    # Check before changing host files; the locked profile is the safe default.
    run(['chronyd', '-p', *render(config, True).splitlines()])
    backup = Path('/var/backups/mr2-clock') / (datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + uuid.uuid4().hex[:6])
    backup.mkdir(parents=True)
    for name in ('/etc/chrony/chrony.conf', '/etc/mr2-clock',
                 '/etc/systemd/system/mr2-clock.service'):
        path = Path(name)
        target = backup / path.relative_to('/')
        if path.is_dir():
            shutil.copytree(path, target)
        elif path.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
    print(f'Backup: {backup}', flush=True)
    subprocess.run(['systemctl', 'stop', 'mr2-clock.service'], check=False,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    source = Path(__file__).resolve().parent
    for name in ('policy.py', 'service.py', 'client.py'):
        install_file('/usr/local/lib/mr2-clock/' + name, (source / name).read_text())
    install_file('/usr/local/bin/mr2-clock-client',
                 '#!/bin/sh\nexec /usr/bin/python3 /usr/local/lib/mr2-clock/client.py "$@"\n', 0o755)
    Path('/etc/mr2-clock').mkdir(exist_ok=True)
    atomic_json('/etc/mr2-clock/config.json', config)
    Path('/var/lib/mr2-clock').mkdir(mode=0o700, exist_ok=True)
    if args.role == 'rover' and not Path('/var/lib/mr2-clock/base_key').exists():
        run(['ssh-keygen', '-t', 'ed25519', '-N', '', '-C', 'mr2-clock-policy',
             '-f', '/var/lib/mr2-clock/base_key'])
    if args.role == 'rover':
        install_file('/etc/mr2-clock/base_key.pub', Path('/var/lib/mr2-clock/base_key.pub').read_text())
        if args.known_hosts_file:
            install_file('/etc/mr2-clock/known_hosts', args.known_hosts_file.read_text())
    if args.role == 'base' and args.rover_public_key:
        key = args.rover_public_key.read_text().strip()
        if not key.startswith('ssh-ed25519 ') or '\n' in key or len(key.split()) < 2:
            raise ValueError('Expected a single Ed25519 rover public key')
        account = pwd.getpwnam(args.user)
        directory = Path(account.pw_dir) / '.ssh'
        directory.mkdir(mode=0o700, exist_ok=True)
        os.chown(directory, account.pw_uid, account.pw_gid)
        target = directory / 'authorized_keys'
        existing = target.read_text() if target.exists() else ''
        if target.exists():
            shutil.copy2(target, backup / 'authorized_keys')
        if not any(key.split()[1] in line.split() for line in existing.splitlines()):
            install_file(target, existing.rstrip() + '\nrestrict,command="/usr/local/bin/mr2-clock-client remote" ' + key + '\n', 0o600)
            os.chown(target, account.pw_uid, account.pw_gid)
    install_file('/etc/chrony/chrony.conf', render(config, True))
    install_file('/etc/systemd/system/mr2-clock.service', '''[Unit]
Description=MR2 offline and ROS launch clock policy
After=chrony.service network.target fake-hwclock.service
Wants=chrony.service

[Service]
Type=simple
ExecStart=/usr/bin/python3 /usr/local/lib/mr2-clock/service.py
Restart=always
RestartSec=2
RuntimeDirectory=mr2-clock
RuntimeDirectoryMode=0755
RuntimeDirectoryPreserve=yes
UMask=0022

[Install]
WantedBy=multi-user.target
''')
    run(['systemctl', 'daemon-reload'])
    run(['systemd-analyze', 'verify', 'mr2-clock.service'])
    run(['systemctl', 'enable', '--now', 'mr2-clock.service'])
    print('Clock manager installed. Configure the restricted SSH key before measurement.')


if __name__ == '__main__':
    main()
