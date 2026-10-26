"""Temporary router with actual central auth/CSRF middleware; no live jobs."""
import hashlib
import importlib
import json
import time

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from test_study_job import prepared
from test_study_evidence import study
from explorer import study_api,study_job


@pytest.fixture
def interface(prepared,monkeypatch):
    store,source=prepared;central=importlib.import_module('explorer.app')
    monkeypatch.setattr(central,'STORE',store)
    monkeypatch.setattr(study_api,'registered_studies',lambda:[dict(study_id=source.study_id,title='Synthetic recorded fixture',attempts=2,source_method='SCRIPTED_SIM',target_optimizer_allowed=False)])
    token='private-test-token';csrf='private-test-csrf'
    with store.connect() as db:db.execute('INSERT INTO sessions VALUES (?,?,?)',(hashlib.sha256(token.encode()).hexdigest(),time.time()+600,csrf))
    app=FastAPI();app.middleware('http')(central.authenticate);study_api.install_router(app,lambda:store)
    with TestClient(app) as client:yield store,source,client,token,csrf


def authenticate(client,token,csrf):
    client.cookies.set('explorer_session',token);return {'X-Explorer-CSRF':csrf}


def test_auth_CSRF_origin_and_allowlisted_ID_only_precede_any_job_or_source_request(interface):
    store,source,client,token,csrf=interface
    assert client.get('/api/research/studies').status_code==401
    assert client.post('/api/research/studies',json={'study_id':source.study_id}).status_code==401
    headers=authenticate(client,token,csrf)
    assert client.post('/api/research/studies',json={'study_id':source.study_id}).status_code==403
    assert client.post('/api/research/studies',json={'study_id':source.study_id},headers=dict(headers,Origin='https://other.example')).status_code==403
    for body in ({'path':'/etc/passwd'},{'study_id':'/etc/passwd'},{'study_id':'../campaign'},
            {'study_id':source.study_id,'estimated_payload_bytes':0},{'study_id':source.study_id,'completeness':'COMPLETE'},
            {'study_id':None},[],True):
        response=client.post('/api/research/studies',json=body,headers=headers)
        assert response.status_code==400,response.text
    assert client.post('/api/research/studies',content=b'x'*5000,headers=headers).status_code==400
    assert client.post('/api/research/studies',content=b'not JSON',headers=headers).status_code==400
    assert client.post('/api/research/studies',content=b'['*1500+b'0'+b']'*1500,headers=headers).status_code==400
    assert store.jobs()==[] and not (store.root/'artifacts/study-export-requests').exists()


def test_API_prepares_immutable_typed_queue_then_only_downloads_real_verified_completed_fixture(interface):
    store,source,client,token,csrf=interface;headers=authenticate(client,token,csrf)
    listed=client.get('/api/research/studies').json()
    assert listed['studies'][0]['study_id']==source.study_id and listed['exports']==[]
    created=client.post('/api/research/studies',json={'study_id':source.study_id},headers=headers)
    assert created.status_code==200,created.text
    job=store.job(created.json()['id'])
    assert job['kind']=='study_export' and job['project_id']=='06' and job['status']=='queued'
    assert type(job['config']['estimated_payload_bytes']) is int and job['config']['estimated_payload_bytes']>0
    assert (store.root/job['config']['request_manifest']).is_file()
    route='/api/research/studies/'+job['id']+'/download'
    assert client.get(route).status_code==409
    # Explicit software-only fixture execution, no Supervisor/physical worker.
    store.update_job(job['id'],status='running');job=store.job(job['id'])
    result=study_job.run_study_export(store,job,job['config']);store.complete_job(job['id'],result)
    response=client.get(route)
    assert response.status_code==200 and response.headers['cache-control']=='no-store'
    assert response.headers['x-content-sha256']==hashlib.sha256(response.content).hexdigest()==result['archive_sha256']
    assert response.headers['content-type']=='application/x-tar'
    exports=client.get('/api/research/studies').json()['exports'];row=exports[0]
    assert row['download_url']==route and row['study_id']==source.study_id
    assert row['result']['verification']=='PARTIAL' and row['result']['independent_restore_verified']
    assert row['result']['outcomes']=={'synthetic-success':'SUCCESS','synthetic-failure':'FAILURE'}
    assert row['result']['required_dependency_gap_count']==4
    assert str(source.campaign_root) not in json.dumps(exports) and 'evidence_tar_path' not in json.dumps(exports)
    for path in ('/api/research/studies?path=/etc/passwd',route+'?path=/etc/passwd','/api/research/studies/nothex/download'):
        assert client.get(path).status_code==400
    assert client.post('/api/research/studies?path=/etc/passwd',json={'study_id':source.study_id},headers=headers).status_code==400
    (store.root/'jobs'/job['id']/'study.tar').write_bytes(response.content+b'changed')
    assert client.get(route).status_code==409


