"""Root/source phase integration with authenticated native software/fake buses."""
from copy import deepcopy
from contextlib import contextmanager
from http.server import ThreadingHTTPServer
import io
import json
import threading
from types import SimpleNamespace
from urllib.request import Request,urlopen
from urllib.error import HTTPError

import pytest

from explorer_shared.contracts import canonical,digest
from test_cooperative_native_sessions import configured,choice,execute
from test_cooperative_observed_service import grant,command
from test_native_cooperative05 import message
from test_native_activation import TOKEN
from test_native_activation import authorize as source_authorize
from explorer_shared.legacy_bridge import LegacyBridge
from explorer_shared.node_agent import make_handler


def prepare(tmp_path,*,policy=False):
    service,store,row,spec,pair,client=configured(tmp_path,policy_roles=('giver',) if policy else ())
    if policy:
        def never_predict(*args,**kwargs):raise AssertionError('No model prediction belongs to explicit source scope setup')
        service.native_controls._ready_prediction=lambda row,spec,role:never_predict
    pair.gate.clock=pair.mux.tap.clock;pair.gate.camera.clock=pair.mux.tap.clock
    execute(service,'native_begin',choice(service,row,'giver','native_begin'))
    execute(service,'native_resume',choice(service,row,'giver','native_resume'))
    pair.arm.pub.publish(message(90))  # SOFTWARE original publisher fixture only.
    reference={'boot_id':'boot','servo_deg':[90]*6,'measured':False}
    pair.mux.tap.reference_header(reference,digest(reference),native_guard_verified=True,source_bytes=canonical(reference))
    pair.owner.reference=lambda:{**reference,'servo_deg':deepcopy(pair.mux.tap.last_publisher['pose_deg'])}
    old_resume=pair.teleop.resume
    def resume(source,observing):
        value=old_resume(source,observing);pair.teleop.lease=pair.native_tick[0]/1e9+.45;return value
    pair.teleop.resume=resume
    def invalidate(reason):
        value=pair.gate.revoke(reason);pair.owner.guard.revoke(reason);return value
    pair.mux.on_invalidate=invalidate
    # The real mount wrapper is separately tested. This fixture uses its same
    # camera root to refresh only a fake cached source frame after each action.
    return service,store,row,spec,pair,client


def phase(service,row,operation):
    body=choice(service,row,'giver',operation);body['neutral']=True
    return execute(service,operation,body)


def new_permission(service,row,pair):
    pair.native_tick[0]+=100_000_000
    path=pair.gate.camera.root/'data/camera-frame.json';value=json.loads(path.read_bytes())
    value['received_monotonic_ns']=pair.native_tick[0];value['frame_id']+='-new-USER';path.write_text(json.dumps(value))
    return grant(service,row,pair,'giver')


@contextmanager
def real_node(pair,tmp_path,monkeypatch):
    folder=tmp_path/'private-proxy';(folder/'config').mkdir(parents=True);(folder/'config/access_token').write_text(TOKEN)
    bridge=LegacyBridge(folder);calls=[]
    class Response(io.BytesIO):
        def __init__(self,status,raw):super().__init__(raw);self.status=status
    def source(request,timeout):
        route=request.full_url.removeprefix('http://127.0.0.1:8080');body=json.loads(request.data) if request.data else None
        result=pair.client.request(request.get_method(),route,json=body,headers={'Authorization':request.headers['Authorization']})
        calls.append((request.get_method(),route,body));return Response(result.status_code,result.content)
    monkeypatch.setattr('explorer_shared.legacy_bridge.urlopen',source);node_token='private-SOFTWARE-node-token'
    agent=SimpleNamespace(mode='nano',mobile_records=None,mobile_capture=None,legacy_bridge=bridge,token=node_token,
        legacy_request=lambda path,payload=None,query=None:bridge.request(path,payload,query=query))
    server=ThreadingHTTPServer(('127.0.0.1',0),make_handler(agent));server.daemon_threads=True
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    origin='http://127.0.0.1:'+str(server.server_port)
    def giver(method,path,body):
        pair.calls.append(('giver',method,path,deepcopy(body)))
        request=Request(origin+path,data=None if body is None else json.dumps(body).encode(),method=method,
            headers={'Authorization':'Bearer '+node_token,'Content-Type':'application/json'})
        try:
            with urlopen(request,timeout=3) as response:return json.load(response)
        except HTTPError as error:raise ValueError(error.read().decode()) from error
    old=pair.giver;old_request=pair.helper.requests['giver'];pair.giver=giver;pair.helper.requests['giver']=giver
    try:yield calls
    finally:
        pair.giver=old;pair.helper.requests['giver']=old_request;server.shutdown();server.server_close();thread.join(2)


