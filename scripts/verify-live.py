"""Opt-in integration check against the disposable lab. Creates only tagged test jobs."""
import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path
import httpx

parser = argparse.ArgumentParser()
parser.add_argument('--api', default='http://127.0.0.1:8017')
parser.add_argument('--prometheus', default='http://127.0.0.1:9090')
parser.add_argument('--grafana', default='http://127.0.0.1:3000')
parser.add_argument('--report', default='docs/live-verification.json')
args = parser.parse_args()
client = httpx.Client(timeout=45)
checks = []
created = []
failed_node = None
success = False


def api(path='state', body=None):
    url = args.api + '/api/' + path
    response = client.get(url) if body is None else client.post(url, json=body)
    response.raise_for_status()
    return response.json()


def check(name, detail):
    checks.append(dict(check=name, detail=detail))
    print('PASS', name, json.dumps(detail), flush=True)


def wait_for(name, predicate, seconds=180):
    deadline = time.monotonic() + seconds
    last = None
    while time.monotonic() < deadline:
        last = api()
        if predicate(last):
            check(name, {'teams': last['teams'], 'jobs': [(j['name'], j['status']) for j in last['jobs']]})
            return last
        time.sleep(2)
    raise AssertionError(name + ' timed out: ' + json.dumps(last))


def submit(name, **kw):
    data = dict(name='verify-' + name, team='team-a', model='A100', gpus=4, priority=20, duration=1800)
    data.update(kw)
    raw = api('jobs', data)
    identifier = raw['metadata']['namespace'] + ':' + raw['metadata']['name']
    created.append(identifier)
    return identifier


def prom(path, **params):
    r = client.get(args.prometheus + '/api/v1/' + path, params=params)
    r.raise_for_status()
    return r.json()['data']


def wait_alert(name, active=True, seconds=100):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        alerts = prom('alerts')['alerts']
        firing = any(a['labels']['alertname'] == name and a['state'] == 'firing' for a in alerts)
        if firing == active:
            check(name + (' firing' if active else ' cleared'), {'active': active, 'alerts': [{'labels':a['labels'],'value':a.get('value')} for a in alerts if a['labels']['alertname']==name]})
            return
        time.sleep(2)
    raise AssertionError('Alert did not reach expected state: ' + name)

try:
    s = api()
    assert s['mode'] == 'kubernetes'
    assert len(s['nodes']) == 4
    assert sum(n['capacity'] for n in s['nodes'] if n['healthy']) == 16
    assert not [j for j in s['jobs'] if j['status'] in ('Queued', 'Running')], 'Run this check on an idle lab'
    check('Kubernetes inventory', {'nodes': len(s['nodes']), 'gpus': 16})
    for i in range(4): submit('borrow-' + str(i))
    wait_for('Team A borrows 8 GPUs', lambda s: s['teams'][0]['allocated'] == 16 and s['teams'][0]['borrowed'] == 8)
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        samples = prom('query', query='fleet_gpu_borrowed{job="gpu-fleet",team="team-a"}')['result']
        if samples and float(samples[0]['value'][1]) == 8:
            check('Borrowing sampled by Prometheus', {'borrowed': 8})
            break
        time.sleep(2)
    else:
        raise AssertionError('Prometheus did not sample borrowed quota')
    urgent = submit('urgent', team='team-b', gpus=8, priority=100)
    s = wait_for('Urgent Team B reclaims nominal share', lambda s: s['teams'][1]['allocated'] == 8 and s['teams'][0]['allocated'] == 8 and sum(j['status']=='Queued' for j in s['jobs']) == 2)
    assert any('preempt' in (e['title']+' '+e['detail']).lower() for e in s['events']), 'Missing Kueue preemption event'
    check('Kueue preemption event', {'observed': True})
    wait_alert('FleetJobsWaiting', seconds=150)
    failed_node = s['nodes'][0]['id']
    api('nodes/' + failed_node, {'healthy': False})
    s = wait_for('Node fault reduces schedulable capacity', lambda s: sum(n['capacity'] for n in s['nodes'] if n['healthy']) == 12 and all(not j['allocations'].get(failed_node) for j in s['jobs']))
    wait_alert('FleetCapacityReduced')
    api('nodes/' + failed_node, {'healthy': True})
    failed_node = None
    wait_for('Node recovery restores 16 GPU allocation', lambda s: sum(n['capacity'] for n in s['nodes'] if n['healthy']) == 16 and sum(t['allocated'] for t in s['teams']) == 16)
    wait_alert('FleetCapacityReduced', False)
    api('jobs/' + urgent + '/cancel', {})
    created.remove(urgent)
    wait_for('Borrowing resumes after urgent job cancellation', lambda s: s['teams'][0]['allocated'] == 16)
    for identifier in list(created):
        api('jobs/' + identifier + '/cancel', {})
        created.remove(identifier)
    wait_for('Allocations released', lambda s: sum(t['allocated'] for t in s['teams']) == 0)
    short = submit('completion', duration=5)
    wait_for('KWOK job completion', lambda s: any(j['id'] == short and j['status'] == 'Completed' for j in s['jobs']))
    h100 = submit('unsupported-flavor', model='H100')
    wait_for('Unsupported H100 remains queued', lambda s: any(j['id']==h100 and j['status']=='Queued' and 'Waiting for Kueue' not in j['reason'] for j in s['jobs']))
    targets = prom('targets')['activeTargets']
    assert targets and all(t['health'] == 'up' for t in targets), targets
    check('Prometheus scrape healthy', {'targets': len(targets)})
    result = prom('query', query='fleet_gpu_capacity{job="gpu-fleet"}')['result']
    assert result and float(result[0]['value'][1]) == 16, result
    check('Prometheus live capacity metric', {'capacity': 16})
    for path in ('/api/health', '/api/dashboards/uid/gpu-fleet', '/api/datasources/proxy/uid/prometheus/api/v1/query?query=fleet_gpu_capacity'):
        r = client.get(args.grafana + path)
        r.raise_for_status()
        check('Grafana ' + path.split('?')[0], {'status': r.status_code})
    success = True
finally:
    if failed_node:
        api('nodes/' + failed_node, {'healthy': True})
    for identifier in created:
        try: api('jobs/' + identifier + '/cancel', {})
        except httpx.HTTPError as exc: print('Cleanup failed:', identifier, str(exc), flush=True)
    Path(args.report).write_text(json.dumps(dict(at=datetime.now(timezone.utc).isoformat(), mode='kubernetes', success=success, checks=checks), indent=2) + '\n')
