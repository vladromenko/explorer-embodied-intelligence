"""One original writer, exact COMMON05 metadata, fake cached inputs only."""
from copy import deepcopy
from http.server import ThreadingHTTPServer
from types import SimpleNamespace
import threading
import uuid

import httpx
import pytest

from explorer_shared.cooperative_native05 import NativeMetadataRecorder
from explorer_shared.node_agent import make_handler
from test_so101_capture_service import service


def cooperative(service,mode='leader'):
    capture,source,clock,request,permission=service
    request=deepcopy(request);selected=deepcopy(request['permission']['binding'])
    selected.pop('source_session_id');selected.pop('demonstration_id')
    selected.update(purpose='cooperative05',input_mode=mode,
        cooperative_session_id=uuid.UUID(request['record_start_body']['session_id']).hex,
        coordinator_epoch='fixture-coordinator-epoch',object_id='fixture-object05')
    if mode=='policy':selected.update(model_job_id='a'*32,checkpoint_content_sha256='b'*64)
    permission.selected=selected;request['permission']['binding']=selected
    context=request['record_start_body']['context']
    context.update(cooperative_parent={'session_id':selected['cooperative_session_id'],'role':'receiver'},
        cooperative_parent_session_id=selected['cooperative_session_id'],
        cooperative_coordinator_epoch=selected['coordinator_epoch'],cooperative_object_id=selected['object_id'],input_mode=mode)
    source.station.mode='policy' if mode=='policy' else 'teleoperation'
    return capture,source,clock,request,permission


@pytest.mark.parametrize('mode',['leader','policy'])
def test_same_original_writer_supports_exact_cooperative_common_owner_without_control(service,mode):
    capture,source,clock,request,permission=cooperative(service,mode)
    before=source.station.mode;start=clock();identity=capture.start(request,'fixture-owner')['recording_id']
    source.sample(start+110_000_000);clock.value=start+150_000_000
    sealed=capture.command('record/finish',{'recording_id':identity,'outcome':'unknown'},'fixture-owner')
    assert sealed['source_context']['context']['cooperative05_capture_permission']==request['permission']
    assert sealed['source_context']['context']['cooperative_parent']['role']=='receiver'
    assert source.station.mode==before and source.station.record is None and permission.calls==2
    assert capture.capabilities()['same_original_station_writer']


@pytest.mark.parametrize('fault',['actor','native_epoch','skill','session','parent','role','coordinator_epoch','object','input','mode','mixed_permission'])
def test_foreign_or_changed_cooperative_capture_rejects_before_original_writer(service,fault):
    capture,source,clock,request,permission=cooperative(service);actor='fixture-owner';body=request['record_start_body'];context=body['context']
    if fault=='actor':actor='foreign'
    elif fault=='native_epoch':request['permission']['control_epoch']='foreign'
    elif fault=='skill':body['skill_version']+=1
    elif fault=='session':body['session_id']=str(uuid.uuid4())
    elif fault=='parent':context['cooperative_parent']['session_id']=uuid.uuid4().hex
    elif fault=='role':context['cooperative_parent']['role']='giver'
    elif fault=='coordinator_epoch':context['cooperative_coordinator_epoch']='foreign'
    elif fault=='object':context['cooperative_object_id']='foreign'
    elif fault=='input':context['input_mode']='policy'
    elif fault=='mode':source.station.mode='policy'
    else:context['source04_demo_permission']=deepcopy(request['permission'])
    with pytest.raises(ValueError):capture.start(request,actor)
    assert source.station.record is None and capture.status()['capture'] is None


def test_revoked_common_permission_between_preflight_and_writer_is_known_denial(service):
    capture,source,clock,request,permission=cooperative(service);permission.deny_at=2
    with pytest.raises(ValueError):capture.start(request,'fixture-owner')
    assert source.station.record is None and not capture.status()['recovery_required']
    permission.deny_at=None;assert capture.start(request,'fixture-owner')['recording_id']


def test_authenticated_common_consumer_uses_original_capture_stage_finish_and_raw_path_remains_closed(service):
    capture,source,clock,request,permission=cooperative(service);token='fake-common05-data-token';calls=[]
    agent=SimpleNamespace(token=token,mode='pi',pi_runtime_profile='so101_lab',station_capture=capture,
        station=source.station,legacy_bridge=None,mobile_records=None)
    server=ThreadingHTTPServer(('127.0.0.1',0),make_handler(agent));server.daemon_threads=True
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
        with httpx.Client(base_url='http://127.0.0.1:'+str(server.server_port),headers={'Authorization':'Bearer '+token},trust_env=False) as client:
            def send(method,path,body):
                calls.append((method,path,deepcopy(body)));response=client.request(method,path,json=body);response.raise_for_status();return response.json()
            prepared={'body':request['record_start_body'],'capture_permission':request['permission'],'capture_actor_id':'fixture-owner'}
            producer=NativeMetadataRecorder('receiver',send,lambda _:deepcopy(prepared),receiver_profile='common')
            reference=producer.record_start({});start=clock();source.sample(start+110_000_000)
            producer.record_stage(reference,'reach',{'cooperative_session_id':request['permission']['binding']['cooperative_session_id'],'phase_id':'PREPARE'})
            clock.value=start+150_000_000;producer.record_finish(reference,{'status':'unknown'})
            assert source.station.mode=='teleoperation' and source.station.record is None
            assert [path for method,path,_ in calls if method=='POST']==['/so101-capture/record/start','/so101-capture/record/stage','/so101-capture/record/finish']
            denied=client.post('/station/record/start',json={});assert denied.status_code==409
    finally:server.shutdown();server.server_close();thread.join(2)


def test_old_source_cannot_fall_back_to_raw_receiver_api():
    calls=[]
    def send(method,path,body):calls.append((method,path));return {}
    producer=NativeMetadataRecorder('receiver',send,lambda _: {},receiver_profile='common')
    with pytest.raises(ValueError,match='no raw fallback'):producer.record_start({})
    assert calls==[('GET','/so101-capture/capabilities')]


@pytest.mark.parametrize('fault',['actor','binding','permission','epoch_during_get','completed_during_get'])
def test_root_permission_preparation_fences_foreign_grants_and_late_session_changes(tmp_path,monkeypatch,fault):
    from test_cooperative_service import fixture,session
    root,store,calls,active,contexts,client=fixture(tmp_path)
    try:
        row,spec=session(root)
        row.update(execution_domain='user_observed_physical_test',observed_test_binding={'actor_id':'trusted-owner'})
        root.workflow._save(row);original_request=root._request
        def factory(role):
            original=original_request(role)
            def request(method,path,body):
                result=original(method,path,body)
                if path=='/station/observed-test/status':
                    if fault=='actor':result['grant']['actor_id']='foreign'
                    elif fault=='binding':result['grant']['binding']['object_id']='foreign'
                    elif fault=='permission':result['execution_permission']=False
                    else:
                        changed=root.workflow.snapshot(row['session_id'])
                        if fault=='epoch_during_get':changed['epoch']=uuid.uuid4().hex
                        else:changed['status']='COMPLETED'
                        root.workflow._save(changed)
                return result
            return request
        monkeypatch.setattr(root,'_request',factory)
        with pytest.raises(ValueError):root._prepare_context('receiver',deepcopy(row['proposed_native_source_contexts']['receiver']))
        assert calls and all(method=='GET' for method,_,_,_ in calls) and not any(active.values())
    finally:root.close();client.close()