@pytest.mark.parametrize('policy',[False,True])
def test_park_new_intent_permission_archived_successor_begin_resume_never_mutates_initial_binding_or_autoattaches(tmp_path,policy):
    service,store,row,spec,pair,client=prepare(tmp_path,policy=policy)
    try:
        initial=deepcopy(service.workflow.snapshot(row['session_id'])['observed_test_binding']);pubs=len(pair.owner.original_publisher.messages)
        parked=phase(service,row,'native_phase_park')
        assert parked['native_receipt']['native_command_called'] and pair.owner.guard.parked is not None
        assert not service.transports
        permission=new_permission(service,row,pair);calls=len(pair.calls);lease=pair.teleop.lease
        adopted=phase(service,row,'native_phase_adopt')
        current=service.workflow.snapshot(row['session_id']);entry=current['native_grant_successors']['giver']
        assert current['observed_test_binding']==initial and entry['permission']['authorization_id']==permission['authorization_id']
        assert adopted['native_receipt']['native_command_called'] is False and pair.teleop.lease==lease
        assert len(pair.owner.original_publisher.messages)==pubs and not service.transports
        assert service.grant_lineage.expected('giver',current,spec)==entry['permission']
        if policy:
            begun=phase(service,row,'native_phase_begin');assert begun['native_receipt']['native']['state']=='CLUTCHED'
        resumed=phase(service,row,'native_phase_resume')
        assert resumed['native_receipt']['native_command_called'] and pair.owner.guard.parked is None
        assert pair.teleop.lease==pair.native_tick[0]/1e9+.45 and not service.transports
        assert not service.snapshot(row['session_id'])['execution_authority']['explicit_user_observed_test_current']
    finally:service.close();pair.close();client.close()


@pytest.mark.parametrize('policy',[False,True])
def test_root_to_actual_authenticated_node_bridge_source_fake_executor_roundtrip(tmp_path,monkeypatch,policy):
    service,store,row,spec,pair,client=prepare(tmp_path,policy=policy)
    try:
        with real_node(pair,tmp_path,monkeypatch) as calls:
            phase(service,row,'native_phase_park');new_permission(service,row,pair);phase(service,row,'native_phase_adopt')
            if policy:phase(service,row,'native_phase_begin')
            phase(service,row,'native_phase_resume')
            posts=[path for method,path,_ in calls if method=='POST' and '/cooperative/phase/' in path]
            assert posts==['/api/explorer/cooperative/phase/'+name for name in (('park','adopt','begin','resume') if policy else ('park','adopt','resume'))]
            assert not any(path.endswith('/input') or path.endswith('/lease') for method,path,_ in calls if method=='POST')
            assert not service.transports and pair.owner.guard.parked is None
    finally:service.close();pair.close();client.close()


def test_phase_choices_reject_browser_source_values_wrong_role_and_changed_fsm_before_rpc(tmp_path):
    service,store,row,spec,pair,client=prepare(tmp_path)
    try:
        body={**choice(service,row,'giver','native_phase_park'),'neutral':True};before=len(pair.calls)
        for extra in ({'expected_revision':1},{'park_request_id':'foreign'},{'authorization_id':'foreign'},
                {'model':{}},{'actor_id':'foreign'},{'command9':[0]*9}):
            with pytest.raises(ValueError,match='fixed native USER'):execute(service,'native_phase_park',{**body,**extra})
        with pytest.raises(ValueError,match='giver role'):execute(service,'native_phase_park',{**body,'role':'receiver'})
        assert len(pair.calls)==before
        with service.workflow.lock:
            changed=service.workflow.snapshot(row['session_id']);changed['protocol_state']='ROSMASTER_HOLDING';service.workflow._save(changed)
        assert service.native_controls.snapshot(service.workflow.snapshot(row['session_id']))['roles']=={}
        with pytest.raises(ValueError,match='Fresh unconsumed'):execute(service,'native_phase_park',body)
        assert len(pair.calls)==before and pair.owner.guard.parked is None
    finally:service.close();pair.close();client.close()