def test_listing_SQL_filters_and_bounds_metadata_before_any_raw_job_fetch(interface,monkeypatch):
    store,source,client,token,csrf=interface;headers=authenticate(client,token,csrf)
    selected=store.create_job('study_export','06',dict(study_id=source.study_id))
    unrelated=store.create_job('world_model','01',{})
    with store.connect() as db:
        db.execute('UPDATE jobs SET result=?,status=?,error=? WHERE id=?',(json.dumps({'oversized':'x'*200000}),'failed','PRIVATE_ERROR_PATH',selected['id']))
        db.execute('UPDATE jobs SET result=? WHERE id=?',(json.dumps({'unrelated':'x'*2000000}),unrelated['id']))
    def forbidden(*args,**kwargs):raise AssertionError('Study listing must not fetch arbitrary Store job bodies')
    monkeypatch.setattr(store,'job',forbidden);monkeypatch.setattr(store,'jobs',forbidden)
    original=study_api.sqlite3.connect;queries=[]
    def traced(*args,**kwargs):
        db=original(*args,**kwargs);db.set_trace_callback(queries.append);return db
    monkeypatch.setattr(study_api.sqlite3,'connect',traced)
    response=client.get('/api/research/studies');assert response.status_code==200,response.text
    rows=response.json()['exports']
    assert len(rows)==1 and rows[0]['id']==selected['id']
    assert rows[0]['details_withheld_reason']=='typed_metadata_byte_budget_exceeded'
    assert 'PRIVATE_ERROR_PATH' not in response.text and unrelated['id'] not in response.text
    assert any('LENGTH(CAST(config AS BLOB))' in query and "kind='study_export'" in query for query in queries)
    assert not any('SELECT config,result FROM jobs' in query for query in queries)


def test_no_false_download_for_completed_nonstudy_or_inconsistent_typed_metadata(interface):
    store,source,client,token,csrf=interface;headers=authenticate(client,token,csrf)
    unrelated=store.create_job('world_model','01',{});store.complete_job(unrelated['id'],dict(archive_sha256='a'*64))
    assert client.get('/api/research/studies/'+unrelated['id']+'/download').status_code==409
    forged=store.create_job('study_export','06',{'study_id':source.study_id})
    store.complete_job(forged['id'],dict(schema='explorer_study_export_job_result_v1',study_id=source.study_id,
        verification='COMPLETE',independent_restore_verified=True,archive_sha256='a'*64,archive_bytes=10))
    listed=client.get('/api/research/studies').json()['exports']
    assert len(listed)==1 and 'download_url' not in listed[0]
    assert client.get('/api/research/studies/'+forged['id']+'/download').status_code==409


def test_typed_result_total_SQL_byte_budget_holds_before_remaining_body_fetches(interface,monkeypatch):
    store,source,client,token,csrf=interface;authenticate(client,token,csrf)
    for _ in range(3):store.create_job('study_export','06',{'study_id':source.study_id})
    monkeypatch.setattr(study_api,'MAX_TOTAL_METADATA_BYTES',40)
    original=study_api.sqlite3.connect;queries=[]
    def traced(*args,**kwargs):
        db=original(*args,**kwargs);db.set_trace_callback(queries.append);return db
    monkeypatch.setattr(study_api.sqlite3,'connect',traced)
    response=client.get('/api/research/studies');assert response.status_code==200
    rows=response.json()['exports']
    assert sum(row.get('details_withheld_reason')=='typed_metadata_byte_budget_exceeded' for row in rows)==2
    assert sum('SELECT config,result FROM jobs' in query for query in queries)==1
