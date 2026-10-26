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


def test_source04_redirect_old_jobs_and_models_are_visible_without_rewriting_sources(monkeypatch,tmp_path):
    module=isolated(monkeypatch,tmp_path)
    old=module.STORE.create_job('train_act','personal',{'dataset_manifest':'retained-original','act_steps':1})
    # Explicit retained v1 fixture: current create_job correctly canonicalizes aliases.
    with module.STORE.connect() as db:
        db.execute('UPDATE jobs SET project_id=? WHERE id=?',('personal',old['id']))
        db.execute('DELETE FROM experiment_runtime_identity WHERE entity_kind=? AND entity_id=?',('job',old['id']))
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
        assert r.status_code==307 and r.headers['location']=='/experiments/04'
        assert client.get('/experiments/04').status_code==200
        state=client.get('/api/state').json()
        assert [row['id'] for row in state['experiments']]==['01','02','03','04','05','06']
        source=next(row for row in state['experiments'] if row['id']=='04')
        assert source['latest_job']['id']==old['id'] and source['latest_job']['project_id']=='04'
        assert source['latest_job']['legacy_experiment_id']=='personal'
        assert state['project_aliases']=={'personal':'04'}
        view=client.get('/api/skills').json()['skills'][0]
        assert view['project_id']=='04' and all(view[key]==value for key,value in card.items())
        csrf={'X-Explorer-CSRF':client.cookies['explorer_csrf']}
        new=client.post('/api/jobs',json={'kind':'train_act','project_id':'personal','config':{}},headers=csrf)
        assert new.status_code==200 and new.json()['project_id']=='04'
        refused=client.post('/api/jobs',json={'kind':'train_act','project_id':'02','config':{'dataset_manifest':'forged-arm-only'}},headers=csrf)
        assert refused.status_code==409
    assert module.STORE.job(old['id'])['project_id']=='04'
    with module.STORE.connect() as db:
        after=[tuple(row) for row in db.execute('SELECT * FROM events')]
        assert db.execute('SELECT project_id FROM jobs WHERE id=?',(old['id'],)).fetchone()[0]=='personal'
        assert __import__('json').loads(db.execute('SELECT body FROM skills').fetchone()[0])==card
    assert before==after
    assert module.STORE.create_import_job('station_import','personal','original-id')['id']==module.STORE.create_import_job('station_import','04','original-id')['id']


def test_legacy_demo_reviews_share_source04_view_and_preserve_bytes(tmp_path):
    from explorer.demonstration_wizard import _annotations
    from explorer_shared.contracts import canonical,event
    store=Store(tmp_path/'state')
    body=event('nx','personal',{'type':'demonstration.review','task_id':'pick_and_place','source_key':'retained-key','outcome':'unknown'})
    with store.connect() as db:
        db.execute('INSERT INTO events VALUES (?,?,?,?,?)',(body['event_id'],'nx',1,body['sha256'],canonical(body).decode()))
    with store.connect() as db:
        original=db.execute('SELECT body FROM events').fetchone()['body']
        assert _annotations(db,'04','pick_and_place',store.experiment_projection)==_annotations(db,'personal','pick_and_place',store.experiment_projection)
        assert not _annotations(db,'06','pick_and_place',store.experiment_projection)
        assert db.execute('SELECT body FROM events').fetchone()['body']==original