def test_source_only_new_authorization_is_not_this_root_intent_permission(tmp_path):
    service,store,row,spec,pair,client=prepare(tmp_path)
    try:
        phase(service,row,'native_phase_park')
        fresh,_=source_authorize(pair.client,actor_id='trusted-owner',input_mode='keyboard')
        assert fresh['execution_permission'];before=len(pair.calls)
        controls=command(service,row,'native_refresh');item=controls['roles']['giver']['operations']['native_phase_adopt']
        assert not item['available'] and 'exact Root intent' in item['reason']
        assert all(method=='GET' for _,method,_,_ in pair.calls[before:])
        assert not service.workflow.snapshot(row['session_id']).get('native_grant_successors')
    finally:service.close();pair.close();client.close()


@pytest.mark.parametrize('blocked',['http_capability','reference_fault','draining','wal_fault'])
def test_unknown_or_faulted_source_never_advertises_adopt_or_posts(tmp_path,monkeypatch,blocked):
    service,store,row,spec,pair,client=prepare(tmp_path)
    try:
        phase(service,row,'native_phase_park');new_permission(service,row,pair)
        original=service.native_controls._source
        def source(role):
            result=original(role)
            if role=='giver':
                boundary=result['source']['release_boundary']
                if blocked=='http_capability':result['source']['capabilities']['explicit_user_giver_phase_http']=False
                elif blocked=='reference_fault':boundary['reference_fault']={'reason':'ORIGINAL_REFERENCE_UNKNOWN'}
                elif blocked=='draining':boundary['original_quiescence']['ready']=False
                else:boundary['persistence']['healthy']=False
            return result
        monkeypatch.setattr(service.native_controls,'_source',source);before=len(pair.calls)
        result=command(service,row,'native_refresh')
        assert not any(item['available'] for name,item in result['roles']['giver']['operations'].items() if name.startswith('native_phase_'))
        assert all(method=='GET' for _,method,_,_ in pair.calls[before:])
    finally:service.close();pair.close();client.close()


def test_multiple_successors_verify_immutable_original_anchor_not_only_rehashed_latest_row(tmp_path):
    service,store,row,spec,pair,client=prepare(tmp_path)
    try:
        for _ in range(2):
            phase(service,row,'native_phase_park');new_permission(service,row,pair);phase(service,row,'native_phase_adopt');phase(service,row,'native_phase_resume')
        current=service.workflow.snapshot(row['session_id']);entry=current['native_grant_successors']['giver']
        assert service.grant_lineage.expected('giver',current,spec)==entry['permission']
        pointer=entry['receipt'];path,_=service.artifact('native-control',pointer['receipt_id']);original=path.read_bytes()
        document=json.loads(original);assert document['intent_evidence']['previous_lineage']['entry_sha256']==entry['previous_entry_sha256']
        changed=deepcopy(current);bad=changed['native_grant_successors']['giver'];bad['previous_entry_sha256']='f'*64
        bad['entry_sha256']=digest({key:value for key,value in bad.items() if key!='entry_sha256'})
        with pytest.raises(ValueError,match='predecessor'):service.grant_lineage.expected('giver',changed,spec)
        changed=deepcopy(current);bad=changed['native_grant_successors']['giver'];bad['permission']['owner_generation']+=10
        bad['entry_sha256']=digest({key:value for key,value in bad.items() if key!='entry_sha256'})
        with pytest.raises(ValueError,match='original source successor'):service.grant_lineage.expected('giver',changed,spec)
        assert path.read_bytes()==original and not service.transports
    finally:service.close();pair.close();client.close()


@pytest.mark.parametrize('tamper',['constraint','feedback','origin_clock','actor'])
def test_decoded_source_tamper_never_promotes_a_successor_or_mutates_initial_grant(tmp_path,tamper):
    service,store,row,spec,pair,client=prepare(tmp_path,policy=True)
    try:
        phase(service,row,'native_phase_park');new_permission(service,row,pair)
        initial=deepcopy(row['observed_test_binding']);original=service._request('giver')
        def giver(method,path,body):
            result=original(method,path,body)
            if method=='POST' and path.endswith('/phase/adopt'):
                if tamper=='constraint':result['interlock']['parked']['constraint']=None
                elif tamper=='feedback':result['successor']['origin']['measured_feedback']=[90]*6
                elif tamper=='origin_clock':result['successor']['origin']['actual_issued']['stamp']['boot_id']='foreign'
                else:result['successor']['new_owner']['observed_source_authorization']['actor_id']='foreign'
            return result
        old=service._request;service._request=lambda role:giver if role=='giver' else old(role)
        with pytest.raises(ValueError):phase(service,row,'native_phase_adopt')
        current=service.workflow.snapshot(row['session_id'])
        assert current['observed_test_binding']==initial and not current.get('native_grant_successors') and not service.transports
        originals=list((service.root/'native-control').glob('*/receipt.json'))
        assert any(json.loads(path.read_bytes())['operation']=='native_phase_adopt' for path in originals)
    finally:service.close();pair.close();client.close()


