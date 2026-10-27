"""Actual native FastAPI/common provider with fake original owners; no devices."""
from copy import deepcopy
import threading
import sys
import time

import pytest

from explorer_shared.native_cooperative05 import NativeCooperativeOwner
from explorer_shared.native_stream03_api import mount_native_stream_api
from test_cooperative_observed_service import Pair,grant,command
from test_cooperative_service import fixture as central_fixture,session
from test_native_activation import TOKEN
from test_native_cooperative05 import Publisher


class NativePair(Pair):
    def __init__(self,tmp_path,service,*,publication_gate=True):
        super().__init__(tmp_path,service.observed_setup);self.publication_gate=publication_gate;self.delayed=None;self.selections=[]
        self.arm.pub=Publisher()
        self.owner=NativeCooperativeOwner(self.mux,self.gate,lambda:{'servo_deg':[90.]*6,'boot_id':'boot','measured':False},target_limits=[(0,180)]*6)
        def select(source,observing,neutral=True):
            assert source=='keyboard' and observing is True and neutral is True
            self.selections.append((source,observing,neutral));self.teleop.owner=source;self.teleop.inputs={}
            return {'owner':source,'generation':self.teleop.generation,'stop_latched':self.teleop.stop_latched}
        self.teleop.select_source=select
        mount_native_stream_api(self.client.app,self.mux,token=TOKEN,observed_test_permit=self.gate.allowed,
            reference_reader=self.owner.reference,activation=self.gate,cooperative=self.owner)
        original_status=self.station.status
        self.station.status=lambda:{**original_status(),'configuration_writes':0}
        service._request=lambda role:self.giver if role=='giver' else self.receiver
    def giver(self,method,path,body):
        self.calls.append(('giver',method,path,deepcopy(body)))
        assert path.startswith('/legacy/explorer/')
        reply=self.client.request(method,'/api/'+path.removeprefix('/legacy/'),headers={'Authorization':'Bearer '+TOKEN},json=body)
        if reply.status_code!=200:raise ValueError(reply.text)
        return reply.json()
    def receiver(self,method,path,body):
        if path=='/station/status':
            self.calls.append(('receiver',method,path,None));assert method=='GET';return self.station.status()
        if path=='/station/observed-test/command':
            self.calls.append(('receiver',method,path,deepcopy(body)));assert method=='POST'
            result=self.provider.command(body['grant_id'],body['binding'],body['operation'],body['body'])
            if self.delayed:
                entered,release=self.delayed;entered.set();assert release.wait(3)
            return result
        result=super().receiver(method,path,body)
        if path=='/station/observed-test/status':
            # Explicit software fixture capability only. No physical driver is
            # present; the real gate/worker qualification is a separate suite.
            result['native_publication_permission_gate_installed']=self.publication_gate
        return result


def configured(tmp_path,*,publication_gate=True,policy_roles=()):
    before=set(sys.modules)
    if policy_roles:
        from test_cooperative_queued_shadows import configured as policy_fixture
        service,store,calls,client,spec,row,_=policy_fixture(tmp_path,input_policy_roles=policy_roles,pin_manual_models=False)
    else:service,store,calls,active,contexts,client=central_fixture(tmp_path);row,spec=session(service)
    pair=NativePair(tmp_path,service,publication_gate=publication_gate)
    grant(service,row,pair,'giver');grant(service,row,pair,'receiver')
    setup=command(service,row,'observed_refresh')
    row=command(service,row,'bind_observed_test',observed_choice_id=setup['bind_choice_id'])
    assert 'torch' not in set(sys.modules)-before
    return service,store,row,spec,pair,client


def choice(service,row,role,operation):
    refreshed=command(service,row,'native_refresh');item=refreshed['roles'][role]['operations'][operation]
    assert item['implemented'] and item['available'],item
    return {'session_id':row['session_id'],'expected_epoch':row['epoch'],'role':role,
        'native_control_choice_id':item['choice_id'],'observing':True,'user_observed_test_confirmed':True,
        **({'neutral':True} if operation in ('native_begin','native_recenter') else {})}


def execute(service,operation,body,actor='trusted-owner'):
    return service.command(operation,body,authenticated_actor=actor)['result']


