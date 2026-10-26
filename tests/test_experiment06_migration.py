"""Compatibility and project enrolment use only isolated stores and HTTP mocks."""
import hashlib
import importlib

import httpx
from fastapi.testclient import TestClient

from explorer.store import Store


def isolated(monkeypatch, tmp_path):
    module=importlib.import_module('explorer.app')
    salt='isolated-migration-test'
    config=dict(root=str(module.ROOT),data_dir=str(tmp_path/'state'),public_url='http://testserver',
        password_salt=salt,password_hash=hashlib.pbkdf2_hmac('sha256',b'test-code',salt.encode(),310000).hex(),nodes=[],ingest_token='not-a-production-token')
    monkeypatch.setattr(module,'CONFIG',config)
    monkeypatch.setattr(module,'STORE',Store(config['data_dir']))
    monkeypatch.setattr(module,'CLIENT',httpx.AsyncClient(transport=httpx.MockTransport(lambda request:httpx.Response(404)),trust_env=False))
    return module


def test_experiment06_redirect_old_jobs_and_models_are_visible_without_rewriting_sources(monkeypatch,tmp_path):
    module=isolated(monkeypatch,tmp_path)
    old=module.STORE.create_job('train_act','personal',{'dataset_manifest':'retained-original','act_steps':1})
    module.STORE.complete_job(old['id'],{'trained':True,'feature_dimension':6,'physical_io':False})
    card={'id':'retained-model','task':'Retained technical hold','source_type':'robot_recording',
        'status':'trained_offline_only','limitations':['No physical skill validation'],
        'evaluation':{'physical_validated':False},'lineage':{'job_id':old['id']},'action_contract':{'names':['j1','j2','j3','j4','j5','gripper']}}
    module.STORE.add_skill(card)
    with module.STORE.connect() as db:
        before=[tuple(row) for row in db.execute('SELECT * FROM events')]
    with TestClient(module.app) as client:
        assert client.post('/api/login',json={'password':'test-code'}).status_code==200
        r=client.get('/learning/so101',follow_redirects=False)
        assert r.status_code==307 and r.headers['location']=='/experiments/06'
        assert client.get('/experiments/06').status_code==200
        state=client.get('/api/state').json()
        assert [row['id'] for row in state['experiments']]==['01','02','03','04','05','06']
        six=next(row for row in state['experiments'] if row['id']=='06')
        assert six['latest_job']['id']==old['id'] and six['latest_job']['project_id']=='personal'
        assert state['project_aliases']=={'personal':'06'}
        assert client.get('/api/skills').json()['skills']==[card]
        csrf={'X-Explorer-CSRF':client.cookies['explorer_csrf']}
        new=client.post('/api/jobs',json={'kind':'train_act','project_id':'personal','config':{}},headers=csrf)
        assert new.status_code==200 and new.json()['project_id']=='06'
        refused=client.post('/api/jobs',json={'kind':'train_act','project_id':'02','config':{'dataset_manifest':'forged-arm-only'}},headers=csrf)
        assert refused.status_code==409
    assert module.STORE.job(old['id'])['project_id']=='personal'
    with module.STORE.connect() as db:
        after=[tuple(row) for row in db.execute('SELECT * FROM events')]
    assert before==after
    assert module.STORE.create_import_job('station_import','personal','original-id')['id']==module.STORE.create_import_job('station_import','06','original-id')['id']


def test_legacy_demo_reviews_share_experiment06_view_and_preserve_bytes(tmp_path):
    from explorer.demonstration_wizard import _annotations
    from explorer_shared.contracts import canonical,event
    store=Store(tmp_path/'state')
    body=event('nx','personal',{'type':'demonstration.review','task_id':'pick_and_place','source_key':'retained-key','outcome':'unknown'})
    with store.connect() as db:
        db.execute('INSERT INTO events VALUES (?,?,?,?,?)',(body['event_id'],'nx',1,body['sha256'],canonical(body).decode()))
    with store.connect() as db:
        original=db.execute('SELECT body FROM events').fetchone()['body']
        assert _annotations(db,'06','pick_and_place')==_annotations(db,'personal','pick_and_place')
        assert db.execute('SELECT body FROM events').fetchone()['body']==original
