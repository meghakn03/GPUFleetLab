"""Optional real control plane adapter. All kubectl calls target the named lab."""
import json
import subprocess
from datetime import datetime, timezone


class KubernetesFleet:
    def __init__(self):
        self.context = 'kind-gpu-fleet'
        self.history = []
        self.preemption_counts = {}
        self.recovery_counts = {}

    def command(self, *args, body=None, json_output=True):
        try:
            result = subprocess.run(['kubectl', '--context', self.context, *args], input=json.dumps(body) if body else None, text=True, capture_output=True, timeout=30)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise RuntimeError('Kubernetes control plane unavailable: ' + str(exc)) from exc
        if result.returncode:
            raise RuntimeError(result.stderr.strip())
        if not json_output:
            return {'message': result.stdout.strip()}
        return json.loads(result.stdout) if result.stdout.strip() else {}

    def items(self, resource):
        return self.command('get', resource, '-A', '-o', 'json')['items']

    def snapshot(self):
        nodes = []
        for n in self.items('nodes'):
            if n['metadata'].get('labels', {}).get('type') != 'kwok': continue
            nodes.append(dict(id=n['metadata']['name'],model=n['metadata']['labels'].get('gpu.fleet/model','A100'),capacity=int(n['status'].get('allocatable',{}).get('nvidia.com/gpu',0)),healthy=not n['spec'].get('unschedulable',False) and any(c['type']=='Ready' and c['status']=='True' for c in n['status'].get('conditions',[]))))
        pods=self.items('pods');workloads=self.items('workloads.kueue.x-k8s.io');jobs=[]
        raw_events = self.items('events')
        for raw in self.items('jobs'):
            meta=raw['metadata'];team=meta['namespace']
            if team not in ('team-a','team-b'):continue
            spec=raw['spec'];status=raw.get('status',{});name=meta['name']
            w=next((w for w in workloads if w['metadata']['namespace']==team and any(o.get('uid')==meta['uid'] for o in w['metadata'].get('ownerReferences',[]))),{})
            conditions=w.get('status',{}).get('conditions',[])
            workload_uid = w.get('metadata', {}).get('uid')
            related_events = [e for e in raw_events if e.get('involvedObject', {}).get('uid') == workload_uid]
            preemptions = sum(e.get('count', 1) for e in related_events if e.get('reason') == 'Preempted')
            recovery_count = int(meta.get('annotations', {}).get('fleet/recoveries', '0'))
            self.preemption_counts[meta['uid']] = max(self.preemption_counts.get(meta['uid'], 0), preemptions)
            self.recovery_counts[meta['uid']] = recovery_count
            allocation={}
            for p in pods:
                if p['metadata']['namespace']==team and p['metadata'].get('labels',{}).get('job-name')==name and p.get('status',{}).get('phase') in ('Running','Pending') and p['spec'].get('nodeName'):
                    node=p['spec']['nodeName'];allocation[node]=allocation.get(node,0)+int(p['spec']['containers'][0].get('resources',{}).get('requests',{}).get('nvidia.com/gpu',0))
            complete=any(c['type']=='Complete' and c['status']=='True' for c in status.get('conditions',[]))
            state='Completed' if complete else 'Running' if allocation else 'Queued'
            admitted=next((c for c in conditions if c['type']=='Admitted' and c['status']=='True'),None)
            created=datetime.fromisoformat(meta['creationTimestamp'].replace('Z','+00:00'))
            admission_times = [e.get('firstTimestamp') or e.get('eventTime') or e['metadata']['creationTimestamp'] for e in related_events if e.get('reason') == 'Admitted']
            first_admitted = min(admission_times) if admission_times else admitted['lastTransitionTime'] if admitted else None
            wait=max(0,int((datetime.fromisoformat(first_admitted.replace('Z','+00:00'))-created).total_seconds())) if first_admitted else None
            req=spec['template']['spec']['containers'][0].get('resources',{}).get('requests',{})
            jobs.append(dict(id=team+':'+name,name=meta.get('annotations',{}).get('fleet/name',name),team=team,gpus=int(req.get('nvidia.com/gpu',0))*spec.get('parallelism',1),model=spec['template']['spec'].get('nodeSelector',{}).get('gpu.fleet/model','A100'),priority=100 if meta.get('labels',{}).get('kueue.x-k8s.io/priority-class')=='urgent' else 20,status=state,allocations=allocation,reason='; '.join(c.get('message',c['type']) for c in conditions[-3:]) or 'Waiting for Kueue reconciliation.',wait=wait,submitted=int(created.timestamp()),elapsed=0,preemptions=preemptions,recoveries=recovery_count))
        teams=[dict(id=t,quota=8,allocated=sum(sum(j['allocations'].values()) for j in jobs if j['team']==t),borrowed=0) for t in ('team-a','team-b')]
        for t in teams:t['borrowed']=max(0,t['allocated']-8)
        events=[]
        for e in raw_events:
            if e['metadata']['namespace'] in ('team-a','team-b') or e.get('reason','').startswith('FleetNode'):
                events.append(dict(id=e['metadata']['uid'],time=e.get('lastTimestamp') or e['metadata']['creationTimestamp'],title=e.get('reason','Event'),detail=e.get('message','')))
        now = int(datetime.now(timezone.utc).timestamp())
        sample = dict(time=now, allocated=sum(t['allocated'] for t in teams), capacity=sum(n['capacity'] for n in nodes if n['healthy']), a=teams[0]['allocated'], b=teams[1]['allocated'])
        if not self.history or self.history[-1]['time'] != now:
            self.history.append(sample)
            self.history = self.history[-100:]
        jobs.sort(key=lambda j: (j['submitted'], j['id']))
        events.sort(key=lambda e: e['time'], reverse=True)
        return dict(mode='kubernetes',clock=now,nodes=nodes,jobs=jobs,teams=teams,events=events[:200],history=list(self.history),preemptions_total=sum(self.preemption_counts.values()),recoveries_total=sum(self.recovery_counts.values()))

    def submit(self,data):
        pod=dict(restartPolicy='Never',nodeSelector={'gpu.fleet/model':data['model']},tolerations=[dict(key='kwok.x-k8s.io/node',operator='Equal',value='fake',effect='NoSchedule')],containers=[dict(name='worker',image='registry.k8s.io/pause:3.10',resources={'requests':{'nvidia.com/gpu':1},'limits':{'nvidia.com/gpu':1}})])
        # KWOK simulates lifecycle; this does not execute user code or train a model.
        body=dict(apiVersion='batch/v1',kind='Job',metadata=dict(generateName='fleet-job-',namespace=data['team'],annotations={'fleet/name':data['name']},labels={'kueue.x-k8s.io/queue-name':'gpu-queue','kueue.x-k8s.io/priority-class':'urgent' if data['priority']>=80 else 'batch'}),spec=dict(suspend=True,parallelism=data['gpus'],completions=data['gpus'],template=dict(metadata={'annotations':{'fleet/duration':str(data.get('duration',300))+'s'}},spec=pod)))
        return self.command('create','-f','-','-o','json',body=body)

    def node_health(self,node_id,healthy):
        snapshot = self.snapshot()
        if node_id not in {n['id'] for n in snapshot['nodes']}:raise KeyError(node_id)
        if next(n for n in snapshot['nodes'] if n['id']==node_id)['healthy'] == healthy:
            return
        # Cordon then delete affected fake pods: Jobs recreate them, scheduler avoids cordoned node.
        self.command('patch','node',node_id,'--type=merge','-p',json.dumps({'spec':{'unschedulable':not healthy}}),'-o','json')
        now = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
        self.command('create', '-f', '-', '-o', 'json', body=dict(apiVersion='v1',kind='Event',metadata={'generateName':'fleet-node-','namespace':'default'},involvedObject={'apiVersion':'v1','kind':'Node','name':node_id},reason='FleetNodeRecovered' if healthy else 'FleetNodeFailed',message=node_id+(': schedulable capacity restored' if healthy else ': cordoned; affected fake pods removed for rescheduling'),type='Normal' if healthy else 'Warning',firstTimestamp=now,lastTimestamp=now,count=1,source={'component':'gpu-fleet-api'}))
        if not healthy:
            for job in snapshot['jobs']:
                if job['allocations'].get(node_id):
                    namespace, name = job['id'].split(':', 1)
                    self.command('annotate','job',name,'-n',namespace,'fleet/recoveries='+str(job['recoveries']+1),'--overwrite','-o','json')
            for team in ('team-a','team-b'):
                self.command('delete','pods','-n',team,'--field-selector','spec.nodeName='+node_id,'--wait=false','-o','name',json_output=False)

    def cancel(self,job_id):
        parts=job_id.split(':',1)
        if len(parts)!=2 or parts[0] not in ('team-a','team-b'):raise KeyError(job_id)
        return self.command('delete','job',parts[1],'-n',parts[0],'-o','name',json_output=False)
