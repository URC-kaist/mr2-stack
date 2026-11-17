#!/usr/bin/python3
"""Clock profiles and process/lease lifecycle; no ROS or third-party imports."""
import ipaddress
import json
import math
import os
from pathlib import Path
import subprocess
import time

LEASE_SECONDS = 60
RENEW_SECONDS = 10
MAX_LOCKED_OFFSET = 0.05
MAX_MEASUREMENT_ERROR = .002


def local_server(host):
    # Chrony 4.2 on both hosts supports interleaved kernel timestamps and
    # frequency transfer. Reject large queue spikes relative to the minimum;
    # a fixed 3.6 ms cutoff starves the estimator on a loaded radio link.
    return (f'server {ipaddress.IPv4Address(host)} iburst prefer minpoll -3 maxpoll -1 '
            'xleave extfield F323 filter 4 maxdelay 0.25 maxdelayratio 2')


def clock_error(track):
    try:
        offset = abs(float(track['System time'].split()[0]))
        delay = abs(float(track['Root delay'].split()[0]))
        dispersion = float(track['Root dispersion'].split()[0])
        if not all(math.isfinite(x) and x >= 0 for x in (offset, delay, dispersion)):
            return None
        return offset + dispersion + delay / 2
    except (KeyError, ValueError, IndexError):
        return None


def can_prepare_step(track, sample, peer):
    """Only for the first pre-launch acquisition, with a qualified local sample."""
    total = clock_error(track)
    try:
        offset = abs(float(track['System time'].split()[0]))
        age, error = sample['peer_sample_age_s'], sample['peer_sample_error_s']
        return (selected_ip(track) == peer and track.get('Leap status') == 'Normal'
                and total is not None and offset <= MAX_LOCKED_OFFSET
                and total-offset <= MAX_MEASUREMENT_ERROR
                and age is not None and error is not None
                and math.isfinite(age) and math.isfinite(error)
                and 0 <= age <= 10 and 0 <= error <= MAX_MEASUREMENT_ERROR)
    except (KeyError, ValueError, IndexError, TypeError):
        return False


class ClockQuality:
    """Bounded holdover; never extend trust beyond the acknowledged base lease."""
    def __init__(self):
        self.reset()

    def reset(self):
        self.anchor = None
        self.reference = None
        self.stable = 0
        self.last_wall = None

    def update(self, track, sample, follows_base, now, wall):
        jumped = False
        if self.last_wall:
            delta, elapsed = wall - self.last_wall[1], now - self.last_wall[0]
            if elapsed < 0 or abs(delta - elapsed) > .002 + max(0, elapsed) * .00015:
                self.reset()
                self.reference = track.get('Ref time (UTC)')
                jumped = True
        self.last_wall = (now, wall)
        bound = clock_error(track)
        result = dict(relative_ready=False, clock_state='invalid', clock_error_bound_s=bound)
        if jumped:
            result['clock_jump'] = True
            return result
        if not follows_base or bound is None or track.get('Leap status') != 'Normal':
            self.anchor = None; self.stable = 0
            return result
        try:
            skew = abs(float(track['Skew'].split()[0]))
            residual = abs(float(track['Residual freq'].split()[0]))
            if not all(math.isfinite(x) for x in (skew, residual)):
                return result
            # Match the configured maxclockerror floor and retain a larger
            # learned frequency uncertainty when chrony reports one.
            drift = max(10., skew + residual) * 1e-6
            result['clock_drift_bound_ppm'] = drift * 1e6
        except (KeyError, ValueError, IndexError):
            return result
        age = sample.get('peer_sample_age_s')
        ref = track.get('Ref time (UTC)')
        accepted = age is not None and math.isfinite(age) and age >= 0 and ref is not None
        if accepted and bound <= MAX_MEASUREMENT_ERROR and ref != self.reference:
            self.reference = ref
            # tracking's dispersion is already aged to this observation. Start
            # forecasting here; subtracting LastRx would count that age twice.
            self.anchor = dict(at=now, error=bound, drift=drift)
        # A median filter can process an older packet into a new current fit.
        # Detect new tracking fits, rather than rejecting them by LastRx age.
        fresh = self.anchor is not None and now - self.anchor['at'] <= 10
        if self.anchor:
            self.anchor['drift'] = max(self.anchor['drift'], drift)
            # Root dispersion already grows between updates. Use the larger
            # estimate, rather than adding the same elapsed uncertainty twice.
            bound = max(bound, self.anchor['error'] +
                        max(0., now-self.anchor['at']) * self.anchor['drift'])
        result['clock_error_bound_s'] = bound
        if self.anchor and bound <= MAX_MEASUREMENT_ERROR:
            self.stable += 1
            if self.stable >= 3:
                result.update(relative_ready=True, clock_state='fresh' if fresh else 'holdover')
        else:
            self.stable = 0
        return result


def atomic_json(path, value, mode=0o644):
    atomic_write(path, json.dumps(value, indent=2) + '\n', mode)


