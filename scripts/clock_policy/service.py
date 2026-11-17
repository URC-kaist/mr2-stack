#!/usr/bin/python3
"""Root clock policy coordinator. Local IPC + restricted, pinned SSH leases."""
import grp
import ipaddress
import json
import os
from pathlib import Path
import re
import socket
import socketserver
import struct
import subprocess
import threading
import time
import uuid

from policy import (LEASE_SECONDS, RENEW_SECONDS, Locks, atomic_json, atomic_write,
                    process_identity, read_json, render, run, selected_ip, tracking, peer_sample,
                    field_base_for_addresses, ClockQuality, local_server, can_prepare_step)

RUNTIME = Path('/run/mr2-clock')
STATE = RUNTIME / 'locks.json'
STATUS = RUNTIME / 'status.json'
CONF = Path('/etc/chrony/chrony.conf')


class Manager:
    def __init__(self, config):
        self.config = config
        self.mutex = threading.RLock()
        self.locks = Locks(Path('/proc/sys/kernel/random/boot_id').read_text().strip(), read_json(STATE))
        self.peer = None
        self.peer_until = 0
        self.next_renew = 0
        self.error = None
        self.applied = None
        self.probe = {}
        self.idle_base = None
        self.applied_locked = None
        self.source_peer = None
        self.peer_epoch = None
        self.clock_epoch = uuid.uuid4().hex
        self.chrony_pid = None
        self.quality = ClockQuality()
        self.reset_source = False
        self.remote_id = self.locks.boot_id.replace('-', '')

    def save(self):
        atomic_json(STATE, self.locks.data(), 0o600)

    def apply(self, locked, peer=None):
        self.idle_base = None
        if not locked and self.config['role'] == 'rover' and self.config.get('field_network'):
            # Re-evaluate on every idle reconciliation, so leaving the field
            # network restores Internet sources without a service restart.
            addresses = json.loads(run(['ip', '-j', '-4', 'address', 'show']))
            self.idle_base = field_base_for_addresses(self.config, addresses)
        content = render(self.config, locked, peer, idle_base=self.idle_base)
        if content == self.applied and not self.reset_source:
            return
        run(['chronyd', '-p', *content.splitlines()])
        atomic_write(CONF, content)
        # Peer availability changes preserve the same running estimator. The
        # on-disk profile stays safe if either daemon restarts in the meantime.
        if self.applied is not None and locked and self.applied_locked:
            try:
                if self.reset_source and self.source_peer:
                    run(['chronyc', 'delete', self.source_peer])
                    self.source_peer = None
                if peer and peer != self.source_peer:
                    if self.source_peer:
                        run(['chronyc', 'delete', self.source_peer])
                    run(['chronyc', 'add', *local_server(peer).split()])
                    self.source_peer = peer
                if self.source_peer:
                    run(['chronyc', 'online' if peer else 'offline', self.source_peer])
                self.applied = content
                self.reset_source = False
                return
            except Exception:
                run(['systemctl', 'stop', 'chrony.service'])
                raise
        # Locked/idle constraints still change only at application boundaries.
        try:
            run(['systemctl', 'restart', 'chrony.service'])
        except Exception:
            # Do not leave an old idle chronyd adjusting time after failed lock.
            run(['systemctl', 'stop', 'chrony.service'])
            raise
        self.applied = content
        self.applied_locked = locked
        self.source_peer = peer if locked else None
        self.reset_source = False
        self.quality.reset()

    def remote(self, host, op):
        output = run([
            'ssh', '-F', '/dev/null', '-i', '/var/lib/mr2-clock/base_key',
            '-o', 'BatchMode=yes', '-o', 'IdentitiesOnly=yes',
            '-o', 'StrictHostKeyChecking=yes', '-o', 'HostKeyAlias=mr2-clock-base',
            '-o', 'UserKnownHostsFile=/etc/mr2-clock/known_hosts',
            '-o', 'ConnectTimeout=2', '-o', 'ConnectionAttempts=1',
            '-o', 'LogLevel=ERROR', f"{self.config['ssh_user']}@{host}",
            'mr2-clock-lease'], input=json.dumps({'op': op, 'id': self.remote_id}) + '\n', timeout=5)
        response = json.loads(output)
        if not response.get('ok') or (op == 'remote-acquire' and not response.get('locked')):
            raise RuntimeError('Base did not acknowledge clock lock')
        return response

    def reconcile(self, force=False):
        now = time.monotonic()
        self.locks.prune(now)
        self.save()
        locked = self.locks.locked
        if self.config['role'] == 'rover' and locked:
            # Freeze locally before any network wait. Leave ROS usable if base
            # is absent, but never follow a base that might still step its clock.
            self.apply(True, self.peer if now < self.peer_until - 20 else None)
            if force or now >= self.next_renew:
                self.next_renew = now + RENEW_SECONDS
                overrides = {s['base_host'] for s in self.locks.local.values() if s.get('base_host')}
                hosts = sorted(overrides) if overrides else list(dict.fromkeys(
                    ([self.peer] if self.peer else []) + self.config['base_hosts']))
                success = False
                previous_peer, previous_until = self.peer, self.peer_until
                for host in hosts:
                    try:
                        sent = time.monotonic()
                        response = self.remote(host, 'remote-acquire')
                        epoch = response.get('clock_epoch')
                        if epoch != self.peer_epoch or host != previous_peer:
                            self.quality.reset()
                            self.reset_source = self.peer_epoch is not None
                        self.peer_epoch = epoch
                        self.peer, self.peer_until = host, sent + LEASE_SECONDS
                        success = True
                        break
                    except (OSError, ValueError, subprocess.SubprocessError, RuntimeError):
                        pass
                if not success:
                    if previous_peer and now < previous_until - 20:
                        self.peer, self.peer_until = previous_peer, previous_until
                    else:
                        self.peer, self.peer_until = None, 0
            self.apply(True, self.peer if time.monotonic() < self.peer_until - 20 else None)
        else:
            self.apply(locked)
            if self.peer:
                host, self.peer = self.peer, None
                try:
                    self.remote(host, 'remote-release')
                except (OSError, ValueError, subprocess.SubprocessError, RuntimeError):
                    pass  # Remote monotonic lease expires after an unclean disconnect.
        self.error = None
        self.publish()

    def publish(self):
        track = {}
        try:
            track = tracking()
        except (OSError, subprocess.SubprocessError):
            pass
        selected = selected_ip(track)
        synced = track.get('Leap status') == 'Normal' and selected not in (None, '0.0.0.0')
        locked = self.locks.locked
        follows_base = (self.config['role'] == 'rover' and synced
                        and selected == self.peer and time.monotonic() < self.peer_until - 20)
        sample = peer_sample('', None)
        if follows_base:
            try:
                sample = peer_sample(run(['chronyc', '-c', '-n', 'sources']), self.peer)
            except (OSError, subprocess.SubprocessError):
                pass
        try:
            pid = Path('/run/chrony/chronyd.pid').read_text().strip()
        except OSError:
            pid = None
        if pid != self.chrony_pid:
            self.chrony_pid = pid
            self.clock_epoch = uuid.uuid4().hex
            self.quality.reset()
        quality = self.quality.update(track, sample, follows_base and bool(self.peer_epoch),
                                      time.monotonic(), time.time())
        if quality.pop('clock_jump', False):
            self.clock_epoch = uuid.uuid4().hex
        utc_state = 'held' if locked else (
            'internet-synchronized' if synced and selected != '127.127.1.1'
            and selected not in self.config.get('base_hosts', []) else 'unverified')
        atomic_json(STATUS, {
            'version': 1, 'role': self.config['role'], 'locked': locked,
            'base_locked': follows_base, 'base_peer': self.peer,
            'selected_source': selected, 'utc_state': utc_state,
            'local_sessions': len(self.locks.local), 'remote_sessions': len(self.locks.remote),
            'updated_monotonic': time.monotonic(), 'error': self.error,
            'internet_probe': self.probe,
            'idle_base_only': self.idle_base,
            **sample,
            **quality,
            'clock_epoch': self.clock_epoch,
            'base_clock_epoch': self.peer_epoch,
        })

    def request(self, request, pid, uid):
        with self.mutex:
            op = request.get('op')
            if op == 'acquire':
                first_launch = not self.locks.locked
                start = process_identity(pid)
                if start is None:
                    raise ValueError('Launch process no longer exists')
                sid = next((key for key, item in self.locks.local.items()
                            if item['pid'] == pid and item['start'] == start), uuid.uuid4().hex)
                override = request.get('base_host')
                if override:
                    override = str(ipaddress.IPv4Address(override))
                    other = {s['base_host'] for s in self.locks.local.values() if s.get('base_host') and s['pid'] != pid}
                    if other and other != {override}:
                        raise ValueError('Another ROS launch is already locked to a different base')
                self.locks.local[sid] = dict(id=sid, pid=pid, start=start, uid=uid,
                                            **({'base_host': override} if override else {}))
                self.save()  # Preserve intention even if SSH or chrony restart fails.
                self.reconcile(force=True)
                if first_launch and request.get('prepare') is True and self.peer:
                    self.prepare_clock()
                return {'ok': True, 'id': sid, 'locked': True, 'base_peer': self.peer}
            if op in ('remote-acquire', 'remote-release'):
                if self.config['role'] != 'base' or not re.fullmatch('[a-f0-9]{32}', request.get('id', '')):
                    raise ValueError('Invalid base lease request')
                sid = request['id']
                if op == 'remote-acquire':
                    self.locks.remote[sid] = time.monotonic() + LEASE_SECONDS
                else:
                    self.locks.remote.pop(sid, None)
                self.save()
                self.reconcile()
                return {'ok': True, 'locked': self.locks.locked, 'clock_epoch': self.clock_epoch}
            if op == 'status':
                return {'ok': True, **read_json(STATUS)}
            # No release RPC for local sessions: children may still be shutting
            # down. Process death + marker disappearance is the release condition.
            raise ValueError('Unsupported clock request')

    def prepare_clock(self):
        # The real launch requests this before creating any ROS/hardware node.
        # Never step for a renewal, another active launch or a plain hold lease.
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            try:
                track = tracking()
                sample = peer_sample(run(['chronyc', '-c', '-n', 'sources']), self.peer)
            except (OSError, subprocess.SubprocessError):
                return
            if can_prepare_step(track, sample, self.peer):
                if abs(float(track['System time'].split()[0])) > .0002:
                    run(['chronyc', 'makestep'])
                    self.quality.reset()
                    self.clock_epoch = uuid.uuid4().hex
                self.publish()
                return
            time.sleep(.5)

    def poll(self):
        while True:
            with self.mutex:
                try:
                    self.reconcile()
                except Exception as error:
                    self.error = str(error)
                    self.publish()
                    print(f'Clock policy error: {error}', flush=True)
            time.sleep(2)

    def observe(self):
        # Separate non-setting chronyd instance; never reuses the main PID/state.
        while True:
            if self.locks.locked:
                try:
                    result = subprocess.run([
                        'chronyd', '-Q', '-t', '8',
                        'pool ntp.ubuntu.com iburst maxsamples 1', 'port 0', 'cmdport 0',
                        'pidfile /run/chrony/mr2-clock-probe.pid'], capture_output=True, text=True, timeout=12)
                    match = re.search(r'System clock wrong by ([+-]?[\d.]+) seconds', result.stdout + result.stderr)
                except (OSError, subprocess.SubprocessError):
                    match = None
                self.probe = {'available': bool(match),
                              'offset_s': float(match[1]) if match else None,
                              'checked_monotonic': time.monotonic()}
            time.sleep(60)


class Handler(socketserver.StreamRequestHandler):
    def handle(self):
        self.request.settimeout(25)
        pid, uid, _gid = struct.unpack('3i', self.request.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
        try:
            line = self.rfile.readline(4097)
            if len(line) > 4096:
                raise ValueError('Request too long')
            result = self.server.manager.request(json.loads(line), pid, uid)
        except Exception as error:
            result = {'ok': False, 'error': str(error)}
        self.wfile.write((json.dumps(result) + '\n').encode())


class Server(socketserver.ThreadingUnixStreamServer):
    daemon_threads = True


def main():
    config = read_json('/etc/mr2-clock/config.json')
    RUNTIME.mkdir(mode=0o755, exist_ok=True)
    path = RUNTIME / 'control.sock'
    path.unlink(missing_ok=True)
    manager = Manager(config)
    with Server(str(path), Handler) as server:
        os.chown(path, 0, grp.getgrnam(config['group']).gr_gid)
        os.chmod(path, 0o660)
        server.manager = manager
        threading.Thread(target=manager.poll, daemon=True).start()
        threading.Thread(target=manager.observe, daemon=True).start()
        server.serve_forever()


if __name__ == '__main__':
    main()
