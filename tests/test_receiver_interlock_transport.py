"""Actual COMMON/WAL/native metadata facade, original fake owner, no motors."""
from copy import deepcopy
import threading
import time

import pytest

from explorer_shared.cooperative_receiver_transport05 import NativeReceiverTransport
from explorer_shared.cooperative_contract05 import ROLE_CONTRACTS
from explorer_shared.handover_adapters import OperatorEvidenceInbox
from explorer_shared.station_publication_gate import StationPublicationGate
from test_station_cooperative_interlock import rig as native_rig,call,base
from test_cooperative_receiver_interlock05 import giver_release
from test_station_observed_activation import authorize


def rig(tmp_path,*,connected=False,bound=True):
    wrapper,provider,station,camera,native_tick,grant,profile=native_rig(tmp_path)
    provider.publication_gate=StationPublicationGate(provider);provider.cooperative_interlock=wrapper
    if bound:call(wrapper,'bind',{**base(grant),'expected_revision':0,'direction_profile_id':profile['profile_sha256']})
    calls=[];nx=[70_000_000_000]
    def request(method,path,body):
        calls.append((method,path,deepcopy(body)))
        if path=='/station/observed-test/status':assert method=='GET';return provider.status()
        if path=='/station/status':assert method=='GET';return {**station.status(),'configuration_writes':0,'last_command':deepcopy(station.last_command)}
        if path=='/station/observed-test/cooperative/status':assert method=='GET';return wrapper.status()
        prefix='/station/observed-test/cooperative/'
        if path.startswith(prefix):
            assert method=='POST';body=deepcopy(body);actor=body.pop('actor_id')
            return wrapper.dispatch(path[len(prefix):],body,authenticated_actor=actor)
        if path.endswith('/revoke'):return provider.revoke(body['reason'],expected_grant_id=body['expected_grant_id'],
            expected_control_epoch=body['expected_control_epoch'],expected_binding=body['binding'])
        if path.endswith('/command'):return provider.command(body['grant_id'],body['binding'],body['operation'],body['body'])
        raise AssertionError('No invented native transport route: '+path)
    binding={'run_id':'run','object_id':'object','coordinator_epoch':'coord'}
    reference={'skill_id':grant['binding']['skill_id'],'skill_version':1,'spec_sha256':grant['binding']['spec_sha256'],
        'action_contract':ROLE_CONTRACTS['receiver']}
    transport=NativeReceiverTransport(request,binding,grant['binding'],grant,reference,clock_domain='nx:independent-boot',
        clock=lambda:nx[0],dual_hold_integration_ready=connected)
    assert calls==[]
    row={'session_id':'run','protocol_object_id':'object','epoch':'coord'}
    return transport,row,wrapper,provider,station,camera,native_tick,nx,calls,grant


def test_capability_current_owner_and_explicit_coordinator_hook_flag_are_independent(tmp_path):
    transport,row,wrapper,provider,station,camera,tick,nx,calls,grant=rig(tmp_path)
    state=transport.observe_state()
    assert state['current_native_owner_ready'] and state['receiver_interlock']['source_interlock_current']
    assert not state['cooperative_dual_hold_publication_interlock_current']
    before=list(calls);cached=transport.interlock_state()
    assert calls==before and cached['cached_snapshot_only'] and cached['source_current_at_last_fetch']
    assert not cached['source_interlock_current'] and not cached['cooperative_dual_hold_publication_interlock_current']
    transport.dual_hold_integration_ready=True
    assert transport.observe_state()['cooperative_dual_hold_publication_interlock_current']
    wrapper.helper.direction=None
    state=transport.observe_state()
    assert state['current_native_owner_ready'] and not state['cooperative_dual_hold_publication_interlock_current']


def test_arm_uses_actual_returned_pose_exact_native_ids_revision_actor_no_motor_request(tmp_path):
    transport,row,wrapper,provider,station,camera,tick,nx,calls,grant=rig(tmp_path,connected=True)
    before=list(station.calls);result=transport.arm_release_guard(row)
    armed=result['native_receiver_dual_hold_guard']
    assert armed['armed'] and armed['frozen']['original_last_command']==station.last_command
    posts=[(path,body) for method,path,body in calls if method=='POST']
    assert len(posts)==1 and posts[0][0]=='/station/observed-test/cooperative/arm'
    assert posts[0][1]=={**base(grant),'expected_revision':1,'interlock_id':armed['interlock_id'],'actor_id':grant['actor_id']}
    assert station.calls==before and not result['hardware_command_sent']
    assert wrapper.check_publication(station.control_epoch,station.mode,[1.,2.,3.,4.,5.,50.])['allowed']
    assert not wrapper.check_publication('new-unowned','teleoperation',[0.]*6)['allowed']


def test_unknown_binding_or_original_permission_never_arms_or_uses_cached_authority(tmp_path):
    transport,row,wrapper,provider,station,camera,tick,nx,calls,grant=rig(tmp_path,connected=True,bound=False)
    with pytest.raises(ValueError,match='capability required'):transport.arm_release_guard(row)
    assert not any(method=='POST' for method,_,_ in calls) and not wrapper.status()['armed']
    before=list(calls)
    with pytest.raises(ValueError,match='Old cooperative'):transport.arm_release_guard(dict(row,epoch='old-coord'))
    assert calls==before
    tick[0]+=2_100_000_000;camera.publish()
    assert not transport.observe_state()['current_native_owner_ready']
    with pytest.raises(ValueError):transport.arm_release_guard(row)
    assert not any(method=='POST' for method,_,_ in calls)


