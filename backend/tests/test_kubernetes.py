import json
import subprocess
from unittest.mock import patch
import pytest
from backend.kubernetes import KubernetesFleet


def test_submission_is_suspended_and_uses_explicit_context():
    f=KubernetesFleet()
    with patch('backend.kubernetes.subprocess.run') as run:
        run.return_value=subprocess.CompletedProcess([],0,'{"kind":"Job"}','')
        f.submit(dict(name='test; echo unsafe',team='team-b',model='A100',gpus=8,priority=100))
        args=run.call_args.args[0]
        assert args[:3]==['kubectl','--context','kind-gpu-fleet']
        body=json.loads(run.call_args.kwargs['input'])
        assert body['spec']['suspend'] is True
        assert body['spec']['parallelism']==8
        assert body['metadata']['namespace']=='team-b'
        assert body['metadata']['labels']['kueue.x-k8s.io/priority-class']=='urgent'
        assert body['metadata']['annotations']['fleet/name']=='test; echo unsafe'
        assert 'shell' not in run.call_args.kwargs


def test_missing_kubectl_is_actionable():
    with patch('backend.kubernetes.subprocess.run',side_effect=FileNotFoundError('kubectl')):
        with pytest.raises(RuntimeError,match='control plane unavailable'):
            KubernetesFleet().snapshot()


def test_cancel_handles_kubectl_delete_name_output():
    with patch('backend.kubernetes.subprocess.run') as run:
        run.return_value=subprocess.CompletedProcess([],0,'job.batch/fleet-job-123 deleted\n','')
        result=KubernetesFleet().cancel('team-a:fleet-job-123')
        assert result['message']=='job.batch/fleet-job-123 deleted'
        assert run.call_args.args[0][-2:]==['-o','name']


def test_observed_history_first_admission_and_event_counters():
    fleet=KubernetesFleet()
    raw={
        'nodes':[{'metadata':{'name':'gpu-worker-01','labels':{'type':'kwok'}},'spec':{},'status':{'allocatable':{'nvidia.com/gpu':'4'},'conditions':[{'type':'Ready','status':'True'}]}}],
        'pods':[],
        'jobs':[{'metadata':{'name':'example','namespace':'team-a','uid':'job-uid','creationTimestamp':'2026-10-31T18:00:00Z','annotations':{'fleet/recoveries':'2'}},'spec':{'parallelism':4,'template':{'spec':{'containers':[{'resources':{'requests':{'nvidia.com/gpu':1}}}]}}}}],
        'workloads.kueue.x-k8s.io':[{'metadata':{'uid':'workload-uid','namespace':'team-a','ownerReferences':[{'uid':'job-uid'}]},'status':{'conditions':[{'type':'Admitted','status':'True','lastTransitionTime':'2026-10-31T18:10:00Z'}]}}],
        'events':[
            {'metadata':{'uid':'event-1','namespace':'team-a','creationTimestamp':'2026-10-31T18:00:05Z'},'involvedObject':{'uid':'workload-uid'},'reason':'Admitted','firstTimestamp':'2026-10-31T18:00:05Z'},
            {'metadata':{'uid':'event-2','namespace':'team-a','creationTimestamp':'2026-10-31T18:01:00Z'},'involvedObject':{'uid':'workload-uid'},'reason':'Preempted','count':3},
        ],
    }
    with patch.object(fleet,'items',side_effect=lambda resource:raw[resource]):
        state=fleet.snapshot()
        assert state['jobs'][0]['wait']==5
        assert state['preemptions_total']==3
        assert state['recoveries_total']==2
        assert state['history'][0]['capacity']==4
        raw['jobs']=[]
        assert fleet.snapshot()['preemptions_total']==3
        assert fleet.snapshot()['recoveries_total']==2
