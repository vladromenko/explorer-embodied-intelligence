"""Actual central auth -> loopback Node HTTP -> COMMON fake Station permission.

Only explicitly labelled structural SOFTWARE models and fake native devices.
No optimizer, real camera, serial port or actual observed-test grant is used.
"""
import hashlib
from http.server import ThreadingHTTPServer
import importlib
import json
from types import SimpleNamespace
import threading
import uuid

from fastapi.testclient import TestClient

from explorer import transfer_source
from explorer_shared.node_agent import make_handler
from test_station_observed_activation import rig
from test_transfer_source import fixture


def test_actual_central_auth_user_display_attach_and_cancel_through_common_native_HTTP(tmp_path,monkeypatch):
    store,model,admission,outer,_=fixture(tmp_path,source_kind='SO101_RECORDED_DEMONSTRATIONS')
    monkeypatch.setattr(transfer_source,'_admission',admission)
    module=importlib.import_module('explorer.app');monkeypatch.setattr(module,'STORE',store)
    provider,station,camera,tick,_=rig();camera.camera_id='SOFTWARE-camera'
    station.calibration=outer['source_calibration_metadata']['calibration_metadata']['follower']['calibration_sha256']
    token='local-SOFTWARE-test-token'
    agent=SimpleNamespace(token=token,mode='pi',station=station,station_observed=provider,mobile_records=None,
        arm_retarget=SimpleNamespace(status=lambda:{'state':'IDLE'},stop=lambda:None))
    server=ThreadingHTTPServer(('127.0.0.1',0),make_handler(agent));server.daemon_threads=True
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    monkeypatch.setattr(module,'CONFIG',dict(module.CONFIG,nodes=[dict(id='pi',host='127.0.0.1',port=server.server_port,token=token)],
        password_salt='fixture',password_hash=hashlib.pbkdf2_hmac('sha256',b'fixture',b'fixture',310000).hex()))
    client=TestClient(module.app);base='/api/lab/source-evaluations'
    try:
        assert client.get(base).status_code==401 and station.calls==[]
        assert client.post('/api/login',json={'password':'fixture'}).status_code==200
        csrf={'X-Explorer-CSRF':client.cookies['explorer_csrf']}
        listed=client.get(base);assert listed.status_code==200,listed.text
        assert listed.json()['models'][0]['id']==model['id'] and listed.json()['models'][0]['eligible']
        body=dict(model_job_id=model['id'],duration_s=.5,object_instance_id='fixture-object',scene_id='fixture-scene',goal_instance_id='fixture-bin')
        assert client.post(base,json=body).status_code==403
        assert client.post(base,json=dict(body,model_job_id='0'*32),headers=csrf).status_code==409
        assert client.post(base,content=b'{"padding":"'+b'x'*4096+b'"}',headers=csrf).status_code==400
        assert client.post(base,json=dict(body,grant_id='f'*32),headers=csrf).status_code==409
        created=client.post(base,json=body,headers=csrf);assert created.status_code==200,created.text
        identity=created.json()['id'];job=store.job(identity);assert job['kind']=='so101_skill_eval'
        assert not job['config'].get('grant_id') and not station.calls and not provider.status()['execution_permission']
        route=base+'/'+identity
        assert client.post(route+'/challenge',json={},headers=csrf).status_code==409
        worker=uuid.uuid4().hex
        store.update_job(identity,status='running',result=dict(phase='awaiting_source_observed_grant',model_ready=True,source_owner_session_id=worker))
        challenge=client.post(route+'/challenge',json={},headers=csrf);assert challenge.status_code==200,challenge.text
        challenge=challenge.json()
        image=client.get(route+'/challenge/'+challenge['challenge_id']+'/frame')
        assert image.status_code==200 and hashlib.sha256(image.content).hexdigest()==challenge['frame']['jpeg_sha256']
        authority=dict(challenge_id=challenge['challenge_id'],displayed_jpeg_sha256=challenge['frame']['jpeg_sha256'],
            expected_control_epoch=challenge['control_epoch'],observing=True,robot_workspace_checked=True,user_observed_test_confirmed=True)
        second_client=TestClient(module.app)
        try:
            second_client.post('/api/login',json={'password':'fixture'})
            stolen=second_client.post(route+'/authorize',json=authority,headers={'X-Explorer-CSRF':second_client.cookies['explorer_csrf']})
            assert stolen.status_code==409 and not provider.status()['execution_permission']
        finally:second_client.close()
        assert client.post(route+'/authorize',json=dict(authority,actor_id='forged'),headers=csrf).status_code==409
        grant=client.post(route+'/authorize',json=authority,headers=csrf);assert grant.status_code==200,grant.text
        grant=grant.json();assert grant['actor_id'].startswith('authenticated-session:') and station.calls==[]
        from explorer.so101_source_evaluation import current_permission
        attachment=current_permission(store,identity,worker)
        assert attachment['grant_id']==grant['grant_id'] and attachment['binding']==job['config']['binding']
        assert client.get(route+'/permission').json()['execution_permission']
        # Another attempt of the SAME model cannot reuse this native grant or
        # its pinned display, even while the original user grant is still live.
        second=client.post(base,json=body,headers=csrf).json()['id']
        assert store.job(second)['config']['binding']['evaluation_id']!=job['config']['binding']['evaluation_id']
        store.update_job(second,status='running',result=dict(phase='awaiting_source_observed_grant',model_ready=True,source_owner_session_id=uuid.uuid4().hex))
        reused=client.post(base+'/'+second+'/observe',json=dict(grant_id=grant['grant_id'],frame_id=challenge['frame']['frame_id'],
            displayed_jpeg_sha256=challenge['frame']['jpeg_sha256'],expected_control_epoch=grant['control_epoch'],observing=True),headers=csrf)
        assert reused.status_code==409 and station.calls==[]
        assert provider.status()['grant']['grant_id']==grant['grant_id']
        # Metadata cancellation revokes only this exact attached grant; no
        # native start/action/STOP is needed while the fake owner is still idle.
        cancelled=client.post('/api/jobs/'+identity+'/cancel',json={},headers=csrf)
        assert cancelled.status_code==200 and not provider.status()['execution_permission'] and station.calls==[]
        assert client.post(route+'/authorize',json=authority,headers=csrf).status_code==409
        assert client.get(base+'?path=/etc/passwd').status_code==400
        assert client.post(route+'/command',json={'operation':'action'},headers=csrf).status_code==400
    finally:
        client.close();server.shutdown();server.server_close();thread.join(2)


