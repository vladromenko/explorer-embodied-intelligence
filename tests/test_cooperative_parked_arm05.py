"""Metadata-only latch from actual parked sources; no real native RPC/devices."""
from copy import deepcopy
from http.server import ThreadingHTTPServer
import json
import threading
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.request import Request,urlopen

import pytest

from explorer_shared.contracts import digest
from explorer_shared import giver_hold_persistence05 as giver_wal
from explorer_shared import station_cooperative_interlock as receiver_wal
from explorer_shared.node_agent import make_handler
from explorer_shared.station_observed_http import StationObservedHttp
from test_cooperative_held_scopes05 import giver,close
from test_cooperative_giver_phases import phase,real_node
from test_native_activation import TOKEN
from test_station_hold_successor import rig as receiver_rig,invoke as adopt


def giver_body(owner):
    scope=owner.held_scope.snapshot();assert scope['ready'],scope
    return {key:scope['binding'][key] for key in ('run_id','object_id','coordinator_epoch','authorization_id')}|{
        'expected_state_id':scope['persistence']['state_id'],'expected_revision':scope['persistence']['revision'],
        'park_request_id':scope['park_request_id'],'phase_id':'RECEIVER_GRASP_CHECK',
        'neutral':True,'observing':True,'user_observed_test_confirmed':True}


def pi_body(wrapper):
    scope=wrapper.held_scope();assert scope['ready'],scope
    return {'grant_id':scope['grant']['grant_id'],'expected_control_epoch':scope['grant']['control_epoch'],
        'interlock_id':scope['interlock_id'],'expected_revision':scope['revision'],'observing':True,'user_observed_test_confirmed':True}


@pytest.mark.parametrize('policy',[False,True])
def test_giver_metadata_arm_keeps_exact_park_origin_and_usedproofs_without_write_resume_or_lease(tmp_path,policy):
    values=giver(tmp_path,policy=policy);service,store,row,spec,pair,client=values
    try:
        if policy:phase(service,row,'native_phase_begin')
        guard=pair.owner.guard;guard.used.add('SOURCE_OLD_CONSUMED_PROOF');guard._durable('software-fixture-retained-proof',lambda:None)
        body=giver_body(pair.owner);parked=deepcopy(guard.parked);used=set(guard.used);lease=pair.teleop.lease
        publications=len(pair.owner.original_publisher.messages);drives=deepcopy(pair.teleop.native_drive)
        result=pair.owner.arm_parked(body,authenticated_actor='trusted-owner')
        assert result['armed'] and result['already_armed'] is False and guard.armed and guard.last_pose==[90]*6
        assert guard.revision==body['expected_revision']+1 and guard.parked==parked and guard.used==used
        assert result['native_commands_sent']==0 and not result['physical_execution_authorized'] and not result['native_command_called']
        assert result['source_scope_sha256']==digest({key:value for key,value in result['source_scope'].items() if key!='scope_sha256'})
        assert pair.teleop.lease==lease and pair.teleop.native_drive==drives and len(pair.owner.original_publisher.messages)==publications
        assert pair.owner.held_scope.snapshot()['ready']
        again=pair.owner.arm_parked(giver_body(pair.owner),authenticated_actor='trusted-owner')
        assert again['already_armed'] and guard.last_pose==[90]*6 and guard.parked==parked and guard.used==used
        assert json.loads(guard.journal.path.read_bytes())['state']['armed']
        with pytest.raises(ValueError):pair.owner.arm_parked(body,authenticated_actor='trusted-owner')
    finally:close(values)


