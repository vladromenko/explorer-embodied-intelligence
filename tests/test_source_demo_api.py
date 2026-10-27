"""Central auth -> real localhost Node COMMON permission/file writer; fake buses."""
import hashlib
import asyncio
from http.server import ThreadingHTTPServer
import importlib
import io
import json
from types import SimpleNamespace
import threading
import uuid

from fastapi.testclient import TestClient
from PIL import Image
import pytest
import httpx

from explorer.store import Store
from explorer import lab_skill_workflow as lab
from explorer import source_demo_workflow as workflow
from explorer_shared.observed_display_receipts import ObservedDisplayReceipts
from explorer_shared.node_agent import make_handler
from explorer_shared.so101_station import SO101Station
from explorer_so101.capture_service import StationCaptureService
from test_source_demo_permission import demo_rig
from test_lab_skill_workflow import proposal


def prepared(store):
    chosen=proposal(store);spec=chosen['spec'];variant=spec['demo_plan']['variants'][0]
    session=lab.create_session(store,spec['skill_id'],1,{'scene_id':'software-table','split':variant['split']})
    return {'skill_id':spec['skill_id'],'skill_version':1,'request_id':uuid.uuid4().hex,
        'context':{'session_id':session['session_id'],'variant_id':variant['variant_id'],'object_instance_id':'software-object'}}


def test_demo_preparation_reuses_confirmed_workflow_and_has_no_model_or_runtime_authority(tmp_path):
    store=Store(tmp_path/'state');body=prepared(store);row=workflow.prepare(store,body,'actor')
    again=workflow.prepare(store,body,'actor');assert row['binding']==again['binding']
    assert row['binding']['demonstration_id']!=body['request_id']
    assert row['binding']['source_session_id']==uuid.UUID(body['context']['session_id']).hex
    assert not row['control_started'] and 'model_job_id' not in row['binding'] and store.jobs()==[]
    with pytest.raises(ValueError):workflow.prepare(store,body,'foreign')
    with pytest.raises(ValueError):workflow.attempt(store,row['demonstration_id'],'foreign')
    with pytest.raises(ValueError):workflow.prepare(store,dict(body,grant_id='injected'),'actor')


@pytest.mark.parametrize('kind',['challenge','observe'])
def test_shared_display_receipts_do_not_cross_scope_actor_or_survive_consumption_or_expiry(kind):
    tick=[10.];receipts=ObservedDisplayReceipts(clock=lambda:tick[0],capacity=2)
    receipts.record(kind,'evaluation','frame','actor','a'*64)
    with pytest.raises(ValueError):receipts.consume(kind,'demonstration','frame','actor','a'*64)
    with pytest.raises(ValueError):receipts.consume(kind,'evaluation','frame','foreign','a'*64)
    receipts.consume(kind,'evaluation','frame','actor','a'*64)
    with pytest.raises(ValueError):receipts.consume(kind,'evaluation','frame','actor','a'*64)
    receipts.record(kind,'demonstration','frame','actor','a'*64);tick[0]+=2
    with pytest.raises(ValueError):receipts.consume(kind,'demonstration','frame','actor','a'*64)