@pytest.mark.parametrize('policy',[False,True])
def test_stop_before_returned_resume_is_remembered_archives_orphan_and_cleans_exact_owner(tmp_path,policy):
    service,store,row,spec,pair,client=prepare(tmp_path,policy=policy)
    entered,release=threading.Event(),threading.Event();errors=[]
    try:
        phase(service,row,'native_phase_park');new_permission(service,row,pair);phase(service,row,'native_phase_adopt')
        if policy:phase(service,row,'native_phase_begin')
        body={**choice(service,row,'giver','native_phase_resume'),'neutral':True};old=service._request
        def giver(method,path,payload):
            value=pair.giver(method,path,payload)
            if method=='POST' and path.endswith('/phase/resume'):entered.set();assert release.wait(4)
            return value
        service._request=lambda role:giver if role=='giver' else old(role)
        def resume():
            try:execute(service,'native_phase_resume',body)
            except Exception as error:errors.append(error)
        thread=threading.Thread(target=resume);thread.start();assert entered.wait(2)
        command(service,row,'stop');release.set();thread.join(3)
        assert not thread.is_alive() and errors and pair.teleop.stop_latched and not pair.mux.active
        assert service.workflow.snapshot(row['session_id'])['status']=='STOPPED' and not service.transports
        documents=[json.loads(path.read_bytes()) for path in (service.root/'native-control').glob('*/receipt.json')]
        orphan=[doc for doc in documents if doc['operation']=='native_phase_resume'];assert len(orphan)==1
        assert orphan[0]['authority_publication_allowed'] is False and orphan[0]['source_response']['native_command_called'] is True
        assert service.artifact('native-control',orphan[0]['receipt_id'])[0].is_file()
    finally:
        release.set()
        if 'thread' in locals():thread.join(3)
        service.close();pair.close();client.close()


def test_phase_choice_pins_pending_checkpoint_generation_before_source_rpc(tmp_path,monkeypatch):
    service,store,row,spec,pair,client=prepare(tmp_path)
    try:
        # Real checkpoint/reservation semantics have their own coordinator
        # tests. Here exercise the opaque-choice boundary with two accepted
        # checkpoint generations in the same coordinator epoch/state.
        monkeypatch.setattr(service.workflow,'native_phase_change_window',lambda row:True)
        with service.workflow.lock:
            current=service.workflow.snapshot(row['session_id']);current['pending_operation']={'operation_id':'human-wait','stage':'before-current-frame'};service.workflow._save(current)
        body={**choice(service,row,'giver','native_phase_park'),'neutral':True}
        with service.workflow.lock:
            current=service.workflow.snapshot(row['session_id']);current['pending_operation']['stage']='another-checkpoint';service.workflow._save(current)
        before=len(pair.calls)
        with pytest.raises(ValueError,match='Fresh unconsumed'):execute(service,'native_phase_park',body)
        assert len(pair.calls)==before and pair.owner.guard.parked is None
    finally:service.close();pair.close();client.close()


def test_phase_advance_between_choice_and_reservation_is_rejected_before_rpc(tmp_path,monkeypatch):
    service,store,row,spec,pair,client=prepare(tmp_path)
    try:
        body={**choice(service,row,'giver','native_phase_park'),'neutral':True};original=service.workflow.begin_native_phase_change
        def reserve(*args):
            with service.workflow.lock:
                current=service.workflow.snapshot(row['session_id']);current['protocol_state']='ROSMASTER_HOLDING';service.workflow._save(current)
            return original(*args)
        monkeypatch.setattr(service.workflow,'begin_native_phase_change',reserve);before=len(pair.calls)
        with pytest.raises(ValueError,match='before native phase reservation'):execute(service,'native_phase_park',body)
        assert len(pair.calls)==before and not service.workflow.native_phase_changes and pair.owner.guard.parked is None
    finally:service.close();pair.close();client.close()


