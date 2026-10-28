"""Real authenticated data-only routes; structural model fixture, no optimizer/hardware."""
import base64
from copy import deepcopy
import hashlib
import importlib
import json
from pathlib import Path
import sys
import uuid

from fastapi.testclient import TestClient
import pytest

from explorer import transfer_geometry as geometry
from explorer_shared.contracts import canonical,digest
from test_transfer_source import fixture
from test_transfer_mapping04 import CALIBRATION


def configured(tmp_path,monkeypatch,*,embedded=True,source_kind="SIMULATION"):
    raw=json.dumps(CALIBRATION,indent=2).encode();checksum=hashlib.sha256(raw).hexdigest()
    envelope=dict(schema='explorer_so101_original_calibration_artifact_v1',role='follower',sha256=checksum,
        raw_base64=base64.b64encode(raw).decode(),bytes=len(raw))
    follower=dict(calibration_sha256=checksum)
    if embedded:follower['calibration_artifact']=deepcopy(envelope)
    store,job,admission,outer,view=fixture(tmp_path,source_kind=source_kind,calibration_metadata=dict(calibration_metadata=dict(follower=follower)))
    monkeypatch.setattr(geometry.transfer_source,'_admission',admission)
    app=importlib.import_module('explorer.app');monkeypatch.setattr(app,'STORE',store);salt='own-calibration-api-fixture'
    monkeypatch.setattr(app,'CONFIG',dict(app.CONFIG,data_dir=str(store.root),nodes=[],password_salt=salt,password_hash=hashlib.pbkdf2_hmac('sha256',b'fixture',salt.encode(),310000).hex()))
    return app,store,job['id'],envelope


def test_source_model_drives_geometry_and_durable_wizard_without_client_paths_or_metric_claims(tmp_path,monkeypatch):
    app,store,ident,envelope=configured(tmp_path,monkeypatch);before=set(sys.modules)
    client=TestClient(app.app)  # no lifespan/poller/device owner
    assert client.get('/api/transfer/geometry?model_job_id='+ident).status_code==401
    assert client.post('/api/login',json={'password':'fixture'}).status_code==200;csrf={'X-Explorer-CSRF':client.cookies['explorer_csrf']}
    for query in ('','?model_job_id=../bad','?model_job_id='+ident+'&path=/etc/passwd','?model_job_id='+ident+'&model_job_id='+ident):
        assert client.get('/api/transfer/geometry'+query).status_code in (400,409)
    response=client.get('/api/transfer/geometry?model_job_id='+ident);assert response.status_code==200,response.text
    result=response.json();assert result['geometry_preview_ready'] and result['source_role']=='follower'
    assert result['source_model_binding']['source_calibration_sha256']==envelope['sha256']
    assert result['source_model_binding']['source_urdf_sha256']=='3a65d2d35e68a8d2f0c2cc176d19b884506543c93ba72980145b80abe276022c'
    assert result['audit']['all_computable_mapping_A_resolved'] and len(result['audit']['B'])==3 and result['audit']['C']
    assert not result['physical_execution_authorized'] and not result['source_object_skill_inferred'] and not result['source_action_head_copied_to_target']
    body={'model_job_id':ident,'target_version':2,'request_id':str(uuid.uuid4())}
    assert client.post('/api/transfer/calibration',json=body).status_code==403
    for invalid in ({**body,'audit':result['audit']},{**body,'measurements':[]},{**body,'target_version':1}):
        assert client.post('/api/transfer/calibration',json=invalid,headers=csrf).status_code in (400,409)
    response=client.post('/api/transfer/calibration',json=body,headers=csrf);assert response.status_code==200,response.text
    state=response.json();assert state['source_model_binding']==result['source_model_binding'] and state['source_version']==1 and state['target_version']==2
    assert state['domain']=='simulation' and not state['physical_calibration_ready'] and not state['physical_execution_authorized']
    assert client.post('/api/transfer/calibration',json=body,headers=csrf).json()==state
    restored=client.get('/api/transfer/calibration/'+state['wizard_id']);assert restored.status_code==200 and restored.json()['source_model_binding']==result['source_model_binding']
    assert 'torch' not in set(sys.modules)-before
    assert [j['id'] for j in store.jobs()]==[ident]  # no target/train/eval job inserted
    # Later source/weights drift cannot attach old calibration to a new model.
    model=store.job(ident);weight=Path(model['result']['checkpoint_dir'])/'model.safetensors';weight.write_bytes(b'changed software fixture')
    assert client.get('/api/transfer/calibration/'+state['wizard_id']).status_code==409
    from explorer.transfer_calibration import session
    assert session(store,state['wizard_id'])['source_model_binding']==result['source_model_binding']


def test_SHA_only_simulation_source_is_explicitly_blocked_without_guessing_calibration(tmp_path,monkeypatch):
    app,store,ident,envelope=configured(tmp_path,monkeypatch,embedded=False);client=TestClient(app.app)
    client.post('/api/login',json={'password':'fixture'})
    result=client.get('/api/transfer/geometry?model_job_id='+ident)
    assert result.status_code==409 and result.json()['blockers'][0]['code']=='A_ORIGINAL_SIM_CALIBRATION_ARTIFACT_UNAVAILABLE'