@pytest.mark.parametrize('changed',['actor','revision','run','token','numeric','reference','pending_proof'])
def test_giver_arm_rejects_unknown_cas_source_or_pending_proof_without_mutation(tmp_path,changed):
    values=giver(tmp_path);service,store,row,spec,pair,client=values
    try:
        body=giver_body(pair.owner);actor='trusted-owner'
        if changed=='actor':actor='foreign-user'
        elif changed=='revision':body['expected_revision']+=1
        elif changed=='run':body['run_id']='foreign-run'
        elif changed=='token':body['park_request_id']='foreign-token'
        elif changed=='numeric':body['held_native6']=[90]*6
        elif changed=='reference':pair.owner.guard.reference_fault={'reason':'NEW_ACTUAL_FAULT'}
        else:pair.owner.guard.pending={'challenge_id':'existing-proof-must-not-be-erased'}
        state=pair.owner.guard._snapshot();drives=deepcopy(pair.teleop.native_drive)
        with pytest.raises(ValueError):pair.owner.arm_parked(body,authenticated_actor=actor)
        assert pair.owner.guard._snapshot()==state and not pair.owner.guard.armed and pair.teleop.native_drive==drives
    finally:close(values)


def test_giver_revoke_during_wal_rejects_late_metadata_arm_and_preserves_park(tmp_path,monkeypatch):
    values=giver(tmp_path);service,store,row,spec,pair,client=values
    entered,release=threading.Event(),threading.Event();errors=[];write=giver_wal.write
    try:
        body=giver_body(pair.owner)
        def delayed(path,value,**kwargs):
            if value.get('pending') is not None:entered.set();assert release.wait(3)
            return write(path,value,**kwargs)
        monkeypatch.setattr(giver_wal,'write',delayed)
        def arm():
            try:pair.owner.arm_parked(body,authenticated_actor='trusted-owner')
            except ValueError as error:errors.append(error)
        worker=threading.Thread(target=arm);worker.start();assert entered.wait(2)
        pair.gate.revoke('USER_FOCUS_LOSS');release.set();worker.join(3)
        assert not worker.is_alive() and errors and not pair.owner.guard.armed and pair.owner.guard.parked is not None
    finally:
        release.set()
        if 'worker' in locals():worker.join(3)
        close(values)


def test_metadata_receipt_retains_its_literal_scope_even_when_source_clock_advanced_since_root_get(tmp_path):
    values=giver(tmp_path);service,store,row,spec,pair,client=values
    try:
        before=pair.owner.held_scope.snapshot();body=giver_body(pair.owner);pair.native_tick[0]+=1
        result=pair.owner.arm_parked(body,authenticated_actor='trusted-owner');actual=result['source_scope']
        assert result['source_scope_sha256']==actual['scope_sha256']!=before['scope_sha256']
        assert actual['source_origin']==before['source_origin'] and actual['actual_issued']['arm_receipt']==before['actual_issued']['arm_receipt']
        assert actual['source_stamp']['monotonic_ns']>before['source_stamp']['monotonic_ns'] and not result['native_command_called']
    finally:close(values)


@pytest.mark.parametrize('already',[False,True])
def test_receiver_metadata_arm_preserves_full_origin_frozen_owner_and_usedproofs(tmp_path,monkeypatch,already):
    values=receiver_rig(tmp_path,monkeypatch,armed=already);wrapper,station,provider,binding,bus,tick,StopAfter,old,fresh,body,initial=values
    adopt(wrapper,body);wrapper.helper.used.add('SOURCE_PRIOR_INDEPENDENT_CONFIRMATION');receiver_wal._write(wrapper.path,wrapper._envelope())
    request=pi_body(wrapper);before=wrapper.helper.snapshot_state();writes=deepcopy(bus.writes)
    result=wrapper.dispatch('held/arm',request,authenticated_actor=old['actor_id']);after=wrapper.helper.snapshot_state()
    assert result['armed'] and result['already_armed'] is already and after['armed'] and after['phase']=='ARMED'
    assert after['frozen']==before['frozen'] and after['owner']==before['owner'] and after['binding']==before['binding']
    assert after['used_confirmations']==before['used_confirmations'] and after['direction']==before['direction']
    assert result['native_commands_sent']==0 and not result['native_command_called'] and not result['physical_execution_authorized']
    assert bus.writes==writes and station.mode=='idle' and wrapper.held_scope()['ready']
    assert after['revision']==before['revision']+(0 if already else 1)
    assert not wrapper.check_publication(station.control_epoch,'idle',[0.,0.,0.,0.,0.,50.])['allowed']
    restored=receiver_wal.StationCooperativeInterlock(provider,wrapper.path)
    assert restored.helper.snapshot_state()==after and restored.helper.armed and not restored.check_publication('foreign','policy',[0.]*6)['allowed']
    for operation in ('bind','arm'):
        generic={key:value for key,value in pi_body(wrapper).items() if key not in ('observing','user_observed_test_confirmed')}
        with pytest.raises(ValueError,match='separate explicit native'):wrapper.dispatch(operation,generic,authenticated_actor=old['actor_id'])


