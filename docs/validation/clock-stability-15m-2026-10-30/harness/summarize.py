import collections
import datetime
import json
import math
from pathlib import Path
import statistics
import sys


def summarize(path):
    r = json.loads(Path(path).read_text())
    start = datetime.datetime.fromisoformat(r['started_at'].replace('Z', '+00:00'))
    end = datetime.datetime.fromisoformat(r['ended_at'].replace('Z', '+00:00'))
    states = []
    for row in r['clocks']:
        if start.timestamp()*1000 <= row['observed_at'] <= end.timestamp()*1000:
            states.append({v['key']: v['value'] for v in row['status']['status'][0]['values']})
    def describe(values):
        values = [v for v in values if math.isfinite(v)]
        return dict(min=min(values), median=statistics.median(values), max=max(values)) if values else None
    def clock_field(name, absolute=False, scale=1):
        values = [float(s[name]) for s in states if s.get(name) not in (None, '', 'None')]
        return describe([(abs(v) if absolute else v)*scale for v in values])
    samples = r['samples']
    gaps = []
    for s in samples:
        if s['delay'][0] == 'Clock sync':
            if gaps and gaps[-1]['end_s'] == round(s['at_s']) - 1:
                gaps[-1]['end_s'] = round(s['at_s'])
            else:
                gaps.append(dict(start_s=round(s['at_s']), end_s=round(s['at_s'])))
    def displayed(index):
        return describe([float(s['delay'][index].split()[0]) for s in samples
                         if s['delay'][0].endswith(' ms') and s['delay'][index].endswith(' ms')])
    elapsed = samples[-1]['at_s'] - samples[0]['at_s']
    frames = {}
    for name, count in samples[-1]['frames'].items():
        initial = samples[0]['frames'].get(name, 0)
        max_gap = gap = 0
        for a, b in zip(samples, samples[1:]):
            gap = gap+1 if a['frames'].get(name, 0) == b['frames'].get(name, 0) else 0
            max_gap = max(max_gap, gap)
        frames[name] = dict(frames_during_observation=count-initial,
                           average_decoded_fps=(count-initial)/elapsed,
                           longest_no_progress_samples=max_gap)
    return dict(started_at_utc=r['started_at'], ended_at_utc=r['ended_at'],
                started_at_kst=start.astimezone(datetime.timezone(datetime.timedelta(hours=9))).isoformat(),
                duration_observed_s=r['duration_observed_s'], samples=len(samples),
                numeric=sum(s['delay'][0].endswith(' ms') for s in samples),
                clock_sync=sum(s['delay'][0] == 'Clock sync' for s in samples),
                clock_sync_intervals_s=gaps, ui_clock_ready_at_start=r['clock_ready_at_start'],
                browser_clock_warmup_s=r['clock_warmup_s'],
                displayed_one_second_median_ms=displayed(0), displayed_one_second_p95_ms=displayed(1),
                running_minimum_last=samples[-1]['delay'][2],
                clock_messages_during_observation=len(states),
                clock_states=dict(collections.Counter(s.get('clock_state') for s in states)),
                estimated_abs_system_offset_ms=clock_field('system_time_offset_s', True, 1000),
                clock_error_bound_ms=clock_field('clock_error_bound_s', scale=1000),
                root_delay_ms=clock_field('root_delay_s', scale=1000),
                root_dispersion_ms=clock_field('root_dispersion_s', scale=1000),
                rover_clock_epochs=sorted(set(s.get('clock_epoch', '') for s in states)),
                base_clock_epochs=sorted(set(s.get('base_clock_epoch', '') for s in states)),
                frames=frames, layout=r['layout'], browser_errors=r['errors'],
                run_error=r.get('error'))


if __name__ == '__main__':
    print(json.dumps(summarize(sys.argv[1]), indent=2))