def atomic_write(path, content, mode=0o644):
    path = Path(path)
    temp = path.with_name(path.name + '.tmp')
    with temp.open('w') as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())
    os.chmod(temp, mode)
    os.replace(temp, path)


def field_base_for_addresses(config, addresses):
    """Select the field authority only while its configured local IP is present."""
    field = config.get('field_network')
    if config['role'] != 'rover' or not field:
        return None
    local = str(ipaddress.IPv4Address(field['local_address']))
    base = str(ipaddress.IPv4Address(field['base_host']))
    for interface in addresses:
        if interface.get('ifname') == field['interface'] and any(
                addr.get('family') == 'inet' and addr.get('local') == local
                for addr in interface.get('addr_info', [])):
            return base
    return None


def render(config, locked, peer=None, idle_base=None):
    role = config['role']
    if role not in ('base', 'rover'):
        raise ValueError('Invalid clock role')
    lines = ['# Managed by mr2-clock; edit /etc/mr2-clock/config.json.',
             'driftfile /var/lib/chrony/chrony.drift', 'rtcsync',
             'logdir /var/log/chrony', 'log tracking measurements statistics',
             'bindcmdaddress 127.0.0.1', 'bindcmdaddress ::1',
             'leapsecmode slew', 'dscp 46', 'maxclockerror 10']
    if role == 'base':
        lines.append('local stratum 8')
        for network in config['allow']:
            lines.append(f'allow {ipaddress.ip_network(network, strict=False)}')
    peers = (([peer] if peer else []) if locked else
             ([idle_base] if idle_base else config.get('base_hosts', [])))
    if role == 'rover':
        for host in peers:
            lines.append(local_server(host))
    if locked:
        lines += [f'maxchange {MAX_LOCKED_OFFSET} 0 -1', 'maxslewrate 100']
    else:
        # A rover with Internet on a second interface must not outvote an
        # offline field base. UTC recovery belongs to the base in this mode.
        if role != 'rover' or not idle_base:
            lines += ['pool ntp.ubuntu.com iburst maxsources 4',
                      'pool pool.ntp.org iburst maxsources 4']
        lines.append('makestep 0.1 -1')
    return '\n'.join(lines) + '\n'


def process_identity(pid):
    try:
        # comm may contain spaces and parentheses.
        tail = Path(f'/proc/{int(pid)}/stat').read_text().rsplit(')', 1)[1].split()
        return None if tail[0] == 'Z' else tail[19]
    except (OSError, ValueError, IndexError):
        return None


def session_alive(session):
    if process_identity(session['pid']) == session['start']:
        return True
    # A killed launch parent may leave ROS children running. Their inherited
    # marker retains the lock until they exit, including after reparenting.
    marker = ('MR2_CLOCK_SESSION=' + session['id']).encode()
    for entry in Path('/proc').iterdir():
        if not entry.name.isdigit():
            continue
        try:
            if entry.stat().st_uid == session['uid'] and marker in (entry / 'environ').read_bytes().split(b'\0'):
                return True
        except OSError:
            continue
    return False


class Locks:
    def __init__(self, boot_id, saved=None):
        saved = saved or {}
        self.boot_id = boot_id
        self.local = saved.get('local', {}) if saved.get('boot_id') == boot_id else {}
        self.remote = saved.get('remote', {}) if saved.get('boot_id') == boot_id else {}

    def prune(self, now, alive=session_alive):
        self.local = {key: value for key, value in self.local.items() if alive(value)}
        self.remote = {key: value for key, value in self.remote.items() if value > now}

    @property
    def locked(self):
        return bool(self.local or self.remote)

    def data(self):
        return dict(boot_id=self.boot_id, local=self.local, remote=self.remote)


def run(args, **kwargs):
    return subprocess.run(args, text=True, capture_output=True, check=True,
                          timeout=kwargs.pop('timeout', 12), **kwargs).stdout


def tracking():
    result = {}
    for line in run(['chronyc', '-n', 'tracking']).splitlines():
        if ':' in line:
            key, value = line.split(':', 1)
            result[key.strip()] = value.strip()
    return result


def selected_ip(track):
    try:
        return str(ipaddress.IPv4Address(int(track['Reference ID'].split()[0], 16)))
    except (KeyError, ValueError):
        return None


def peer_sample(output, peer):
    """Reject stale or large samples even if chrony retains an older tracking fit."""
    for line in output.splitlines():
        fields = line.split(',')
        if len(fields) < 10 or fields[1] != '*' or fields[2] != peer:
            continue
        try:
            age, offset, error = float(fields[6]), float(fields[7]), float(fields[9])
            ready = (all(math.isfinite(x) for x in (age, offset, error))
                     and 0 <= age <= 20 and abs(offset) <= .002 and 0 <= error <= .002)
            return dict(relative_ready=ready, peer_sample_age_s=age, peer_sample_offset_s=offset,
                        peer_sample_error_s=error)
        except ValueError:
            break
    return dict(relative_ready=False, peer_sample_age_s=None, peer_sample_offset_s=None,
                peer_sample_error_s=None)


def read_json(path, default=None):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return {} if default is None else default
