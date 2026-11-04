import json
import re

with open('logs/tom.log', 'r') as f:
    content = f.read()

reports = []
for line in content.split('\n'):
    line = line.strip()
    if line.startswith('{'):
        try:
            data = json.loads(line)
            if data.get('event') == 'voice.latency.report':
                ctx = data.get('context', {})
                lr = ctx.get('latency_report', {})
                if lr:
                    reports.append({
                        'utterance_id': ctx.get('utterance_id'),
                        'metrics': lr.get('metrics', {})
                    })
        except:
            pass

print(f'Found {len(reports)} latency reports')
for i, r in enumerate(reports):
    print(f'\n--- Turn {i+1} ({r["utterance_id"]}) ---')
    for name, data in r['metrics'].items():
        val = data.get('value_ms')
        status = data.get('status', '')
        target = data.get('target_ms', 0)
        ceiling = data.get('ceiling_ms', 0)
        if val is not None:
            print(f'  {name}: {val:.1f}ms (target={target:.0f}, ceiling={ceiling:.0f}) [{status}]')
        else:
            print(f'  {name}: N/A (target={target:.0f}, ceiling={ceiling:.0f}) [{status}]')