@pytest.mark.parametrize('changed',['actor','revision','grant','numeric','frame','pose','pending_proof'])
def test_receiver_held_arm_rejects_unknown_or_changed_context_without_write(tmp_path,monkeypatch,changed):
    values=receiver_rig(tmp_path,monkeypatch,armed=False);wrapper,station,provider,binding,bus,tick,StopAfter,old,fresh,body,initial=values
    adopt(wrapper,body);request=pi_body(wrapper);actor=old['actor_id']
    if changed=='actor':actor='foreign-user'
    elif changed=='revision':request['expected_revision']+=1
    elif changed=='grant':request['grant_id']='foreign-grant'
    elif changed=='numeric':request['native6']=[0.]*6
    elif changed=='frame':provider.grant['data']['displayed_frame']['received_monotonic_ns']=0
    elif changed=='pose':station.measurements['follower'][1]=1.
    else:
        wrapper.helper.pending={'challenge_id':'pending-proof-must-survive'};receiver_wal._write(wrapper.path,wrapper._envelope())
    before=wrapper.helper.snapshot_state();writes=deepcopy(bus.writes)
    with pytest.raises(ValueError):wrapper.dispatch('held/arm',request,authenticated_actor=actor)
    assert wrapper.helper.snapshot_state()==before and not wrapper.helper.armed and bus.writes==writes


def test_receiver_final_wal_failure_latches_globally_without_unarming_or_writes(tmp_path,monkeypatch):
    values=receiver_rig(tmp_path,monkeypatch,armed=False);wrapper,station,provider,binding,bus,tick,StopAfter,old,fresh,body,initial=values
    adopt(wrapper,body);request=pi_body(wrapper);write=receiver_wal._write;count=[];writes=deepcopy(bus.writes)
    def failed(path,value):
        count.append(value)
        if len(count)==2:raise OSError('software-only final arm seal failure')
        return write(path,value)
    monkeypatch.setattr(receiver_wal,'_write',failed)
    with pytest.raises(OSError,match='final arm seal'):wrapper.dispatch('held/arm',request,authenticated_actor=old['actor_id'])
    assert wrapper.helper.armed and wrapper.fault is not None and bus.writes==writes
    assert not wrapper.check_publication('unowned','policy',[0.]*6)['allowed']
    assert receiver_wal.StationCooperativeInterlock(provider,wrapper.path).fault is not None


def test_giver_final_wal_failure_keeps_armed_parked_barrier_without_commands(tmp_path,monkeypatch):
    values=giver(tmp_path);service,store,row,spec,pair,client=values
    try:
        body=giver_body(pair.owner);write=giver_wal.write;count=[];drives=deepcopy(pair.teleop.native_drive)
        def failed(path,value,**kwargs):
            count.append(value)
            if len(count)==2:raise OSError('software-only giver final arm seal failure')
            return write(path,value,**kwargs)
        monkeypatch.setattr(giver_wal,'write',failed)
        with pytest.raises(OSError,match='giver final arm seal'):pair.owner.arm_parked(body,authenticated_actor='trusted-owner')
        assert pair.owner.guard.armed and pair.owner.guard.parked is not None and pair.owner.guard.journal.recovery_required
        assert not pair.owner.held_scope.snapshot()['ready'] and pair.teleop.native_drive==drives
    finally:close(values)


