"""Actual adapter callbacks and durable operator challenge tests; no hardware IO."""
from dataclasses import replace
import json
from pathlib import Path
import threading
import time

import pytest

from explorer_shared.handover_adapters import (OperatorEvidenceInbox, ROSMASTERArmTransport,
    NativeDispatchProof, BindingBlocked, inspect_native_observability)
from explorer_shared.handover_runtime import ArmCommand
from explorer_shared.pi_nano_route import NanoArmGateway
from test_pi_nano_route import OriginalHTTPFixture, DirectClientFixture


def challenge(inbox, statement='receiver_holds_object', *, role='receiver', after='close-1'):
    values,errors=[],[]
    def waiting():
        try:values.append(inbox(statement,'run-1','candy-1',role,after,2))
        except Exception as error:errors.append(error)
    thread=threading.Thread(target=waiting);thread.start()
    for _ in range(100):
        pending=inbox.pending()
        if pending:break
        time.sleep(.005)
    assert len(pending)==1
    return pending[0],thread,values,errors


def test_authenticated_single_use_operator_evidence_keeps_original_correlation(tmp_path):
    inbox=OperatorEvidenceInbox(tmp_path)
    pending,thread,values,errors=challenge(inbox)
    confirmed=inbox.confirm(pending['challenge_id'],{'affirmed':True,'observing':True},authenticated_actor='owner-session')
    thread.join(2)
    assert not thread.is_alive() and not errors
    result=values[0]
    assert (result.run_id,result.object_id,result.role,result.after_command_id)==('run-1','candy-1','receiver','close-1')
    assert result.evidence.kind=='operator_confirmation' and result.evidence.actor=='operator'
    assert result.evidence.independent is False  # Human judgement, never a force measurement.
    assert result.evidence.received_ns==confirmed['received_ns']
    assert result.source_receipt['confirmation']['acquired_time_basis'].startswith('operator_confirmation_received_at_server')
    persisted=json.loads((tmp_path/(pending['challenge_id']+'.json')).read_text())
    assert persisted['state']=='consumed' and persisted['authenticated_actor']=='owner-session'
    with pytest.raises(ValueError):inbox.confirm(pending['challenge_id'],{'affirmed':True,'observing':True},authenticated_actor='owner-session')


@pytest.mark.parametrize('body',[{'affirmed':True,'observing':False},{'affirmed':False,'observing':True},
    {'affirmed':True,'observing':True,'object_id':'foreign'}, {'affirmed':True,'observing':True,'actor':'operator'}])
def test_body_cannot_assert_authentication_or_rebind_object_or_skip_observation(tmp_path,body):
    inbox=OperatorEvidenceInbox(tmp_path)
    pending,thread,values,errors=challenge(inbox)
    try:
        with pytest.raises(ValueError):inbox.confirm(pending['challenge_id'],body,authenticated_actor='owner-session')
        assert not values
    finally:inbox.cancel_run('run-1');thread.join(2)
    assert isinstance(errors[0],RuntimeError)


def test_restart_or_cancel_cannot_reuse_old_confirmation(tmp_path):
    first=OperatorEvidenceInbox(tmp_path)
    pending,thread,values,errors=challenge(first)
    second=OperatorEvidenceInbox(tmp_path)
    assert second.pending()==[]
    with pytest.raises(ValueError):second.confirm(pending['challenge_id'],{'affirmed':True,'observing':True},authenticated_actor='owner')
    first.cancel_run('run-1');thread.join(2)
    assert not values and isinstance(errors[0],RuntimeError)
    assert json.loads((tmp_path/(pending['challenge_id']+'.json')).read_text())['state']=='cancelled'


def test_timeout_never_synthesizes_hold_or_release_authorization(tmp_path):
    inbox=OperatorEvidenceInbox(tmp_path)
    with pytest.raises(TimeoutError):inbox('receiver_holds_object','run','object','receiver','close',.01)
    assert inbox.pending()==[]
    assert json.loads(next(tmp_path.glob('*.json')).read_text())['state']=='expired'


def test_stationary_confirmation_is_explicit_and_not_inferred_from_idle_or_busy_false(tmp_path):
    inbox=OperatorEvidenceInbox(tmp_path)
    result=[]
    thread=threading.Thread(target=lambda:result.append(inbox.stationary_observation('run','object','giver','offer',2)))
    thread.start()
    for _ in range(100):
        pending=inbox.pending()
        if pending:break
        time.sleep(.005)
    assert pending[0]['statement']=='giver_stationary' and not result
    inbox.confirm(pending[0]['challenge_id'],{'affirmed':True,'observing':True},authenticated_actor='owner')
    thread.join(2)
    assert result[0].stationary and result[0].after_command_id=='offer'
    assert result[0].source_receipt['kind']=='explicit_authenticated_operator_stationary_confirmation'
    assert not result[0].source_receipt['measured_joint_feedback_claimed']


