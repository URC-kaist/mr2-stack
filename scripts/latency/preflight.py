#!/usr/bin/env python3
"""Read-only two-host deployment checks. Never starts captures or sends commands."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import shutil
import socket
import ssl
import subprocess
import sys
import time
from pathlib import Path
from urllib.request import HTTPRedirectHandler, HTTPSHandler, build_opener

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'rover/ros2_ws/src/mr2_system_status'))
from mr2_system_status.chrony_status import read_chrony_status  # noqa: E402


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError(f'Unexpected HTTP redirect ({code}); check nginx latency routes')


def run(*args):
    return subprocess.run(args, check=True, capture_output=True, text=True,
                          timeout=8).stdout.strip()


def stream_map(streams):
    result = {}
    for item in streams:
        name, port = item['stream_id'], item['udp_port']
        if not isinstance(name, str) or not name or name in result:
            raise ValueError('Invalid or duplicate stream ID')
        if type(port) is not int or not 1 <= port <= 65535 or port in result.values():
            raise ValueError('Invalid or duplicate UDP port')
        result[name] = port
    if not result:
        raise ValueError('No video streams configured')
    return result


def clock_ready(status, role):
    if status.get('available') is not True or status.get('synchronized') is not True:
        raise ValueError('chrony is unavailable or unsynchronized')
    if not status.get('policy_available') or not status.get('policy_locked'):
        raise ValueError('Clock policy must be fresh and locked for the ROS launch')
    age = abs(time.time() * 1000 - status['sampled_at_epoch_ms'])
    if not math.isfinite(age) or age > 5000:
        raise ValueError('Clock status is stale or host clocks differ by more than 5 s')
    if role == 'rover':
        if not status.get('relative_ready') or not status.get('base_locked') or not status.get('base_peer') or status.get('selected_source') != status['base_peer']:
            raise ValueError('Rover is not synchronized to the locked base')
        offset = status['system_time_offset_s']
        bound = abs(offset) + status['root_dispersion_s'] + abs(status['root_delay_s']) / 2
        if status.get('clock_error_bound_s') is not None:
            if not math.isfinite(status['clock_error_bound_s']) or status['clock_error_bound_s'] < 0:
                raise ValueError('Clock uncertainty is unavailable')
            bound = max(bound, status['clock_error_bound_s'])
        if not math.isfinite(offset) or not math.isfinite(bound) or abs(offset) > .002 or bound > .002:
            raise ValueError('Rover offset or chrony error bound exceeds 2 ms')
    return status


def validate_gateway(payload, expected_streams, role, interface):
    if payload.get('schema_version') != 1 or payload.get('configured') is not True:
        raise ValueError(f"Gateway diagnostics not configured: {payload.get('missing')}")
    if stream_map(payload['streams']) != expected_streams:
        raise ValueError('Gateway stream IDs/ports differ from the local video config')
    if role == 'base' and payload['capture_interface'] != interface:
        raise ValueError('Gateway capture interface differs from the checked interface')
    if not payload.get('public_base_url'):
        raise ValueError('Gateway artifact upload URL is missing')
    clock_ready(payload['clock'], 'base')
    available = {s['stream_id'] for s in payload['video_streams'] if s.get('available') is True}
    if available != set(expected_streams):
        raise ValueError(f'Expected live streams {sorted(expected_streams)}, got {sorted(available)}')
    return payload


def compare_reports(base, rover):
    errors = []
    if base.get('role') != 'base' or rover.get('role') != 'rover':
        errors.append('Pass the base report first and rover report second')
    if not base.get('passed') or not rover.get('passed'):
        errors.append('Both host preflights must pass')
    for field in ['commit', 'video_config_sha256', 'streams']:
        if not base.get(field) or base.get(field) != rover.get(field):
            errors.append(f'{field} differs between hosts or is missing')
    return errors


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--compare', nargs=2, metavar=('BASE_JSON', 'ROVER_JSON'))
    parser.add_argument('--role', choices=['base', 'rover'])
    parser.add_argument('--interface', help='Actual Rocket-facing local network interface')
    parser.add_argument('--peer-ip', help='Other host address reached through Rocket')
    parser.add_argument('--gateway-url', help='Public base upload URL, e.g. http://192.168.1.101')
    parser.add_argument('--ca-file', help='Trusted CA/certificate file if the URL uses HTTPS')
    parser.add_argument('--video-config', type=Path,
                        default=ROOT / 'rover/ros2_ws/src/mr2_launch/config/video_streams_flat.json')
    parser.add_argument('--expected-commit', help='Full commit SHA supplied with the handoff')
    parser.add_argument('--report', type=Path, help='Write JSON evidence here as well as stdout')
    args = parser.parse_args()
    if args.compare:
        reports = [json.loads(Path(p).read_text()) for p in args.compare]
        errors = compare_reports(*reports)
        print(json.dumps({'passed': not errors, 'errors': errors}, indent=2))
        return int(bool(errors))
    if not all([args.role, args.interface, args.peer_ip, args.gateway_url]):
        parser.error('--role, --interface, --peer-ip and --gateway-url are required')
    report = {'schema_version': 1, 'role': args.role, 'host': socket.gethostname(),
              'sampled_at_epoch_ms': time.time_ns() // 1_000_000, 'checks': []}

    def check(name, action):
        try:
            detail = action()
            report['checks'].append({'name': name, 'passed': True, 'detail': detail})
        except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError) as exc:
            report['checks'].append({'name': name, 'passed': False, 'detail': str(exc)})

    def revision():
        report['commit'] = run('git', '-C', str(ROOT), 'rev-parse', 'HEAD')
        if run('git', '-C', str(ROOT), 'status', '--porcelain', '--untracked-files=no'):
            raise ValueError('Tracked checkout changes are present')
        if args.expected_commit and report['commit'] != args.expected_commit:
            raise ValueError('Checkout does not match the expected full commit SHA')
        return report['commit']

    def config():
        data = json.loads(args.video_config.read_text())
        report['streams'] = stream_map(data['streams'])
        report['video_config_sha256'] = hashlib.sha256(
            json.dumps(data, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        if args.role == 'rover':
            prefix = Path(run('ros2', 'pkg', 'prefix', 'mr2_launch'))
            installed = json.loads((prefix / 'share/mr2_launch/config' / args.video_config.name).read_text())
            if installed != data:
                raise ValueError('Installed rover video config differs; rebuild/source the intended workspace')
            for package in ['mr2_latency_msgs', 'mr2_latency_diagnostics', 'mr2_video_streaming', 'mr2_xbee_bridge']:
                run('ros2', 'pkg', 'prefix', package)
        return report['streams']

    def interface():
        if args.interface == 'lo' or not (Path('/sys/class/net') / args.interface).exists():
            raise ValueError('Rocket measurement requires an existing non-loopback interface')
        routes = json.loads(run('ip', '-j', 'route', 'get', args.peer_ip))
        if not routes or routes[0].get('dev') != args.interface:
            raise ValueError('Route to peer does not use the capture interface')
        return routes[0]['dev']

    def capture_permission():
        binary = shutil.which('tcpdump')
        if not binary:
            raise ValueError('tcpdump is not installed')
        capabilities = run('getcap', str(Path(binary).resolve()))
        if 'cap_net_raw' not in capabilities or 'cap_net_admin' not in capabilities:
            raise ValueError('tcpdump capabilities missing; see install_capture_permissions.bash')
        return capabilities

    def gateway():
        context = ssl.create_default_context(cafile=args.ca_file)
        opener = build_opener(NoRedirect(), HTTPSHandler(context=context))
        url = args.gateway_url.rstrip('/') + '/latency/uplink/status'
        with opener.open(url, timeout=8) as response:
            payload = json.load(response)
        result = validate_gateway(payload, report['streams'], args.role, args.interface)
        if payload.get('source_revision') != report.get('commit'):
            raise ValueError('Gateway process revision differs from checkout; deploy/restart the same commit')
        # Exercise the exact upload origin advertised to the rover, too.
        public_url = payload['public_base_url'].rstrip('/') + '/latency/uplink/status'
        with opener.open(public_url, timeout=8) as response:
            public_status = json.load(response)
        validate_gateway(public_status, report['streams'], args.role, args.interface)
        if public_status.get('source_revision') != report.get('commit'):
            raise ValueError('Public upload URL reaches a gateway running a different revision')
        return result

    check('checkout', revision)
    check('video_config_and_packages', config)
    check('rocket_interface', interface)
    check('capture_capabilities', capture_permission)
    def local_clock():
        status = read_chrony_status()
        status['role'] = args.role
        return clock_ready(status, args.role)
    check('chrony', local_clock)
    if args.role == 'base':
        def node_version():
            version = run('node', '--version')
            if not version.startswith('v24.'):
                raise ValueError(f'Node.js 24 required, found {version}')
            return version
        check('node', node_version)
        check('gstreamer_rtp', lambda: run('gst-inspect-1.0', 'rtpstreampay').splitlines()[0])
    check('gateway_runtime', gateway)
    report['passed'] = all(c['passed'] for c in report['checks'])
    report['scope'] = 'Read-only prerequisites; browser, ROS services and live capture require acceptance tests'
    output = json.dumps(report, indent=2) + '\n'
    if args.report:
        args.report.write_text(output)
    print(output, end='')
    return int(not report['passed'])


if __name__ == '__main__':
    raise SystemExit(main())
