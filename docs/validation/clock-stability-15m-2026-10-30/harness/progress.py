import json
from pathlib import Path

p = Path('/tmp/mr2-clock-stability/15m/partial.json')
if not p.exists():
    print(json.dumps({'status': 'preparing'}))
else:
    try:
        r = json.loads(p.read_text())
        s = r['samples']
        clocks = [{x['key']: x['value'] for x in c['status']['status'][0]['values']}
                  for c in r['clocks']]
        print(json.dumps(dict(samples=len(s), last_s=round(s[-1]['at_s']),
            numeric=sum(x['delay'][0].endswith(' ms') for x in s),
            clock_sync=sum(x['delay'][0] == 'Clock sync' for x in s),
            first_clock_sync_s=next((round(x['at_s']) for x in s if x['delay'][0] == 'Clock sync'), None),
            delay=s[-1]['delay'],
            latest_clock_state=clocks[-1].get('clock_state') if clocks else None,
            frames=s[-1]['frames'])))
    except (ValueError, KeyError, IndexError):
        print(json.dumps({'status': 'snapshot_writing'}))