def test_physical_domain_reads_existing_calibration_via_authenticated_node_GET_without_any_observed_binding(tmp_path,monkeypatch):
    from http.server import ThreadingHTTPServer
    import threading
    from types import SimpleNamespace
    from explorer_shared.node_agent import make_handler
    app,store,ident,envelope=configured(tmp_path,monkeypatch,source_kind='SO101_RECORDED_DEMONSTRATIONS')
    path=tmp_path/'SOFTWARE-follower-original.json';path.write_bytes(base64.b64decode(envelope['raw_base64']))
    station=SimpleNamespace(lock=threading.RLock(),configuration={'roles':{'follower':{'calibration':str(path),'port':'SOFTWARE_NO_DEVICE'}}},profiles={})
    agent=SimpleNamespace(token='OWN_METADATA_FIXTURE_TOKEN',mode='pi',station=station,station_observed=None,mobile_records=None,arm_retarget=None)
    server=ThreadingHTTPServer(('127.0.0.1',0),make_handler(agent));server.daemon_threads=True
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    monkeypatch.setattr(app,'CONFIG',dict(app.CONFIG,nodes=[dict(id='pi',host='127.0.0.1',port=server.server_port,token=agent.token)]))
    client=TestClient(app.app);client.post('/api/login',json={'password':'fixture'});csrf={'X-Explorer-CSRF':client.cookies['explorer_csrf']}
    try:
        response=client.get('/api/transfer/geometry?model_job_id='+ident);assert response.status_code==200,response.text
        assert response.json()['source_model_binding']['source_domain']=='PHYSICAL' and not response.json()['physical_execution_authorized']
        state=client.post('/api/transfer/calibration',json=dict(model_job_id=ident,target_version=2,request_id=str(uuid.uuid4())),headers=csrf)
        assert state.status_code==200,state.text
        assert not state.json()['physical_calibration_ready'] and not state.json()['calibration_complete']
        assert state.json()['active_step']=='joint_reference_views' and path.read_bytes()==base64.b64decode(envelope['raw_base64'])
        # Existing metadata node has no observation provider, camera, bus, native
        # command or even station.status method. Any such access would fail.
        path.write_bytes(path.read_bytes()+b' ')
        assert client.get('/api/transfer/geometry?model_job_id='+ident).status_code==409
    finally:server.shutdown();server.server_close();thread.join(2)


def test_source_and_target_domains_are_separate_without_promoting_physical_calibration(tmp_path,monkeypatch):
    from contextlib import contextmanager
    from explorer import transfer_geometry_api as api
    app,store,ident,envelope=configured(tmp_path,monkeypatch,source_kind='SO101_RECORDED_DEMONSTRATIONS')
    reads=[]
    class Metadata:
        def calibration_artifact(self,role,expected_sha256):
            reads.append((role,expected_sha256));return envelope
    @contextmanager
    def metadata(_):yield Metadata()
    monkeypatch.setattr(api,'pi_metadata_connection',metadata)
    client=TestClient(app.app);client.post('/api/login',json={'password':'fixture'})
    csrf={'X-Explorer-CSRF':client.cookies['explorer_csrf']}
    body=dict(model_job_id=ident,target_version=2,request_id=str(uuid.uuid4()),target_domain='simulation')
    response=client.post('/api/transfer/calibration',json=body,headers=csrf);assert response.status_code==200,response.text
    wizard=response.json();assert wizard['source_model_binding']['source_domain']=='PHYSICAL' and wizard['domain']=='simulation'
    assert wizard['calibration_complete'] and not wizard['physical_calibration_ready'] and not wizard['physical_execution_authorized']
    assert all(step['status']=='SIM_AUTO_GEOMETRY' for step in wizard['steps'])
    assert client.post('/api/transfer/calibration',json=body,headers=csrf).json()['wizard_id']==wizard['wizard_id']
    assert reads and all(role=='follower' and checksum==envelope['sha256'] for role,checksum in reads)
    assert client.post('/api/transfer/calibration',json=dict(body,target_domain='guessed'),headers=csrf).status_code==400
    assert client.post('/api/transfer/calibration',json=dict(body,target_domain='physical'),headers=csrf).status_code==409


def test_simulation_source_physical_target_preparation_retains_all_physical_gates(tmp_path,monkeypatch):
    app,store,ident,envelope=configured(tmp_path,monkeypatch)
    client=TestClient(app.app);client.post('/api/login',json={'password':'fixture'})
    csrf={'X-Explorer-CSRF':client.cookies['explorer_csrf']}
    response=client.post('/api/transfer/calibration',json=dict(model_job_id=ident,target_version=2,
        request_id=str(uuid.uuid4()),target_domain='physical'),headers=csrf)
    assert response.status_code==200,response.text
    wizard=response.json();assert wizard['source_model_binding']['source_domain']=='SIMULATION' and wizard['domain']=='physical'
    assert not wizard['calibration_complete'] and not wizard['physical_calibration_ready'] and not wizard['physical_execution_authorized']
    assert all(step['status']=='CAPTURE_REQUIRED' for step in wizard['steps'])