def test_expired_original_user_scope_can_only_park_its_exact_old_owner(tmp_path):
    service,store,row,spec,pair,client=prepare(tmp_path)
    try:
        original=pair.owner.guard.binding['authorization_id'];pair.gate.revoke('EXPLICIT_FAKE_FOCUS_LOSS')
        assert pair.gate.status()['authorization_id'] is None
        result=phase(service,row,'native_phase_park')
        assert result['native_receipt']['park']['previous_owner']['authorization_id']==original
        assert pair.teleop.stop_latched and pair.owner.guard.parked['stage']=='STOPPED_BARRIER'
        assert not service.transports
    finally:service.close();pair.close();client.close()


def test_missing_current_policy_head_blocks_begin_without_loading_or_predicting(tmp_path,monkeypatch):
    service,store,row,spec,pair,client=prepare(tmp_path,policy=True)
    try:
        phase(service,row,'native_phase_park');new_permission(service,row,pair);phase(service,row,'native_phase_adopt')
        def not_ready(*args):raise ValueError('EXACT_POLICY_WORKER_NOT_READY')
        monkeypatch.setattr(service.native_controls,'_ready_prediction',not_ready);before=len(pair.calls)
        controls=command(service,row,'native_refresh');item=controls['roles']['giver']['operations']['native_phase_begin']
        assert not item['available'] and item['reason']=='EXACT_POLICY_WORKER_NOT_READY' and not pair.mux.active
        assert all(method=='GET' for _,method,_,_ in pair.calls[before:])
    finally:service.close();pair.close();client.close()


def test_malformed_resume_does_not_erase_prior_real_begin_scope_for_exact_stop(tmp_path):
    service,store,row,spec,pair,client=prepare(tmp_path,policy=True)
    try:
        phase(service,row,'native_phase_park');new_permission(service,row,pair);phase(service,row,'native_phase_adopt');begun=phase(service,row,'native_phase_begin')
        body={**choice(service,row,'giver','native_phase_resume'),'neutral':True};old=service._request
        def giver(method,path,payload):
            result=pair.giver(method,path,payload)
            if method=='POST' and path.endswith('/phase/resume'):result['native']={'unknown_return':True}
            return result
        service._request=lambda role:giver if role=='giver' else old(role)
        with pytest.raises(ValueError,match='Exact returned native giver'):execute(service,'native_phase_resume',body)
        owned=service.native_controls.owned_scope(row,'giver')
        assert owned['operation']=='native_phase_begin' and pair.mux.active and pair.mux.state=='ACTIVE'
        stopped=command(service,row,'stop')['native_owned_session_cleanup']['roles']['giver']
        assert stopped['source_cleanup_receipt'] and pair.teleop.stop_latched and not pair.mux.active
        requests=[payload for _,method,path,payload in pair.calls if method=='POST' and path.endswith('/cooperative/stop')]
        assert requests[-1]['control_epoch']==begun['native_receipt']['native']['control_epoch']
        originals=[json.loads(path.read_bytes()) for path in (service.root/'native-control').glob('*/receipt.json')]
        orphan=next(doc for doc in originals if doc['operation']=='native_phase_resume')
        assert orphan['authority_publication_allowed'] is False and orphan['source_response']['native']=={'unknown_return':True}
    finally:service.close();pair.close();client.close()


def test_stop_during_source_final_wal_cannot_restore_running_phase(tmp_path,monkeypatch):
    from explorer_shared import giver_hold_persistence05 as wal
    service,store,row,spec,pair,client=prepare(tmp_path,policy=True)
    entered,release,stopped=threading.Event(),threading.Event(),threading.Event();errors=[];stop_errors=[]
    try:
        phase(service,row,'native_phase_park');new_permission(service,row,pair);phase(service,row,'native_phase_adopt');phase(service,row,'native_phase_begin')
        body={**choice(service,row,'giver','native_phase_resume'),'neutral':True};write=wal.write
        def final(path,value,**kwargs):
            if path==pair.owner.guard.journal.path and value.get('pending') is None and (value.get('state') or {}).get('parked') is None:
                entered.set();assert release.wait(5)
            return write(path,value,**kwargs)
        monkeypatch.setattr(wal,'write',final)
        def resume():
            try:execute(service,'native_phase_resume',body)
            except Exception as error:errors.append(error)
        def stop():
            try:command(service,row,'stop')
            except Exception as error:stop_errors.append(error)
            finally:stopped.set()
        thread=threading.Thread(target=resume);thread.start();assert entered.wait(2)
        stopper=threading.Thread(target=stop);stopper.start();assert stopped.wait(2)
        assert not stop_errors and pair.teleop.stop_latched and not pair.mux.active
        release.set();thread.join(3);stopper.join(3)
        assert not thread.is_alive() and errors and service.workflow.snapshot(row['session_id'])['status']=='STOPPED'
        assert not service.transports and not pair.gate.status()['execution_permission']
    finally:
        release.set()
        if 'thread' in locals():thread.join(3)
        if 'stopper' in locals():stopper.join(3)
        service.close();pair.close();client.close()