class Client:
    def __init__(self):
        self.original=OriginalHTTPFixture()
        registration={'state':'REGISTERED_DECLARATION','registration_id':'software-fixture','registration_sha256':'fixture-sha'}
        self.gateway=NanoArmGateway(self.original.request,registration)
        self.direct=DirectClientFixture(self.gateway)
        self.calls=[]
    def status(self):self.calls.append('status');return self.direct.status()
    def start(self,body):self.calls.append('start');return self.direct.start(body)
    def action(self,body):self.calls.append('action');return self.direct.action(body)
    def heartbeat(self,body):self.calls.append('lease');return self.direct.heartbeat(body)
    def stop(self,body):self.calls.append('stop');return self.direct.stop(body)


def binding(tmp_path,proof=True):
    client=Client()
    inbox=OperatorEvidenceInbox(tmp_path)
    owner={'run_id':'run-1','owner_id':'handover:run-1','resource':'m3-arm','valid':True,'epoch':'root-fixture'}
    def dispatched(context,timeout):
        # This is an explicit SOFTWARE ONLY native-send receipt source fixture.
        return NativeDispatchProof(context['run_id'],context['object_id'],context['command_id'],context['plan_id'],
            context['target_session'],tuple(context['goal_deg']),100,'nano-fixture-boot',time.monotonic_ns(),
            'handover-coordinator',{'basis':'native_robotio_send_receipt','software_fixture_only':True})
    transport=ROSMASTERArmTransport(client,inbox,'run-1','candy-1',lambda:dict(owner),lambda:{'operator_near':True},
        dispatch_proof=dispatched if proof else None)
    return transport,client,inbox,owner


def offer():return ArmCommand('offer-1','giver','offer',{'goal_deg':[90,60,145,65,90,100]})


def test_actual_native_binding_defaults_to_blocked_without_send_evidence_before_any_command(tmp_path):
    transport,client,_,_=binding(tmp_path,proof=False)
    assert not transport.capability()['available']
    with pytest.raises(BindingBlocked) as failure:transport.submit(offer(),'run-1','candy-1',None)
    assert failure.value.code=='BLOCKED_NATIVE_DISPATCH'
    assert client.calls==[] and client.original.calls==[]


def test_qualified_software_proof_binds_original_plan_start_then_explicit_lease_and_stop(tmp_path):
    transport,client,_,_=binding(tmp_path)
    receipt=transport.submit(offer(),'run-1','candy-1',None)
    assert receipt.dispatched and receipt.command_id=='offer-1'
    assert receipt.source['native_dispatch_proof']['source_receipt']['software_fixture_only']
    assert not receipt.source['hardware_reach_or_hold_claimed']
    assert [op for op,_ in client.original.calls][-2:]==['arm/trajectory/plan','arm/trajectory/start']
    assert client.calls.count('lease')==0  # submit never invents heartbeat.
    transport.held(held=True,observing=True)
    assert client.original.calls[-1][0]=='arm/trajectory/lease'
    stopped=transport.stop('run-1')
    assert not stopped['physical_stop_confirmed'] and client.original.calls[-1][0]=='arm/trajectory/stop'


@pytest.mark.parametrize('defect',['plan','session','command','run','object','endpoint','clock','basis','future'])
def test_unrelated_or_stale_native_receipt_cannot_become_dispatch(tmp_path,defect):
    transport,client,_,_=binding(tmp_path)
    actual=transport.dispatch_proof
    def wrong(context,timeout):
        p=actual(context,timeout)
        return replace(p,**{'plan':{'plan_id':'foreign'},'session':{'target_session':'foreign'},'command':{'command_id':'foreign'},
            'run':{'run_id':'foreign'},'object':{'object_id':'foreign'},'endpoint':{'goal_deg':(0,)*6},
            'clock':{'received_clock_domain':'foreign'},'basis':{'source_receipt':{'basis':'API_ACK'}},
            'future':{'received_ns':time.monotonic_ns()+10_000_000_000}}[defect])
    transport.dispatch_proof=wrong
    with pytest.raises(BindingBlocked) as failure:transport.submit(offer(),'run-1','candy-1',None)
    assert failure.value.code=='BLOCKED_NATIVE_DISPATCH'
    assert client.calls.count('action')==1  # No automatic retry.


def test_release_requires_native_boundary_permit_guard_before_any_submission(tmp_path):
    transport,client,_,_=binding(tmp_path)
    release=ArmCommand('release-1','giver','release',{'goal_deg':[90,60,145,65,90,30]})
    with pytest.raises(BindingBlocked) as failure:transport.submit(release,'run-1','candy-1',None)
    assert failure.value.code=='BLOCKED_RELEASE_DEADLINE_AT_NATIVE_BOUNDARY' and client.calls==[]


def test_measured_registration_and_current_root_ownership_are_required(tmp_path):
    transport,client,_,owner=binding(tmp_path)
    owner['valid']=False
    with pytest.raises(BindingBlocked) as blocked:transport.submit(offer(),'run-1','candy-1',None)
    assert blocked.value.code=='BLOCKED_ARM_OWNERSHIP' and client.calls==[]
    owner['valid']=True
    client.gateway.registration={'state':'UNREGISTERED'}
    with pytest.raises(BindingBlocked) as blocked:transport.submit(offer(),'run-1','candy-1',None)
    assert blocked.value.code=='BLOCKED_MEASURED_REGISTRATION' and client.calls==['status']


