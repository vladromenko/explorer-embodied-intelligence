"""Exact audited ManualTeleop call ABI, fake owner/publisher only."""
import time

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from explorer_shared.cooperative_transport05 import NativeGiverManualTransport
from explorer_shared.native_stream03_api import mount_native_stream_api
from test_native_cooperative05 import fixture,confirmed,message


def rig():
    source,mux,teleop,arm,publisher,tap,activation,now,binding=fixture();activation.mode='keyboard';selections=[]
    def select(source,observing,neutral=True):
        assert source=='keyboard' and observing is True and neutral is True
        selections.append((source,observing,neutral));teleop.owner=source;teleop.inputs={}
        return {'owner':source,'generation':teleop.generation,'stop_latched':teleop.stop_latched}
    teleop.select_source=select
    app=FastAPI();mount_native_stream_api(app,mux,token='manual-native-fixture-private',observed_test_permit=activation.allowed,
        reference_reader=source.reference,activation=activation,cooperative=source)
    client=TestClient(app);calls=[]
    def request(method,path,body):
        calls.append((method,path,body))
        reply=client.request(method,path.replace('/legacy/','/api/',1),json=body,
            headers={'Authorization':'Bearer manual-native-fixture-private'})
        if reply.status_code!=200:raise ValueError(reply.text)
        return reply.json()
    return source,mux,teleop,arm,publisher,tap,activation,now,binding,request,selections


def start(source,mux,binding,request):
    prepared=request('POST','/legacy/explorer/cooperative/prepare',{**binding,'neutral':True})
    request('POST','/legacy/explorer/cooperative/bind',binding)
    generation=prepared['native']['owner_generation']
    result=request('POST','/legacy/explorer/cooperative/manual/resume',{**binding,'expected_native_generation':generation})
    assert result['neutral_base_host_request_returned']
    return NativeGiverManualTransport(request,binding,generation,clock_domain='nx:separate-native-clock',
        receiver_command=lambda:{'actual_native_host_dispatch':True,'command_id':'receiver-close-actual','source_receipt':{'native_mock_write':True}})


def test_user_prepare_and_manual_resume_use_same_original_owner_neutral_receipt_and_no_model():
    source,mux,teleop,arm,publisher,tap,activation,now,binding,request,selections=rig()
    prepared=request('POST','/legacy/explorer/cooperative/prepare',{**binding,'neutral':True})
    assert selections==[('keyboard',True,True)] and not teleop.native_drive and not publisher.messages and not arm.calls
    request('POST','/legacy/explorer/cooperative/bind',binding)
    generation=prepared['native']['owner_generation']
    request('POST','/legacy/explorer/cooperative/manual/resume',{**binding,'expected_native_generation':generation})
    transport=NativeGiverManualTransport(request,binding,generation,clock_domain='nx:separate-native-clock')
    assert transport.observe_state()['current_native_owner_ready'] and teleop.native_drive==[[0.,0.,0.]] and not mux.active
    assert tap.since()['receipts'][-1]['kind']=='base_drive_host_request'
    with pytest.raises(ValueError,match='never submits hidden'):transport.submit_observed({})
    assert not arm.calls and not publisher.messages and not transport.observe_state()['policy_model_required']


def test_scoped_manual_stop_generation_authorization_fences_and_actual_same_original_stop():
    source,mux,teleop,arm,publisher,tap,activation,now,binding,request,selections=rig()
    transport=start(source,mux,binding,request);generation=teleop.generation
    with pytest.raises(ValueError,match='another native generation'):
        request('POST','/legacy/explorer/cooperative/stop',{**binding,'expected_native_generation':generation-1})
    activation.id='new-user-auth'
    with pytest.raises(ValueError,match='another native generation'):transport.stop(binding['run_id'])
    assert teleop.native_stop==0
    activation.id='auth';transport.stop(binding['run_id'])
    assert teleop.native_stop==1 and teleop.stop_latched and teleop.generation==generation+1
    with pytest.raises(ValueError):transport.stop(binding['run_id'])
    assert teleop.native_stop==1


def test_manual_release_only_authorizes_native_boundary_after_current_correlated_hold_never_opens_itself():
    source,mux,teleop,arm,publisher,tap,activation,now,binding,request,selections=rig()
    transport=start(source,mux,binding,request)
    session={'session_id':binding['run_id'],'protocol_object_id':binding['object_id'],'epoch':binding['coordinator_epoch']}
    arm._publish_command([90]*6,80,{})  # actual fake original-arm publication establishes issued context.
    transport.arm_release_guard(session);arm.pub.publish(message(100))
    wrapper=transport.prepare_release(session,None)
    proof=confirmed(binding,wrapper['challenge'])['operator_confirmation']
    proof.update(created_ns=time.monotonic_ns(),received_ns=time.monotonic_ns(),clock_domain=transport.domain)
    before=len(publisher.messages)
    grant=transport.authorize_manual_release(session,{'native_release_challenge':wrapper,
        'operator_confirmation_receipt':{'confirmation':proof}})
    assert grant['grant_id'] and len(publisher.messages)==before
    arm.pub.publish(message(80));assert len(publisher.messages)==before+1
    assert source.guard.last_publication['guard_grant']['grant_id']==grant['grant_id']