def test_receiver_revoke_while_wal_pending_cannot_arm_old_grant(tmp_path,monkeypatch):
    values=receiver_rig(tmp_path,monkeypatch,armed=False);wrapper,station,provider,binding,bus,tick,StopAfter,old,fresh,body,initial=values
    adopt(wrapper,body);request=pi_body(wrapper);write=receiver_wal._write;entered,release=threading.Event(),threading.Event();errors=[]
    def delayed(path,value):
        if value.get('pending') is not None:entered.set();assert release.wait(3)
        return write(path,value)
    monkeypatch.setattr(receiver_wal,'_write',delayed)
    def arm():
        try:wrapper.dispatch('held/arm',request,authenticated_actor=old['actor_id'])
        except ValueError as error:errors.append(error)
    worker=threading.Thread(target=arm);worker.start();assert entered.wait(2)
    provider.revoke('USER_FOCUS_LOSS');release.set();worker.join(3)
    assert not worker.is_alive() and errors and not wrapper.helper.armed and wrapper.helper.frozen['parked_observation_only']


def test_actual_pi_node_fixed_metadata_arm_requires_source_actor_and_rejects_native_values(tmp_path,monkeypatch):
    values=receiver_rig(tmp_path,monkeypatch,armed=False);wrapper,station,provider,binding,bus,tick,StopAfter,old,fresh,body,initial=values
    adopt(wrapper,body);request_body=pi_body(wrapper);writes=deepcopy(bus.writes);token='PRIVATE-LOCAL-SOFTWARE-ARM-TOKEN'
    agent=SimpleNamespace(token=token,mode='pi',station=station,station_observed=provider,mobile_records=None,legacy_bridge=None,
        lab_vision=None,arm_retarget=SimpleNamespace(status=lambda:{'state':'IDLE'}))
    server=ThreadingHTTPServer(('127.0.0.1',0),make_handler(agent));server.daemon_threads=True
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start();base='http://127.0.0.1:'+str(server.server_port);calls=[]
    def request(method,path,payload):
        calls.append((method,path,deepcopy(payload)))
        with urlopen(Request(base+path,method=method,headers={'Authorization':'Bearer '+token},
                data=None if payload is None else json.dumps(payload).encode()),timeout=2) as response:return json.load(response)
    facade=StationObservedHttp(request,binding)
    try:
        assert not calls and wrapper.status()['parked_observation_arm_http']
        with pytest.raises(HTTPError):urlopen(Request(base+'/station/observed-test/cooperative/held/arm',data=b'{}',method='POST'),timeout=2)
        with pytest.raises(ValueError):facade.cooperative('held/arm',{**request_body,'native6':[0]*6},authenticated_actor=old['actor_id'])
        with pytest.raises(HTTPError):facade.cooperative('held/arm',request_body,authenticated_actor='foreign-operator')
        assert bus.writes==writes and not wrapper.helper.armed
        reply=facade.cooperative('held/arm',request_body,authenticated_actor=old['actor_id'])
        assert reply['armed'] and not reply['native_command_called'] and bus.writes==writes
        assert not reply['physical_execution_authorized'] and station.mode=='idle'
    finally:server.shutdown();server.server_close();thread.join(2)


def test_actual_authenticated_giver_node_bridge_metadata_arm_never_calls_target(tmp_path,monkeypatch):
    values=giver(tmp_path);service,store,row,spec,pair,client=values
    try:
        body=giver_body(pair.owner);publications=len(pair.owner.original_publisher.messages);drives=deepcopy(pair.teleop.native_drive)
        assert pair.owner.status()['capabilities']['parked_observation_arm_http']
        path='/api/explorer/cooperative/phase/arm'
        assert pair.client.post(path,json=body).status_code==401
        assert pair.client.post(path,json={**body,'actor_id':'forged'},headers={'Authorization':'Bearer '+TOKEN}).status_code==409
        with real_node(pair,tmp_path,monkeypatch) as calls:
            result=service._request('giver')('POST','/legacy/explorer/cooperative/phase/arm',body)
            assert result['armed'] and not result['native_command_called']
            assert calls==[('POST',path,body)]
        assert pair.teleop.native_drive==drives and len(pair.owner.original_publisher.messages)==publications
    finally:close(values)
