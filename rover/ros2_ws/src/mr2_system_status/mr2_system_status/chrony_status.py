"""Read-only chronyc tracking parser for rover diagnostics."""

from __future__ import annotations

import re
import json
import math
from pathlib import Path
import subprocess
import time
from typing import Any, Callable, Dict


RunCommand = Callable[..., subprocess.CompletedProcess[str]]


def read_clock_policy(path='/run/mr2-clock/status.json'):
    try:
        value = json.loads(Path(path).read_text())
        age = time.monotonic() - value['updated_monotonic']
        fresh = 0 <= age < 20 and not value.get('error')
        error_bound = value.get('clock_error_bound_s')
        drift_ppm = value.get('clock_drift_bound_ppm', 10.)
        if not isinstance(drift_ppm, (int, float)) or not math.isfinite(drift_ppm) or drift_ppm < 10:
            drift_ppm = 10.
        if isinstance(error_bound, (int, float)) and math.isfinite(error_bound):
            error_bound += max(0., age) * drift_ppm * 1e-6
        return dict(policy_available=fresh, policy_locked=bool(value['locked']),
                    relative_ready=value.get('relative_ready', False),
                    base_locked=bool(value['base_locked']), base_peer=value['base_peer'],
                    selected_source=value['selected_source'], utc_state=value['utc_state'],
                    policy_error=value.get('error'),
                    internet_offset_s=value.get('internet_probe', {}).get('offset_s'),
                    clock_state=value.get('clock_state', 'invalid'),
                    clock_error_bound_s=error_bound,
                    clock_drift_bound_ppm=drift_ppm,
                    clock_epoch=value.get('clock_epoch'),
                    base_clock_epoch=value.get('base_clock_epoch'))
    except (OSError, ValueError, KeyError, TypeError):
        return dict(policy_available=False, policy_locked=False, base_locked=False,
                    base_peer=None, selected_source=None, utc_state='unverified',
                    policy_error='Clock policy unavailable', internet_offset_s=None)


def require_measurement_clock():
    status = read_chrony_status()
    if not all(status.get(key) for key in ('available', 'synchronized', 'policy_available', 'policy_locked', 'base_locked', 'relative_ready')):
        raise RuntimeError('Clock is not locked and synchronized to base')
    if status.get('selected_source') != status.get('base_peer') or not status.get('base_peer'):
        raise RuntimeError('Clock source is not the locked base')
    offset = status.get('system_time_offset_s')
    delay, dispersion = status.get('root_delay_s'), status.get('root_dispersion_s')
    if not all(isinstance(x, (int, float)) and math.isfinite(x) for x in (offset, delay, dispersion)):
        raise RuntimeError('Clock uncertainty is unavailable')
    bound = abs(offset) + dispersion + abs(delay) / 2
    reported = status.get('clock_error_bound_s')
    if isinstance(reported, (int, float)):
        if not math.isfinite(reported) or reported < 0:
            raise RuntimeError('Clock uncertainty is unavailable')
        bound = max(bound, reported)
    if bound > .002:
        raise RuntimeError('Clock offset or uncertainty exceeds 2 ms')


def _number(fields: Dict[str, str], name: str) -> float | None:
    raw = fields.get(name)
    if raw is None:
        return None
    try:
        return float(raw.split()[0])
    except (ValueError, IndexError):
        return None


def parse_chronyc_tracking(
    output: str, sampled_at_epoch_ms: int | None = None
) -> Dict[str, Any]:
    fields: Dict[str, str] = {}
    for line in output.splitlines():
        if ":" not in line:
            continue
        name, value = line.split(":", 1)
        fields[name.strip()] = value.strip()

    leap_status = fields.get("Leap status", "")
    stratum = _number(fields, "Stratum")
    reference_raw = fields.get("Reference ID", "")
    reference_match = re.fullmatch(r"([^\s]+)(?:\s+\((.*)\))?", reference_raw)
    system_match = re.fullmatch(
        r"([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)\s+"
        r"seconds\s+(fast|slow)\s+of\s+NTP\s+time",
        fields.get("System time", ""),
        flags=re.IGNORECASE,
    )
    if (
        not leap_status
        or stratum is None
        or reference_match is None
        or system_match is None
    ):
        raise ValueError("chronyc tracking output is missing required fields")

    magnitude = abs(float(system_match.group(1)))
    system_time_offset_s = (
        magnitude if system_match.group(2).lower() == "fast" else -magnitude
    )
    synchronized = leap_status.lower() != "not synchronised" and stratum > 0
    return {
        "schema_version": 1,
        "role": "rover",
        "available": True,
        "synchronized": synchronized,
        "reference_id": reference_match.group(1),
        "reference_name": reference_match.group(2) or None,
        "stratum": int(stratum),
        "system_time_offset_s": system_time_offset_s,
        "last_offset_s": _number(fields, "Last offset"),
        "rms_offset_s": _number(fields, "RMS offset"),
        "root_delay_s": _number(fields, "Root delay"),
        "root_dispersion_s": _number(fields, "Root dispersion"),
        "update_interval_s": _number(fields, "Update interval"),
        "leap_status": leap_status,
        "sampled_at_epoch_ms": sampled_at_epoch_ms
        if sampled_at_epoch_ms is not None
        else time.time_ns() // 1_000_000,
        "error": None,
    }


def unavailable_status(
    error: str, sampled_at_epoch_ms: int | None = None
) -> Dict[str, Any]:
    return {
        "schema_version": 1,
        "role": "rover",
        "available": False,
        "synchronized": False,
        "reference_id": None,
        "reference_name": None,
        "stratum": None,
        "system_time_offset_s": None,
        "last_offset_s": None,
        "rms_offset_s": None,
        "root_delay_s": None,
        "root_dispersion_s": None,
        "update_interval_s": None,
        "leap_status": None,
        "sampled_at_epoch_ms": sampled_at_epoch_ms
        if sampled_at_epoch_ms is not None
        else time.time_ns() // 1_000_000,
        "error": error,
    }


def read_chrony_status(
    runner: RunCommand = subprocess.run, timeout_s: float = 1.5
) -> Dict[str, Any]:
    sampled_at_epoch_ms = time.time_ns() // 1_000_000
    try:
        result = runner(
            ["chronyc", "-n", "tracking"],
            check=True,
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
        return {**parse_chronyc_tracking(result.stdout, sampled_at_epoch_ms), **read_clock_policy()}
    except FileNotFoundError:
        return unavailable_status("chronyc is not installed", sampled_at_epoch_ms)
    except (subprocess.SubprocessError, ValueError):
        return unavailable_status("chronyc tracking failed", sampled_at_epoch_ms)