def test_current_permission_without_installed_original_publication_gate_is_never_full_authority(tmp_path):
    transport,row,wrapper,provider,station,camera,tick,nx,calls,grant=rig(tmp_path,connected=True)
    provider.publication_gate=None
    state=transport.observe_state()
    assert state['current_native_owner_ready'] and not state['cooperative_dual_hold_publication_interlock_current']
    with pytest.raises(ValueError,match='capability required'):transport.arm_release_guard(row)
    assert not wrapper.status()['armed'] and not any(method=='POST' for method,_,_ in calls)


def test_late_arm_response_after_stop_retains_latch_cannot_adopt_new_grant_or_unarm(tmp_path):
    transport,row,wrapper,provider,station,camera,tick,nx,calls,grant=rig(tmp_path,connected=True)
    entered,release=threading.Event(),threading.Event();original=transport.permission.request;errors=[]
    def blocked(method,path,body):
        result=original(method,path,body)
        if path.endswith('/cooperative/arm'):entered.set();assert release.wait(2)
        return result
    transport.permission.request=blocked
    def arm():
        try:transport.arm_release_guard(row)
        except ValueError as error:errors.append(error)
    thread=threading.Thread(target=arm);thread.start();assert entered.wait(1)
    transport.stop('run');new,_=authorize(provider,grant['binding']);release.set();thread.join(2)
    assert errors and not thread.is_alive() and wrapper.status()['armed']
    assert provider.status()['grant']['grant_id']==new['grant_id'] and not transport.observe_state()['current_native_owner_ready']
    assert not wrapper.check_publication('new-unowned','policy',[0.]*6)['allowed']
    assert not any(path.endswith('/cooperative/finish') or path.endswith('/cooperative/reset_safe') for _,path,_ in calls)


def independent(inbox,row,after,nx):
    answers=[]
    thread=threading.Thread(target=lambda:answers.append(inbox('object_transferred_to_receiver',row['session_id'],
        row['protocol_object_id'],'receiver',after,1.)))
    nx[0]+=1;thread.start();deadline=time.monotonic()+1
    while time.monotonic()<deadline:
        pending=inbox.pending()
        if pending:break
        time.sleep(.001)
    assert pending;nx[0]+=1
    inbox.confirm(pending[0]['challenge_id'],{'affirmed':True,'observing':True},authenticated_actor='actual-server-owner')
    thread.join(1);assert not thread.is_alive();nx[0]+=1
    return answers[0].source_receipt


def test_actual_native_challenge_and_consumed_inbox_proof_unfreeze_without_clock_equation_or_command(tmp_path):
    transport,row,wrapper,provider,station,camera,tick,nx,calls,grant=rig(tmp_path,connected=True)
    before=list(station.calls);transport.arm_release_guard(row);pending=transport.finish_challenge(row,giver_release())
    assert wrapper.status()['armed'] and pending['challenge']['clock_domain']!=transport.domain
    assert pending['metadata_does_not_unfreeze'] and not pending['cross_clock_conversion_assumed']
    inbox=OperatorEvidenceInbox(tmp_path/'independent-inbox',clock=lambda:nx[0],clock_domain=transport.domain)
    proof=independent(inbox,row,pending['challenge']['giver_release_receipt_id'],nx)
    finished=transport.finish_interlock(row,pending['challenge']['challenge_id'],proof)
    assert not wrapper.status()['armed'] and wrapper.status()['phase']=='UNFROZEN_TO_FINISH'
    assert finished['independent_confirmation']['coordinator_challenge_received_ns']==pending['received_coordinator_ns']
    assert station.calls==before and not finished['hardware_command_sent'] and not finished['full_task_success_claimed']
    with pytest.raises(ValueError,match='Consumed independent'):transport.finish_interlock(row,pending['challenge']['challenge_id'],proof)


def test_old_independent_proof_or_native_timer_never_unfreezes_and_never_auto_renews(tmp_path):
    transport,row,wrapper,provider,station,camera,tick,nx,calls,grant=rig(tmp_path,connected=True)
    transport.arm_release_guard(row);pending=transport.finish_challenge(row,giver_release())
    inbox=OperatorEvidenceInbox(tmp_path/'independent-inbox',clock=lambda:nx[0],clock_domain=transport.domain)
    proof=independent(inbox,row,pending['challenge']['giver_release_receipt_id'],nx)
    stale=deepcopy(proof);stale['confirmation']['created_ns']=pending['received_coordinator_ns']-1
    before=list(calls)
    with pytest.raises(ValueError,match='Consumed independent'):transport.finish_interlock(row,pending['challenge']['challenge_id'],stale)
    assert calls==before and wrapper.status()['armed']
    tick[0]+=2_000_000_001;camera.publish()
    with pytest.raises(ValueError):transport.finish_interlock(row,pending['challenge']['challenge_id'],proof)
    assert wrapper.status()['armed'] and not any(path.endswith('/observe') or 'lease' in path for _,path,_ in calls)
