"""Deterministic, transactional GPU allocation simulator; no hardware telemetry."""
import copy
import uuid


class Fleet:
    def __init__(self):
        self.now = 0
        self.nodes = [dict(id=f'gpu-worker-{i:02}', model='A100', capacity=4, healthy=True) for i in range(1, 5)]
        self.jobs = []
        self.events = []
        self.history = []
        self.record('Cluster ready', 'Four simulated A100 nodes; 8 GPU nominal quota per team.')
        self.sample()

    def record(self, title, detail):
        self.events.insert(0, dict(id=str(uuid.uuid4()), time=self.now, title=title, detail=detail))
        self.events = self.events[:200]

    def usage(self, team):
        return sum(j['gpus'] for j in self.jobs if j['team'] == team and j['status'] == 'Running')

    def placement(self, job, exclude=()):
        remaining = job['gpus']
        allocation = {}
        for node in self.nodes:
            if not node['healthy'] or node['model'] != job['model']:
                continue
            used = sum(j['allocations'].get(node['id'], 0) for j in self.jobs if j['status'] == 'Running' and j['id'] not in exclude)
            take = min(remaining, node['capacity'] - used)
            if take:
                allocation[node['id']] = take
                remaining -= take
        return allocation if remaining == 0 else None

    def schedule(self):
        for job in sorted((j for j in self.jobs if j['status'] == 'Queued'), key=lambda j: (-j['priority'], j['submitted'], j['order'])):
            allocation = self.placement(job)
            victims = []
            if allocation is None and self.usage(job['team']) + job['gpus'] <= 8:
                candidates = sorted((j for j in self.jobs if j['status'] == 'Running' and j['preemptible'] and j['priority'] < job['priority']), key=lambda j: (j['priority'], -j['order']))
                reclaimed = {'team-a': 0, 'team-b': 0}
                for candidate in candidates:
                    team = candidate['team']
                    if team != job['team'] and self.usage(team) - reclaimed[team] - candidate['gpus'] < 8:
                        continue
                    victims.append(candidate)
                    reclaimed[team] += candidate['gpus']
                    allocation = self.placement(job, [v['id'] for v in victims])
                    if allocation is not None:
                        break
            if allocation is None:
                job['reason'] = f"Needs {job['gpus']} {job['model']} GPUs. Insufficient healthy capacity; no eligible lower-priority allocation can be reclaimed within nominal quota."
                continue
            for victim in victims:
                victim.update(status='Queued', allocations={}, reason=f"Preempted for {job['name']}; checkpoint retained.")
                victim['preemptions'] += 1
                self.record('Workload preempted', f"{victim['name']} released {victim['gpus']} GPUs for {job['name']}.")
            job.update(status='Running', allocations=allocation, reason='Admitted within nominal quota.' if self.usage(job['team']) + job['gpus'] <= 8 else 'Admitted by borrowing idle cohort quota.')
            if job['started'] is None:
                job['started'] = self.now
                job['wait'] = self.now - job['submitted']
            self.record('Workload admitted', f"{job['name']}: {job['gpus']} GPUs. {job['reason']}")

    def submit(self, data):
        job = dict(id=str(uuid.uuid4())[:8], order=len(self.jobs), submitted=self.now, started=None, wait=None, elapsed=0, preemptions=0, recoveries=0, status='Queued', allocations={}, reason='Awaiting admission.', **data)
        self.jobs.append(job)
        self.record('Workload submitted', f"{job['name']} → {job['team']}; priority {job['priority']}.")
        self.schedule()
        self.sample()
        return job

    def tick(self, seconds):
        self.now += seconds
        for job in self.jobs:
            if job['status'] == 'Running':
                job['elapsed'] += seconds
                if job['elapsed'] >= job['duration']:
                    job.update(status='Completed', allocations={}, reason='Simulated runtime completed.')
                    self.record('Workload completed', job['name'])
        self.schedule()
        self.sample()

    def node_health(self, node_id, healthy):
        node = next((n for n in self.nodes if n['id'] == node_id), None)
        if node is None:
            raise KeyError(node_id)
        node['healthy'] = healthy
        self.record('Node recovered' if healthy else 'Node failed', f"{node_id}: {'4 GPUs restored' if healthy else '4 GPUs removed from schedulable capacity'}.")
        if not healthy:
            for job in self.jobs:
                if node_id in job['allocations']:
                    job.update(status='Queued', allocations={}, reason=f'Interrupted by {node_id}; awaiting full gang re-admission.')
                    job['recoveries'] += 1
                    self.record('Workload interrupted', f"{job['name']} requeued after {node_id} failed; checkpoint retained.")
        self.schedule()
        self.sample()

    def cancel(self, job_id):
        job = next((j for j in self.jobs if j['id'] == job_id), None)
        if job is None:
            raise KeyError(job_id)
        if job['status'] not in ('Running', 'Queued'):
            raise ValueError('Only active jobs can be cancelled')
        job.update(status='Cancelled', allocations={}, reason='Cancelled by operator.')
        self.record('Workload cancelled', job['name'])
        self.schedule()
        self.sample()

    def sample(self):
        self.history.append(dict(time=self.now, allocated=sum(self.usage(t) for t in ('team-a', 'team-b')), capacity=sum(n['capacity'] for n in self.nodes if n['healthy']), a=self.usage('team-a'), b=self.usage('team-b')))
        self.history = self.history[-100:]

    def snapshot(self):
        return copy.deepcopy(dict(mode='simulator', clock=self.now, nodes=self.nodes, jobs=self.jobs, events=self.events, history=self.history, teams=[dict(id=t, quota=8, allocated=self.usage(t), borrowed=max(0, self.usage(t)-8)) for t in ('team-a', 'team-b')]))