def test_retained_source_frames_are_bound_to_the_selected_attempt_and_original_SHA(tmp_path,monkeypatch):
    from explorer import so101_source_evaluation as evaluation
    from explorer.store import Store
    module=importlib.import_module('explorer.app');store=Store(tmp_path/'state');monkeypatch.setattr(module,'STORE',store)
    monkeypatch.setattr(module,'CONFIG',dict(module.CONFIG,nodes=[],password_salt='fixture',password_hash=hashlib.pbkdf2_hmac('sha256',b'fixture',b'fixture',310000).hex()))
    job=store.create_job('so101_skill_eval','04',dict(schema=evaluation.REQUEST_SCHEMA,source_model_job_id='a'*32,
        binding=dict(spec_sha256='b'*64),source_phases=['grasp'],phase_reference={'grasp':'object'},context={}))
    folder=store.root/'jobs'/job['id']/'observations';folder.mkdir(parents=True)
    ident=uuid.uuid4().hex;raw=b'\xff\xd8EXPLICIT-FAKE-JPEG\xff\xd9'
    metadata=folder/(ident+'.json');image=folder/(ident+'.jpg')
    metadata.write_text(json.dumps(dict(observation=dict(frame_sha256=hashlib.sha256(raw).hexdigest(),frame_id='fixture-frame',frame_boot_id='fixture-boot'))));image.write_bytes(raw)
    with TestClient(module.app) as client:
        client.post('/api/login',json={'password':'fixture'})
        path='/api/lab/source-evaluations/'+job['id']+'/frames/'+ident
        response=client.get(path);assert response.status_code==200 and response.content==raw
        image.write_bytes(raw+b'changed')
        assert client.get(path).status_code==409
        assert client.get(path+'?path=/etc/passwd').status_code==400
        image.unlink();image.symlink_to(metadata)
        assert client.get(path).status_code==409