def test_separate_user_manual_prepare_resume_attach_and_pi_original_start_never_auto_renew_or_imply_handover_ready(tmp_path):
    service,store,row,spec,pair,client=configured(tmp_path)
    try:
        before=len(pair.calls)
        for _ in range(2):service.snapshot(row['session_id'])
        assert len(pair.calls)==before and pair.station.calls==pair.arm.calls==pair.teleop.native_drive==[]
        refreshed=command(service,row,'native_refresh')
        assert all(method=='GET' for _,method,_,_ in pair.calls[before:])
        assert not refreshed['roles']['receiver']['operations']['native_resume']['implemented']
        assert not refreshed['roles']['receiver']['operations']['native_lease']['implemented']
        first=execute(service,'native_begin',choice(service,row,'giver','native_begin'))
        assert first['native_receipt']['prepare'] and pair.selections==[('keyboard',True,True)]
        assert not pair.teleop.native_drive and pair.teleop.stop_latched and not service.transports
        resumed=execute(service,'native_resume',choice(service,row,'giver','native_resume'))
        assert resumed['native_receipt']['neutral_base_host_request_returned'] and pair.teleop.native_drive==[[0.,0.,0.]]
        assert not service.transports
        execute(service,'native_attach',choice(service,row,'giver','native_attach'))
        assert set(service.transports)=={(row['session_id'],'giver')}
        started=execute(service,'native_begin',choice(service,row,'receiver','native_begin'))
        assert started['native_receipt']['native_command_called'] and [op for op,_ in pair.station.calls]==['teleop/start']
        execute(service,'native_attach',choice(service,row,'receiver','native_attach'))
        state=service.snapshot(row['session_id'])
        assert state['execution_authority']['both_current_user_grants_and_native_owners']
        assert not state['execution_authority']['explicit_user_observed_test_current']
        assert state['execution_authority']['reason']=='NATIVE_RECEIVER_DUAL_HOLD_PUBLICATION_INTERLOCK_REQUIRED'
        assert not store.jobs() and service.models=={} and not pair.arm.calls and not pair.arm.publications
        assert not any(path.endswith('/lease') or path.endswith('/observe') for _,_,path,_ in pair.calls)
        assert len(service.workflow.snapshot(row['session_id'])['native_user_control_receipts'])==5
    finally:service.close();pair.close();client.close()


def test_choices_are_actor_role_epoch_one_use_and_ignore_forged_native_identifiers(tmp_path):
    service,store,row,spec,pair,client=configured(tmp_path)
    try:
        body=choice(service,row,'giver','native_begin');before=len(pair.calls)
        with pytest.raises(ValueError,match='USER-bound'):execute(service,'native_begin',body,actor='another-owner')
        with pytest.raises(ValueError,match='Fresh unconsumed'):execute(service,'native_begin',{**body,'role':'receiver'})
        with pytest.raises(ValueError,match='Separate explicit USER'):execute(service,'native_begin',{**body,'neutral':False})
        assert len(pair.calls)==before
        execute(service,'native_begin',{**body,'authorization_id':'forged','control_epoch':'forged','source':'other'})
        actual=next(payload for _,method,path,payload in pair.calls[before:] if method=='POST' and path.endswith('/prepare'))
        assert actual['authorization_id']!='forged'
        with pytest.raises(ValueError,match='Fresh unconsumed'):execute(service,'native_begin',body)
        fresh=choice(service,row,'giver','native_resume');command(service,row,'stop')
        with pytest.raises(ValueError,match='epoch changed'):execute(service,'native_resume',fresh)
        assert pair.teleop.native_drive==[]
    finally:service.close();pair.close();client.close()


def test_missing_actual_pi_write_gate_and_changed_original_native_generation_fail_without_control_calls(tmp_path):
    service,store,row,spec,pair,client=configured(tmp_path,publication_gate=False)
    try:
        refreshed=command(service,row,'native_refresh')
        denied=refreshed['roles']['receiver']['operations']['native_begin']
        assert not denied['available'] and denied['reason']=='NATIVE_USER_PERMISSION_REQUIRED_AT_EVERY_ORIGINAL_WRITE'
        body=choice(service,row,'giver','native_begin');pair.teleop.generation+=1
        with pytest.raises(ValueError,match='owner changed'):execute(service,'native_begin',body)
        assert not pair.selections and pair.station.calls==pair.teleop.native_drive==[]
    finally:service.close();pair.close();client.close()


def test_stop_during_returning_pi_start_cleans_exact_known_epoch_and_cannot_restore_protocol(tmp_path):
    service,store,row,spec,pair,client=configured(tmp_path)
    try:
        body=choice(service,row,'receiver','native_begin');entered,release=threading.Event(),threading.Event()
        pair.delayed=(entered,release);errors=[]
        def start():
            try:execute(service,'native_begin',body)
            except ValueError as error:errors.append(error)
        thread=threading.Thread(target=start);thread.start();assert entered.wait(1)
        command(service,row,'stop');pair.delayed=None;release.set();thread.join(2)
        assert errors and not thread.is_alive() and service.workflow.snapshot(row['session_id'])['status']=='STOPPED'
        assert [op for op,_ in pair.station.calls]==['teleop/start','stop'] and pair.station.mode=='idle'
        assert not service.transports and not pair.provider.status()['execution_permission']
    finally:service.close();pair.close();client.close()


