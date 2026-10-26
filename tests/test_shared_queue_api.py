"""Actual auth/CSRF API and durable metadata; no worker or hardware started."""
import hashlib
import importlib
from fastapi.testclient import TestClient

from explorer.store import Store


def test_queue_metadata_CAS_preserves_raw_scientific_job_and_explicit_pause_intent(tmp_path,monkeypatch):
    module=importlib.import_module('explorer.app');store=Store(tmp_path/'state')
    first=store.create_job('so101_skill_train','04',{'backend':'act','original_setting':'preserve-me'})
    second=store.create_job('rosmaster_skill_train','03',{'backend':'act'})
    with store.connect() as db:raw_before=tuple(db.execute('SELECT config,result FROM jobs WHERE id=?',(first['id'],)).fetchone())
    monkeypatch.setattr(module,'STORE',store)
    monkeypatch.setattr(module,'CONFIG',dict(module.CONFIG,nodes=[],password_salt='fixture',password_hash=hashlib.pbkdf2_hmac('sha256',b'fixture',b'fixture',310000).hex()))
    with TestClient(module.app) as client:
        assert client.get('/api/queue').status_code==401
        assert client.post('/api/login',json={'password':'fixture'}).status_code==200
        headers={'X-Explorer-CSRF':client.cookies['explorer_csrf']}
        preview=client.get('/api/queue');assert preview.status_code==200
        assert preview.json()['authoritative_table']=='jobs' and len(preview.json()['jobs'])==2
        with store.connect() as db:assert db.execute('SELECT COUNT(*) FROM job_scheduling').fetchone()[0]==0
        record=next(row for row in preview.json()['jobs'] if row['id']==first['id'])['scheduling']
        body=dict(options=dict(priority=4,dependencies=[second['id']]),expected_revision=record['revision'])
        path='/api/jobs/'+first['id']+'/schedule'
        assert client.post(path,json=body).status_code==403
        changed=client.post(path,json=body,headers=headers);assert changed.status_code==200
        assert changed.json()['metadata']['priority']==4
        assert client.post(path,json=body,headers=headers).status_code==409
        current=store.schedule_status(second['id'])
        assert client.post('/api/jobs/'+second['id']+'/schedule',json=dict(options={'dependencies':[first['id']]},expected_revision=current['revision']),headers=headers).status_code==409
        assert client.post('/api/jobs/'+first['id']+'/pause',json={},headers=headers).status_code==200
        assert store.schedule_status(first['id'])['runtime']['user_pause_requested'] is True
        with store.connect() as db:
            raw_after=tuple(db.execute('SELECT config,result FROM jobs WHERE id=?',(first['id'],)).fetchone())
            assert raw_after==raw_before and db.execute('SELECT COUNT(*) FROM job_scheduling_history').fetchone()[0]>=1
        bad=dict(options={'resources':{'gpu_slots':1}},expected_revision=store.schedule_status(second['id'])['revision'])
        assert client.post('/api/jobs/'+second['id']+'/schedule',json=bad,headers=headers).status_code==409
