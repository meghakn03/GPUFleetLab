import os
import threading
from pathlib import Path
from typing import Literal
from fastapi import FastAPI, HTTPException
from fastapi.responses import PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from .engine import Fleet

app = FastAPI(title='GPU Fleet Manager', version='1.0.0')
fleet = Fleet()
if os.environ.get('FLEET_MODE') == 'kubernetes':
    from .kubernetes import KubernetesFleet
    fleet = KubernetesFleet()
lock = threading.RLock()

@app.exception_handler(RuntimeError)
async def adapter_error(request, exc):
    from fastapi.responses import JSONResponse
    return JSONResponse(status_code=503, content={'detail': str(exc)})

def simulator_only():
    if not isinstance(fleet, Fleet):
        raise HTTPException(409, 'This action is only available in simulator mode. Kubernetes lifecycle is controller driven.')

class Job(BaseModel):
    name: str = Field(min_length=1, max_length=64, pattern=r'^[a-zA-Z0-9][a-zA-Z0-9 _.-]*$')
    team: Literal['team-a', 'team-b'] = 'team-a'
    model: Literal['A100', 'H100'] = 'A100'
    gpus: int = Field(default=4, ge=1, le=16, strict=True)
    priority: int = Field(default=50, ge=0, le=100, strict=True)
    duration: int = Field(default=120, ge=1, le=86400, strict=True)
    preemptible: bool = True

class Tick(BaseModel):
    seconds: int = Field(default=10, ge=1, le=3600, strict=True)

class Health(BaseModel):
    healthy: bool

@app.get('/api/state')
def state():
    with lock:
        return fleet.snapshot()

@app.post('/api/jobs', status_code=201)
def submit(job: Job):
    with lock:
        return fleet.submit(job.model_dump())

@app.post('/api/tick')
def tick(body: Tick):
    with lock:
        simulator_only()
        fleet.tick(body.seconds)
        return fleet.snapshot()

@app.post('/api/nodes/{node_id}')
def health(node_id: str, body: Health):
    with lock:
        try:
            fleet.node_health(node_id, body.healthy)
        except KeyError:
            raise HTTPException(404, 'Node not found')
        return fleet.snapshot()

@app.post('/api/jobs/{job_id}/cancel')
def cancel(job_id: str):
    with lock:
        try:
            fleet.cancel(job_id)
        except KeyError:
            raise HTTPException(404, 'Job not found')
        except ValueError as error:
            raise HTTPException(409, str(error))
        return fleet.snapshot()

@app.post('/api/reset')
def reset():
    global fleet
    with lock:
        simulator_only()
        fleet = Fleet()
        return fleet.snapshot()

@app.post('/api/demo/{step}')
def demo(step: Literal['borrow', 'urgent', 'failure', 'recover']):
    with lock:
        simulator_only()
        if step == 'borrow':
            for i in range(4):
                fleet.submit(Job(name=f'foundation-train-{i+1}', priority=20, duration=300).model_dump())
        elif step == 'urgent':
            fleet.submit(Job(name='urgent-inference', team='team-b', gpus=8, priority=100, duration=90).model_dump())
        else:
            fleet.node_health('gpu-worker-01', step == 'recover')
        return fleet.snapshot()

@app.get('/healthz')
def healthy():
    return {'status': 'ok', 'mode': 'simulator' if isinstance(fleet, Fleet) else 'kubernetes'}

@app.get('/metrics', response_class=PlainTextResponse)
def metrics():
    with lock:
        s = fleet.snapshot()
    lines = ['# HELP fleet_gpu_capacity Healthy simulated GPU capacity', '# TYPE fleet_gpu_capacity gauge', f"fleet_gpu_capacity {sum(n['capacity'] for n in s['nodes'] if n['healthy'])}"]
    for t in s['teams']:
        for key in ('allocated', 'borrowed', 'quota'):
            lines.append(f'fleet_gpu_{key}{{team="{t["id"]}"}} {t[key]}')
    for status in ('Running', 'Queued', 'Completed', 'Cancelled'):
        lines.append(f'fleet_jobs{{status="{status}"}} {sum(j["status"] == status for j in s["jobs"])}')
    waits = [j['wait'] for j in s['jobs'] if j['wait'] is not None]
    lines.extend([f'fleet_queue_wait_seconds_sum {sum(waits)}', f'fleet_queue_wait_seconds_count {len(waits)}', f'fleet_preemptions_total {s.get("preemptions_total", sum(j["preemptions"] for j in s["jobs"]))}', f'fleet_recoveries_total {s.get("recoveries_total", sum(j["recoveries"] for j in s["jobs"]))}'])
    return '\n'.join(lines) + '\n'

static = Path(__file__).parent.parent / 'frontend' / 'dist'
if static.exists():
    app.mount('/', StaticFiles(directory=static, html=True), name='dashboard')