def test_authenticated_demo_control_record_pause_review_and_Range_use_one_actual_software_Station(tmp_path,monkeypatch):
    store=Store(tmp_path/'central');body=prepared(store)
    source_root=tmp_path/'source';source_root.mkdir()
    station,permission,_,_,bus,_,tick,_,_=demo_rig(source_root,monkeypatch)
    monkeypatch.setattr('explorer_shared.so101_station.time.monotonic_ns',lambda:tick[0])
    image=Image.new('RGB',(4,3),(80,90,100));buffer=io.BytesIO();image.save(buffer,format='JPEG');jpeg=buffer.getvalue()
    camera=station.camera
    def frame():return SimpleNamespace(jpeg=jpeg,frame_id='software-camera-frame',monotonic_ns=tick[0],width=4,height=3,timestamp_basis='software-host-receive')
    camera.latest=frame
    station._sample=SO101Station._sample.__get__(station,SO101Station)
    writer=threading.Thread(target=station._record_writer,daemon=True);writer.start()
    capture=StationCaptureService(station,permission,source_root/'capture',clock=lambda:tick[0])
    token='localhost-source-demo-software-token'
    agent=SimpleNamespace(token=token,mode='pi',station=station,station_observed=permission,station_capture=capture,
        legacy_bridge=None,mobile_records=None,arm_retarget=SimpleNamespace(status=lambda:{'state':'SOURCE04_ONLY'},stop=lambda:None))
    server=ThreadingHTTPServer(('127.0.0.1',0),make_handler(agent));server.daemon_threads=True
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    module=importlib.import_module('explorer.app');monkeypatch.setattr(module,'STORE',store)
    # One test server owns one async transport/event loop. Other tests must not
    # inherit a keep-alive connection from this localhost exercise.
    monkeypatch.setattr(module,'CLIENT',httpx.AsyncClient(timeout=4,trust_env=False))
    async def idle_poll():await asyncio.Event().wait()
    monkeypatch.setattr(module,'poll_nodes',idle_poll)
    monkeypatch.setattr(module,'CONFIG',dict(module.CONFIG,nodes=[dict(id='pi',host='127.0.0.1',port=server.server_port,token=token)],
        password_salt='fixture',password_hash=hashlib.pbkdf2_hmac('sha256',b'fixture',b'fixture',310000).hex()))
    client=TestClient(module.app);client.__enter__();base='/api/lab/demonstrations'
    try:
        assert client.get(base).status_code==401 and not bus.writes
        assert client.post('/api/login',json={'password':'fixture'}).status_code==200
        csrf={'X-Explorer-CSRF':client.cookies['explorer_csrf']}
        assert client.post(base,json=body).status_code==403
        created=client.post(base,json=body,headers=csrf);assert created.status_code==200,created.text
        row=created.json();route=base+'/'+row['demonstration_id'];assert not bus.writes and not permission.status()['execution_permission']
        challenge=client.post(route+'/challenge',json={},headers=csrf);assert challenge.status_code==200,challenge.text
        challenge=challenge.json();authority=dict(challenge_id=challenge['challenge_id'],displayed_jpeg_sha256=challenge['frame']['jpeg_sha256'],
            expected_control_epoch=challenge['control_epoch'],observing=True,robot_workspace_checked=True,user_observed_test_confirmed=True)
        assert client.post(route+'/authorize',json=authority,headers=csrf).status_code==409
        delivered=client.get(route+'/challenge/'+challenge['challenge_id']+'/frame');assert delivered.status_code==200
        assert hashlib.sha256(delivered.content).hexdigest()==challenge['frame']['jpeg_sha256']
        grant=client.post(route+'/authorize',json=authority,headers=csrf);assert grant.status_code==200,grant.text
        grant=grant.json();assert grant['actor_id'].startswith('authenticated-session:') and not bus.writes
        started=client.post(route+'/start',json={'grant_id':grant['grant_id'],'expected_control_epoch':grant['control_epoch']},headers=csrf)
        assert started.status_code==200,started.text
        current=started.json()['grant'];assert station.mode=='teleoperation' and bus.writes
        capture_body={'grant_id':current['grant_id'],'expected_control_epoch':current['control_epoch']}
        begin=client.post(route+'/capture-start',json=capture_body,headers=csrf);assert begin.status_code==200,begin.text
        ident=begin.json()['recording_id'];writes=len(bus.writes)
        tick[0]+=110_000_000;measured=[0.,1.,2.,3.,4.,50.];issued=[1.,2.,3.,4.,5.,50.]
        station.measurements=dict(leader=measured,follower=measured,clock_domain=station.clock_domain,monotonic_ns=tick[0])
        station.last_command=dict(requested=issued,dispatched=issued,monotonic_ns=tick[0]+1,clock_domain=station.clock_domain,control_epoch=station.control_epoch,sequence=0)
        station._sample(frame(),measured,issued,issued,tick[0],tick[0]+1);station.record_queue.join()
        tick[0]+=10_000_000
        for action in ('pause','resume'):
            reply=client.post(route+'/'+action,json={'recording_id':ident},headers=csrf);assert reply.status_code==200,reply.text
        tick[0]+=10_000_000
        finished=client.post(route+'/finish',json={'recording_id':ident,'outcome':'unknown'},headers=csrf);assert finished.status_code==200,finished.text
        assert finished.json()['state']=='completed' and len(bus.writes)==writes and station.mode=='teleoperation'
        manifest=client.get(route+'/capture/manifest');assert manifest.status_code==200 and manifest.json()==finished.json()
        first=client.get(route+'/capture/download',headers={'Range':'bytes=4096-'});assert first.status_code==206,first.text
        checksum=first.headers['X-Content-SHA256']
        approval=client.post(route+'/review',json={'recording_id':ident,'decision':'approve','expected_archive_sha256':checksum},headers=csrf)
        assert approval.status_code==200 and approval.json()['transfer_ready'],approval.text
        queued=store.jobs();assert len(queued)==1 and queued[0]['kind']=='station_import'
        assert approval.json()['import_job']['id']==queued[0]['id']
        assert queued[0]['config']==dict(episode_id=ident,archive_sha256=checksum,
            source_format='so101_source_capture_v1',source_review_sha256=approval.json()['review_sha256'])
        rejected=client.post(route+'/review',json={'recording_id':ident,'decision':'reject','expected_archive_sha256':checksum},headers=csrf)
        assert rejected.status_code==200 and rejected.json()['import_job'] is None
        with pytest.raises(ValueError):workflow.current_source_review(store,ident,checksum,approval.json()['review_sha256'])
        assert len(store.jobs())==1 and len(bus.writes)==writes
        assert permission.status()['grant']['binding']==row['binding']
        assert client.post(route+'/stop',json={'grant_id':current['grant_id'],'expected_control_epoch':grant['control_epoch']},headers=csrf).status_code==409
    finally:
        client.__exit__(None,None,None);server.shutdown();server.server_close();thread.join(2);capture.close()
        station.record_queue.put(None);writer.join(2);assert not writer.is_alive()
