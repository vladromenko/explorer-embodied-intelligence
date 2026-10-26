"""Source/profile admission through authenticated API; no CUDA or device IO."""
import hashlib
import importlib

from fastapi.testclient import TestClient

from explorer.store import Store
from explorer_shared.learning.runtime_profiles import ACT9_CUDA_DETERMINISTIC_PROFILE


def test_server_binds_only_matching_published_visual_cuda_profile_before_enqueue(tmp_path,monkeypatch):
    app=importlib.import_module('explorer.app');scheduler=importlib.import_module('explorer.scheduler');store=Store(tmp_path/'state');lookups=[]
    def selection(job,release,store=None):
        lookups.append(job)
        assert job['kind']=='manipulation_student'
        assert job['config']['cuda_execution_profile']==ACT9_CUDA_DETERMINISTIC_PROFILE
        return '/explicit-software-fixture-not-a-real-GPU-runtime'
    monkeypatch.setattr(scheduler,'executable_for',selection)
    monkeypatch.setattr(app,'STORE',store)
    monkeypatch.setattr(app,'CONFIG',dict(app.CONFIG,nodes=[],password_salt='fixture',password_hash=hashlib.pbkdf2_hmac('sha256',b'fixture',b'fixture',310000).hex()))
    with TestClient(app.app) as client:
        assert client.get('/api/visual-act/profile').status_code==401
        assert client.post('/api/login',json={'password':'fixture'}).status_code==200
        headers={'X-Explorer-CSRF':client.cookies['explorer_csrf']}
        state=client.get('/api/visual-act/profile').json()
        assert state['cuda_available'] and state['tensor_profile']['state_dimension']==9 and state['physical_execution_authorized'] is False
        body={'kind':'manipulation_student','project_id':'01','config':{'task':'candy_to_tray','device':'cuda','image_size':64,'batch_size':2,'chunk_size':4}}
        assert client.post('/api/jobs',json=body).status_code==403
        for field,value in (('image_size',224),('batch_size',True),('chunk_size',20),('cuda_execution_profile','invented')):
            altered={**body,'config':{**body['config'],field:value}}
            assert client.post('/api/jobs',json=altered,headers=headers).status_code==400
        assert store.jobs()==[]
        created=client.post('/api/jobs',json=body,headers=headers)
        assert created.status_code==200,created.text
        config=store.job(created.json()['id'])['config']
        assert config['cuda_execution_profile']==ACT9_CUDA_DETERMINISTIC_PROFILE
        assert config['image_size']==64 and config['batch_size']==2 and config['chunk_size']==4
        assert all('_execution_commit' not in job['config'] for job in lookups)
        def unavailable(*args,**kwargs):raise ValueError('No actual current source binding')
        monkeypatch.setattr(scheduler,'executable_for',unavailable)
        assert client.get('/api/visual-act/profile').json()['cuda_available'] is False
        rejected=client.post('/api/jobs',json=body,headers=headers)
        assert rejected.status_code==409 and len(store.jobs())==1