def test_stale_choice_does_not_cleanup_a_different_already_started_user_epoch(tmp_path):
    service,store,row,spec,pair,client=configured(tmp_path)
    try:
        body=choice(service,row,'receiver','native_begin');grant_now=pair.provider.status()['grant']
        current=pair.provider.command(grant_now['grant_id'],grant_now['binding'],'teleop/start',
            {'expected_control_epoch':grant_now['control_epoch']})['grant']
        with pytest.raises(ValueError,match='owner changed'):execute(service,'native_begin',body)
        assert pair.provider.status()['grant']['control_epoch']==current['control_epoch']
        assert [op for op,_ in pair.station.calls]==['teleop/start']
    finally:service.close();pair.close();client.close()


def test_policy_native_begin_registration_resume_attach_and_explicit_recenter_do_not_run_prediction_or_auto_lease(tmp_path,monkeypatch):
    service,store,row,spec,pair,client=configured(tmp_path,policy_roles=('giver',));predictions=[]
    def pure(reference,**kwargs):predictions.append((reference,kwargs));raise AssertionError('No prediction belongs to native USER session setup')
    monkeypatch.setattr(service.native_controls,'_ready_prediction',lambda row,spec,role:pure)
    try:
        begin=execute(service,'native_begin',choice(service,row,'giver','native_begin'))
        assert set(begin['native_receipt'])=={'prepare','bind','constraint','begin'}
        assert pair.mux.active and pair.mux.state=='CLUTCHED' and pair.teleop.native_drive==[[0.,0.,0.]]
        execute(service,'native_resume',choice(service,row,'giver','native_resume'))
        execute(service,'native_attach',choice(service,row,'giver','native_attach'))
        old=pair.mux.epoch;pair.teleop.lease=pair.mux.clock()-1
        execute(service,'native_recenter',choice(service,row,'giver','native_recenter'))
        assert pair.mux.epoch!=old and pair.mux.state=='CLUTCHED'
        execute(service,'native_resume',choice(service,row,'giver','native_resume'))
        assert not predictions and callable(service.models['giver'])
        posts=[path for _,method,path,_ in pair.calls if method=='POST' and path.startswith('/legacy/explorer/cooperative/')]
        assert posts==['/legacy/explorer/cooperative/'+part for part in ('prepare','bind','constraint/register','begin','resume','recenter','resume')]
        assert not any(path.endswith('/input') or path.endswith('/lease') for path in posts)
    finally:service.close();pair.close();client.close()


def test_expired_or_pending_choices_reject_before_any_source_operation(tmp_path):
    service,store,row,spec,pair,client=configured(tmp_path)
    try:
        tick=[time.monotonic_ns()];service.native_controls.clock=lambda:tick[0]
        body=choice(service,row,'receiver','native_begin');before=len(pair.calls);tick[0]+=2_000_000_001
        with pytest.raises(ValueError,match='Fresh unconsumed'):execute(service,'native_begin',body)
        assert len(pair.calls)==before and not pair.station.calls
        tick[0]=time.monotonic_ns();body=choice(service,row,'giver','native_begin')
        with service.workflow.lock:
            current=service.workflow.snapshot(row['session_id']);current['pending_operation']={'operation_id':'actual-pending-fixture'};service.workflow._save(current)
        before=len(pair.calls)
        with pytest.raises(ValueError,match='current protocol step'):execute(service,'native_begin',body)
        assert len(pair.calls)==before and not pair.selections
    finally:service.close();pair.close();client.close()


@pytest.mark.parametrize('blocked',[{'armed':True},{'parked_observation_only':True},{'all_target_writes_blocked':True},{'persistence_fault':'CORRUPT_WAL'}])
def test_generic_receiver_begin_never_offered_over_held_or_faulted_publication_gate(tmp_path,monkeypatch,blocked):
    service,store,row,spec,pair,client=configured(tmp_path)
    original=service.native_controls._source
    def source(role):
        value=original(role)
        if role=='receiver':value['permission']['cooperative_hold_interlock']=blocked
        return value
    monkeypatch.setattr(service.native_controls,'_source',source)
    try:
        result=service.native_controls.refresh(row,spec,actor='trusted-owner');begin=result['roles']['receiver']['operations']['native_begin']
        assert not begin['available'] and begin['reason']=='EXPLICIT_HELD_PHASE_TRANSITION_OR_SAFE_OBJECT_RESET_REQUIRED'
        assert not begin.get('choice_id') and pair.station.calls==[]
    finally:service.close();pair.close();client.close()