def test_original_feedback_command_echo_and_busy_false_never_qualify_stationarity():
    calls=[]
    def request(method,path,body):
        calls.append((method,path,body))
        return {'measured':{'available':False,'reason':'NO_PUBLISHER_IN_CURRENT_ROS_GRAPH'},'command_echo_is_measurement':False,
            'force_measured':False} if path.endswith('feedback') else {'busy':False,'estimated_only':True,'servo_deg':[90]*6}
    result=inspect_native_observability(request)
    assert not result['stationary_qualified'] and not result['native_dispatch_qualified']
    assert result['measured_feedback_reason']=='NO_PUBLISHER_IN_CURRENT_ROS_GRAPH'
    assert all(method=='GET' and body is None for method,_,body in calls)


def _confirm_from_separate_process(folder, session_id, challenge_id, queue):
    attached=OperatorEvidenceInbox(folder,session_id=session_id)
    pending=attached.pending('run-1')
    attached.confirm(challenge_id,{'affirmed':True,'observing':True},authenticated_actor='authenticated-owner-fixture')
    queue.put(len(pending))


def test_authenticated_api_process_can_confirm_waiting_worker_via_durable_attempt_session(tmp_path):
    import multiprocessing
    inbox=OperatorEvidenceInbox(tmp_path)
    values=[]
    thread=threading.Thread(target=lambda:values.append(inbox('receiver_holds_object','run-1','object','receiver','close',10)))
    thread.start()
    for _ in range(100):
        pending=inbox.pending()
        if pending:break
        time.sleep(.005)
    ctx=multiprocessing.get_context('spawn')
    queue=ctx.Queue()
    process=ctx.Process(target=_confirm_from_separate_process,args=(str(tmp_path),inbox.session_id,pending[0]['challenge_id'],queue))
    process.start();process.join(10);thread.join(2)
    assert process.exitcode==0 and not thread.is_alive() and queue.get(timeout=1)==1
    assert values[0].source_receipt['confirmation']['authenticated_actor']=='authenticated-owner-fixture'
    assert values[0].evidence.statement=='receiver_holds_object'


def test_two_attached_api_handlers_cannot_consume_same_operator_challenge_twice(tmp_path):
    inbox=OperatorEvidenceInbox(tmp_path)
    pending,waiter,values,errors=challenge(inbox)
    first=OperatorEvidenceInbox(tmp_path,session_id=inbox.session_id)
    second=OperatorEvidenceInbox(tmp_path,session_id=inbox.session_id)
    successes,failures=[],[]
    def confirming(attached):
        try:successes.append(attached.confirm(pending['challenge_id'],{'affirmed':True,'observing':True},authenticated_actor='owner'))
        except ValueError as error:failures.append(error)
    threads=[threading.Thread(target=confirming,args=(attached,)) for attached in (first,second)]
    for thread in threads:thread.start()
    for thread in threads:thread.join(2)
    waiter.join(2)
    assert len(successes)==1 and len(failures)==1 and len(values)==1 and not errors


def test_giver_release_never_moves_arm_joints_during_dual_hold(tmp_path):
    transport,client,_,_=binding(tmp_path)
    transport.submit(offer(),'run-1','candy-1',None)
    client.original.complete()
    transport.release_submit=lambda *_:pytest.fail('Inconsistent release must not reach a dispatcher')
    release=ArmCommand('release-1','giver','release',{'goal_deg':[91,60,145,65,90,30]})
    permit=dict(run_id='run-1',object_id='candy-1',clock_domain='handover-coordinator',evidence_id='held-fixture',valid_until_ns=time.monotonic_ns()+1_000_000_000)
    with pytest.raises(ValueError,match='only gripper'):transport.submit(release,'run-1','candy-1',permit)
    assert client.calls.count('action')==1


def test_software_native_boundary_guard_must_return_matching_release_permit_receipt(tmp_path):
    transport,client,_,_=binding(tmp_path)
    transport.submit(offer(),'run-1','candy-1',None)
    client.original.complete()
    checks=[]
    def guard(request_client,body,authorization):
        assert time.monotonic_ns()<=authorization['valid_until_ns']
        checks.append(dict(authorization))
        return {**request_client.action(body),'authorization_checked_at_native_dispatch':True}
    transport.release_submit=guard
    release=ArmCommand('release-1','giver','release',{'goal_deg':[90,60,145,65,90,30]})
    permit=dict(run_id='run-1',object_id='candy-1',clock_domain='handover-coordinator',evidence_id='held-fixture',valid_until_ns=time.monotonic_ns()+1_000_000_000)
    result=transport.submit(release,'run-1','candy-1',permit)
    assert result.dispatched and checks==[permit]
    assert result.source['gateway_api_receipt']['authorization_checked_at_native_dispatch']
    assert client.calls.count('lease')==0
