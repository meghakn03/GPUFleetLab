import random
import unittest
from backend.engine import Fleet


def job(**kw):
    return dict(name='train', team='team-a', model='A100', gpus=4, priority=20, duration=300, preemptible=True, **kw) if not kw else {**job(), **kw}

class SchedulerTests(unittest.TestCase):
    def test_borrow_reclaim_failure_recovery(self):
        f = Fleet()
        for _ in range(4): f.submit(job())
        self.assertEqual(f.usage('team-a'), 16)
        urgent = f.submit(job(team='team-b', gpus=8, priority=100, duration=60))
        self.assertEqual(urgent['status'], 'Running')
        self.assertEqual(f.usage('team-a'), 8)
        self.assertEqual(sum(j['preemptions'] for j in f.jobs), 2)
        f.node_health('gpu-worker-01', False)
        self.assertEqual(sum(j['recoveries'] for j in f.jobs), 1)
        self.assertLessEqual(sum(f.usage(t) for t in ('team-a','team-b')),12)
        f.node_health('gpu-worker-01', True)
        self.assertEqual(sum(f.usage(t) for t in ('team-a','team-b')),16)
        f.tick(60)
        self.assertEqual(urgent['status'], 'Completed')
        self.assertEqual(f.usage('team-a'),16)

    def test_no_partial_preemption(self):
        f=Fleet()
        for i in range(4): f.submit(job(preemptible=i==0))
        waiting=f.submit(job(team='team-b',gpus=8,priority=100))
        self.assertEqual(waiting['status'],'Queued')
        self.assertEqual(sum(j['preemptions'] for j in f.jobs),0)

    def test_model_wait_and_cancellation(self):
        f=Fleet();j=f.submit(job(model='H100'))
        f.tick(30)
        self.assertEqual(j['status'],'Queued')
        self.assertIn('H100',j['reason'])
        f.cancel(j['id']);self.assertEqual(j['status'],'Cancelled')
        with self.assertRaises(ValueError): f.cancel(j['id'])

    def test_priority_fifo_wait_and_checkpoint(self):
        f=Fleet();a=f.submit(job(gpus=16,duration=60,preemptible=False))
        b=f.submit(job(team='team-b',priority=90,duration=10));f.tick(60)
        self.assertEqual(b['status'],'Running');self.assertEqual(b['wait'],60)
        f.tick(5);f.node_health(next(iter(b['allocations'])),False)
        self.assertEqual(b['elapsed'],5)
        f.tick(5);self.assertEqual(b['status'],'Completed')

    def test_randomized_capacity_invariants(self):
        r=random.Random(9);f=Fleet()
        for i in range(200):
            action=r.randrange(3)
            if action==0:f.submit(job(gpus=r.randint(1,16),team=r.choice(['team-a','team-b']),priority=r.randint(0,100),duration=r.randint(1,100)))
            elif action==1:f.tick(10)
            else:f.node_health(r.choice(f.nodes)['id'],r.choice([True,False]))
            for n in f.nodes:
                allocated=sum(j['allocations'].get(n['id'],0) for j in f.jobs)
                self.assertLessEqual(allocated,n['capacity'] if n['healthy'] else 0)
            for j in f.jobs:
                self.assertEqual(sum(j['allocations'].values()),j['gpus'] if j['status']=='Running' else 0)

if __name__=='__main__':unittest.main()