def test_stop_during_artifact_fsync_sees_returned_phase_owner_without_attach(tmp_path,monkeypatch):
    import explorer.cooperative_native_receipts as module
    service,store,row,spec,pair,client=prepare(tmp_path,policy=True)
    entered,release,stopped=threading.Event(),threading.Event(),threading.Event();errors=[];stop_errors=[]
    try:
        phase(service,row,'native_phase_park');new_permission(service,row,pair);phase(service,row,'native_phase_adopt');phase(service,row,'native_phase_begin')
        body={**choice(service,row,'giver','native_phase_resume'),'neutral':True};write=module.write_original
        def blocked(path,raw):
            entered.set();assert release.wait(5);return write(path,raw)
        monkeypatch.setattr(module,'write_original',blocked)
        def resume():
            try:execute(service,'native_phase_resume',body)
            except Exception as error:errors.append(error)
        def stop():
            try:command(service,row,'stop')
            except Exception as error:stop_errors.append(error)
            finally:stopped.set()
        thread=threading.Thread(target=resume);thread.start();assert entered.wait(2)
        stopper=threading.Thread(target=stop);stopper.start();assert stopped.wait(2)
        assert not stop_errors and pair.teleop.stop_latched and not pair.mux.active and not service.transports
        assert service.workflow.snapshot(row['session_id'])['status']=='STOPPED'
        release.set();thread.join(3);stopper.join(3);assert errors and not thread.is_alive()
        documents=[json.loads(path.read_bytes()) for path in (service.root/'native-control').glob('*/receipt.json')]
        assert len([doc for doc in documents if doc['operation']=='native_phase_resume'])==1
        assert all(item['operation']!='native_phase_resume' for item in service.workflow.snapshot(row['session_id'])['native_user_control_receipts'])
    finally:
        release.set()
        if 'thread' in locals():thread.join(3)
        if 'stopper' in locals():stopper.join(3)
        service.close();pair.close();client.close()


def test_late_phase_reply_cleanup_does_not_stop_a_foreign_new_authorization(tmp_path):
    service,store,row,spec,pair,client=prepare(tmp_path)
    entered,release=threading.Event(),threading.Event();errors=[]
    try:
        phase(service,row,'native_phase_park');new_permission(service,row,pair);phase(service,row,'native_phase_adopt')
        body={**choice(service,row,'giver','native_phase_resume'),'neutral':True};old=service._request
        def giver(method,path,payload):
            value=pair.giver(method,path,payload)
            if method=='POST' and path.endswith('/phase/resume'):entered.set();assert release.wait(4)
            return value
        service._request=lambda role:giver if role=='giver' else old(role)
        def resume():
            try:execute(service,'native_phase_resume',body)
            except Exception as error:errors.append(error)
        thread=threading.Thread(target=resume);thread.start();assert entered.wait(2)
        pair.owner.stop({**pair.owner.guard.binding,'expected_native_generation':pair.teleop.generation})
        foreign,_=source_authorize(pair.client,actor_id='foreign-owner',input_mode='keyboard')
        before=len([1 for _,method,path,_ in pair.calls if method=='POST' and path.endswith('/cooperative/stop')])
        command(service,row,'stop');release.set();thread.join(3)
        assert errors and not thread.is_alive() and pair.gate.status()['authorization_id']==foreign['authorization_id']
        assert len([1 for _,method,path,_ in pair.calls if method=='POST' and path.endswith('/cooperative/stop')])==before
        assert not service.transports
    finally:
        release.set()
        if 'thread' in locals():thread.join(3)
        service.close();pair.close();client.close()
