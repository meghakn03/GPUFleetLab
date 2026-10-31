"""Pause only the specified local Fleet API, verify alert firing, then always resume."""
import argparse
import json
import os
import signal
import time
from datetime import datetime, timezone
from pathlib import Path
import httpx

parser=argparse.ArgumentParser()
parser.add_argument('--pid',type=int,required=True)
args=parser.parse_args()
client=httpx.Client(timeout=10)
url='http://127.0.0.1:9090/api/v1/alerts'
report={'at':datetime.now(timezone.utc).isoformat(),'success':False,'checks':[]}

def wait(firing):
    deadline=time.monotonic()+100
    while time.monotonic()<deadline:
        r=client.get(url);r.raise_for_status()
        active=any(a['labels']['alertname']=='FleetAPIUnavailable' and a['state']=='firing' for a in r.json()['data']['alerts'])
        if active==firing:
            report['checks'].append({'check':'FleetAPIUnavailable','firing':active})
            print('PASS API unavailable alert '+('firing' if firing else 'cleared'),flush=True)
            return
        time.sleep(2)
    raise AssertionError('FleetAPIUnavailable did not reach expected state')

# Verify we are about to pause the intended API, rather than an already failed service.
r=client.get('http://127.0.0.1:8017/healthz');r.raise_for_status()
assert r.json()['mode']=='kubernetes'
try:
    os.kill(args.pid,signal.SIGSTOP)
    print('Paused local Fleet API; waiting for the 30-second alert rule',flush=True)
    try:wait(True)
    finally:os.kill(args.pid,signal.SIGCONT)
    wait(False)
    r=client.get('http://127.0.0.1:8017/healthz');r.raise_for_status()
    report['success']=True
finally:
    Path('docs/alert-verification.json').write_text(json.dumps(report,indent=2)+'\n')
