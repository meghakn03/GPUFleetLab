from fastapi.testclient import TestClient
from backend.app import app
client=TestClient(app)

def test_api_demo_and_validation():
    assert client.post('/api/reset').status_code==200
    assert client.post('/api/jobs',json={'name':'invalid','gpus':0}).status_code==422
    assert client.post('/api/jobs',json={'name':'invalid','team':'other'}).status_code==422
    assert client.post('/api/demo/borrow').json()['teams'][0]['borrowed']==8
    s=client.post('/api/demo/urgent').json()
    assert s['teams'][1]['allocated']==8
    assert client.post('/api/nodes/missing',json={'healthy':False}).status_code==404
    assert client.post('/api/tick',json={'seconds':-10}).status_code==422
    assert 'fleet_gpu_allocated{team="team-b"} 8' in client.get('/metrics').text
    assert client.get('/healthz').json()['status']=='ok'
