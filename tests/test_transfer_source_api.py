"""Source-selection API metadata contracts; all qualified bindings here are fixtures."""
import hashlib
import importlib
import json

from fastapi.testclient import TestClient

from explorer.store import Store


def test_current_source_catalog_and_id_only_selection_preserve_first_event_without_target_job(tmp_path,monkeypatch):
    app=importlib.import_module('explorer.app');source=importlib.import_module('explorer.transfer_source');store=Store(tmp_path/'state');calls=[]
    model,evaluation='a'*32,'b'*32
    def qualified(*args):
        assert args==(store,model,evaluation);calls.append(args)
        return dict(schema='explicit_software_binding_fixture_not_actual_object_skill',source_job_id=model,source_evaluation_job_id=evaluation,
            source_action_contract_id='native6_fixture',target_action_contract_id='native9_fixture',physical_execution_authorized=False)
    monkeypatch.setattr(source,'prepare_qualified_source',qualified)
    monkeypatch.setattr(app,'STORE',store)
    monkeypatch.setattr(app,'CONFIG',dict(app.CONFIG,nodes=[],password_salt='fixture',password_hash=hashlib.pbkdf2_hmac('sha256',b'fixture',b'fixture',310000).hex()))
    with TestClient(app.app) as client:
        assert client.get('/api/transfer/sources').status_code==401
        assert client.post('/api/login',json={'password':'fixture'}).status_code==200
        csrf={'X-Explorer-CSRF':client.cookies['explorer_csrf']}
        catalog=client.get('/api/transfer/sources').json()
        assert catalog['current']['source_experiment']=='04' and catalog['current']['target_experiment']=='06'
        assert catalog['current']['choices']==[] and catalog['sources']==[]
        body={'model_job_id':model,'evaluation_job_id':evaluation}
        assert client.post('/api/transfer/source-selection',json=body).status_code==403
        for invalid in ({**body,'source_trace_manifest':'/etc/passwd'},{'model_job_id':'../bad','evaluation_job_id':evaluation},[]):
            assert client.post('/api/transfer/source-selection',json=invalid,headers=csrf).status_code==400
        assert calls==[]
        selected=client.post('/api/transfer/source-selection',json=body,headers=csrf)
        assert selected.status_code==200,selected.text
        receipt=selected.json();assert receipt['target_job_created'] is False and receipt['physical_execution_authorized'] is False
        with store.connect() as db:raw=db.execute('SELECT body FROM events WHERE event_id=?',(receipt['selection_event_id'],)).fetchone()['body']
        again=client.post('/api/transfer/source-selection',json=body,headers=csrf)
        assert again.json()==receipt
        with store.connect() as db:
            assert db.execute('SELECT body FROM events WHERE event_id=?',(receipt['selection_event_id'],)).fetchone()['body']==raw
            assert db.execute('SELECT count(*) FROM events').fetchone()[0]==1
        assert store.jobs()==[]
        envelope=json.loads(raw);assert envelope['project_id']=='06' and envelope['experiment_numbering_version']==2
        def unavailable(*args):raise source.SourceBlocked([{'code':'A_SOURCE_EVALUATION_OWNER_NOT_IMPLEMENTED','category':'A','detail':'Explicit software fixture'}])
        monkeypatch.setattr(source,'prepare_qualified_source',unavailable)
        failure=client.post('/api/transfer/source-selection',json=body,headers=csrf)
        assert failure.status_code==409 and failure.json()['blockers'][0]['category']=='A'
