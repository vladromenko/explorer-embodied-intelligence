"""ASGI + memory-only native capture routes; no physical node or controller IO."""
import hashlib
import importlib
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from explorer.store import Store


@pytest.mark.parametrize('project,node_id,upstream,prefix',[('02','nano','/native-capture/','/api/mobile/capture/'),('03','pi','/arm-retarget/','/api/retarget/')])
def test_same_capture_metadata_identity_phase_review_for_both_input_transports(tmp_path,monkeypatch,project,node_id,upstream,prefix):
    module=importlib.import_module('explorer.app');store=Store(tmp_path/'state');calls=[];ident='c'*32;active=None
    def transport(request):
        nonlocal active
        if request.method=='GET':
            if request.url.path==upstream+'status':
                return httpx.Response(200,json={'active_recording_id':active,'capture':{'recording_id':ident}} if project=='02' else {'recording':{'active':active}})
            return httpx.Response(200,json={})
        body=json.loads(request.content);calls.append((request.url.path,body))
        if request.url.path==upstream+'record/start':active=ident;return httpx.Response(200,json={'recording_id':ident,'state':'RECORDING'})
        if request.url.path==upstream+'record/finish':active=None;return httpx.Response(200,json={'capture':{'recording_id':ident,'state':'SEALING'}})
        if request.url.path==upstream+'record/review':
            receipt={'schema':'explorer_native_capture_review_v1','review_id':'f'*32,'recording_id':ident,'archive_sha256':'e'*64,
                'decision':body['decision'],'actor_id':body['actor_id'],'reviewed_native_ns':1234,'source_boot_id':'fixture','source_clock_domain':'fixture',
                'raw_archive_unchanged':True,'previous_review_sha256':None}
            checksum=hashlib.sha256(json.dumps(receipt,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()
            return httpx.Response(200,json={'source_review':receipt,'review_sha256':checksum,'transfer_ready':body['decision']=='approve'})
        return httpx.Response(200,json={'recording_id':ident})
    config=dict(module.CONFIG,data_dir=str(store.root),password_salt='fixture',password_hash=hashlib.pbkdf2_hmac('sha256',b'fixture',b'fixture',310000).hex(),nodes=[dict(id=node_id,host='memory.test',port=8092,token='fixture')])
    monkeypatch.setattr(module,'STORE',store);monkeypatch.setattr(module,'CONFIG',config);monkeypatch.setattr(module,'CLIENT',httpx.AsyncClient(transport=httpx.MockTransport(transport)))
    with TestClient(module.app) as client:
        assert client.post('/api/login',json={'password':'fixture'}).status_code==200
        headers={'X-Explorer-CSRF':client.cookies['explorer_csrf']}
        def post(path,body):return client.post(path,json=body,headers=headers)
        chosen=post('/api/skill-workflow',{'preset':'sock_to_basket','project_id':project}).json();skill=chosen['spec']['skill_id'];base='/api/skill-workflow/'+skill
        assert post(base+'/confirm',{'version':1,'spec_sha256':chosen['spec_sha256']}).status_code==200
        state=client.get(base).json();variant=state['variants'][0];phase=state['spec']['stages'][0]['id']
        session=post(base+'/session',dict(version=1,split=variant['split'],scene='explicit-scene',object_instance_id='explicit-object',group_id='explicit-group')).json()
        context=dict(skill_id=skill,version=1,spec_sha256=chosen['spec_sha256'],variant_id=variant['variant_id'],session_id=session['session_id'],stationary_chassis_confirmed=True,phase_id=phase)
        started=post(prefix+'record/start',dict(skill_context=context,observing=True))
        assert started.status_code==200,started.text
        capture=started.json()['skill_capture']['capture_id'];native=calls[-1][1]['skill_context']
        assert native['skill_spec']==chosen['spec'] and native['skill_version']==1 and native['phase_id']==phase
        assert native['split']['object_instance_id']=='explicit-object' and native['split']['group_id']=='explicit-group'
        count=len(calls)
        wrong=post(prefix+'record/stage',dict(expected_id='d'*32,skill_capture_id=capture,stage=phase))
        assert wrong.status_code==409 and len(calls)==count
        marked=post(prefix+'record/stage',dict(expected_id=ident,skill_capture_id=capture,stage=phase))
        assert marked.status_code==200 and calls[-1][1]['phase_id']==phase
        finished=post(prefix+'record/finish',dict(expected_id=ident,skill_capture_id=capture,outcome='success',skill_review={'completed_stages':[],'full_task_confirmed':False,'object_and_goal_visible':False}))
        assert finished.status_code==200,finished.text
        assert finished.json()['skill_review']['full_task_confirmed'] is False
        evidence=calls[-1][1]['outcome_evidence']
        assert evidence['operator_outcome']=='success' and evidence['outcomes']['end_to_end']['status']=='unknown'
        assert not evidence['physical_validation_inferred']
        assert all(path.startswith(upstream+'record/') for path,_ in calls)
        assert not store.jobs()  # Finish labels alone never enqueue native02 transfer.
        if project=='02':
            reviewed=post(prefix+'record/review',dict(expected_id=ident,skill_capture_id=capture,decision='approve',expected_archive_sha256='e'*64,actor_id='forged-client-actor'))
            assert reviewed.status_code==200,reviewed.text
            assert calls[-1][1]['actor_id']!='forged-client-actor'
            imported=reviewed.json()['import_job'];assert imported['kind']=='mobile_import'
            assert imported['config']['archive_sha256']=='e'*64
            assert imported['config']['source_review_sha256']==reviewed.json()['review_sha256']
        assert not any(job['kind'].endswith('_train') for job in store.jobs())


def test_tabletop_proxy_preserves_exact_jpeg_identity_and_never_exposes_execute(tmp_path,monkeypatch):
    module=importlib.import_module('explorer.app');store=Store(tmp_path/'state');calls=[];jpeg=b'\xff\xd8memory-frame\xff\xd9'
    def transport(request):
        calls.append(request.url.path)
        if '/frames/' in request.url.path:return httpx.Response(200,content=jpeg,headers={'Content-Type':'image/jpeg','X-Frame-ID':'source:1','X-Frame-SHA256':hashlib.sha256(jpeg).hexdigest(),'X-Clock-Domain':'pi:fixture'})
        return httpx.Response(200,json={'native_execution_enabled':False})
    config=dict(module.CONFIG,data_dir=str(store.root),password_salt='fixture',password_hash=hashlib.pbkdf2_hmac('sha256',b'fixture',b'fixture',310000).hex(),nodes=[dict(id='pi',host='memory.test',port=8092,token='fixture')])
    monkeypatch.setattr(module,'STORE',store);monkeypatch.setattr(module,'CONFIG',config);monkeypatch.setattr(module,'CLIENT',httpx.AsyncClient(transport=httpx.MockTransport(transport)))
    with TestClient(module.app) as client:
        assert client.get('/api/lab/vision/frames/source:1').status_code==401
        client.post('/api/login',json={'password':'fixture'});headers={'X-Explorer-CSRF':client.cookies['explorer_csrf']}
        frame=client.get('/api/lab/vision/frames/source:1');assert frame.content==jpeg
        assert frame.headers['X-Frame-ID']=='source:1' and frame.headers['X-Frame-SHA256']==hashlib.sha256(jpeg).hexdigest()
        count=len(calls);assert client.post('/api/lab/vision/execute',json={},headers=headers).status_code==404
        assert len(calls)==count
        assert client.get('/api/lab/calibration/board.pdf').headers['content-type']=='application/pdf'
