"""Actual authenticated native ASGI ABI + mocked original publisher only."""
from copy import deepcopy
import time
from urllib.parse import urlsplit

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from explorer_shared.cooperative_contract05 import ROLE_CONTRACTS
from explorer_shared.cooperative_transport05 import NativeGiverPolicyTransport
from explorer_shared.native_stream03_api import mount_native_stream_api
from test_native_cooperative05 import fixture,start,confirmed


def setup(*,emit=True,constraint_factory=None):
    source,mux,teleop,arm,publisher,tap,activation,now,binding=fixture();constraint=None
    if constraint_factory:
        constraint=constraint_factory(binding,{'model_id':'actual-source-model','checkpoint_sha256':'a'*64,'spec_sha256':'b'*64})
        source.register_constraint({**binding,'constraint':constraint})
    epoch,model=start(source,mux,binding)
    app=FastAPI();mount_native_stream_api(app,mux,token='private-fixture-token',observed_test_permit=activation.allowed,
        reference_reader=source.reference,activation=activation,cooperative=source)
    client=TestClient(app);calls=[];pending=[]
    def request(method,path,body):
        calls.append((method,path,deepcopy(body)))
        if emit and method=='GET' and '/dispatch?' in path and pending:
            incoming=pending.pop(0);record={'command_generation':mux.generation,'input_received_at':arm.stream_intent['received_at']}
            arm._publish_command([round(value) for value in incoming['action9'][:6]],80,record)
        result=client.request(method,path.replace('/legacy/','/api/',1),headers={'Authorization':'Bearer private-fixture-token'},
            json=body if body is not None else None)
        if result.status_code!=200:raise ValueError(str(result.json()))
        if method=='POST' and path.endswith('/input'):pending.append(deepcopy(body))
        return result.json()
    def predict(command):
        observation=request('GET','/legacy/explorer/cooperative/observation',None)
        return {'action_contract':ROLE_CONTRACTS['giver'],'model':model,'action9':[90.]*5+[80. if command['operation']=='release' else 91.]+[0.,0.,0.],
            'source_sequence':mux.sequence+1,'source_observation':observation}
    transport=NativeGiverPolicyTransport(request,binding,epoch,predict,clock_domain='NX:test-boot',timeout_s=.04,action_constraint=constraint,
        receiver_command=lambda:{'actual_native_host_dispatch':True,'command_id':'receiver-close-actual','source_receipt':{'native_mock_write':True}})
    command={'command_id':'actual-command-1','run_id':binding['run_id'],'object_id':binding['object_id'],'role':'giver',
        'coordinator_epoch':binding['coordinator_epoch'],'operation':'offer','reference':model}
    return transport,command,source,mux,teleop,arm,publisher,tap,calls,binding


def test_real_native_api_acceptance_followed_by_actual_correlated_mock_host_publication():
    transport,command,source,mux,teleop,arm,publisher,tap,calls,binding=setup();lease=teleop.lease
    result=transport.submit_observed(command)
    assert result.dispatched and result.command_id==command['command_id']
    assert result.source['basis']=='actual_original_native_host_publication' and not result.source['attainment_verified']
    assert len(publisher.messages)==2 and teleop.lease==lease
    assert not any(path.endswith('/lease') or path.endswith('/resume') or path.endswith('/begin') for _,path,_ in calls)


def test_native_api_ack_alone_times_out_without_fabricated_dispatch():
    transport,command,source,mux,teleop,arm,publisher,tap,calls,binding=setup(emit=False)
    with pytest.raises(TimeoutError,match='accepted but no correlated'):transport.submit_observed(command)
    assert len(publisher.messages)==1 and any(path.endswith('/input') for _,path,_ in calls)


def test_release_challenge_predates_new_consumed_operator_hold_and_exact_opening_grant_is_required():
    transport,command,source,mux,teleop,arm,publisher,tap,calls,binding=setup()
    session={'session_id':binding['run_id'],'protocol_object_id':binding['object_id'],'epoch':binding['coordinator_epoch']}
    transport.arm_release_guard(session);wrapper=transport.prepare_release(session,None)
    proof=confirmed(binding,wrapper['challenge'])['operator_confirmation']
    proof.update(created_ns=time.monotonic_ns(),received_ns=time.monotonic_ns(),clock_domain='NX:test-boot')
    authorization={'native_release_challenge':wrapper,'operator_confirmation_receipt':{'confirmation':proof}}
    result=transport.submit_observed({**command,'operation':'release'},authorization)
    native=result.source['native_receipt'];boundary=native['original_payload']['cooperative_publication_boundary']
    assert boundary['opening'] and boundary['guard_grant']['operator_confirmation']==proof
    assert len(publisher.messages)==2 and publisher.messages[-1].joint6==80
    assert boundary['native_clock_domain']!='NX:test-boot'


def test_consumed_confirmation_from_before_native_challenge_cannot_authorize_opening():
    transport,command,source,mux,teleop,arm,publisher,tap,calls,binding=setup()
    session={'session_id':binding['run_id'],'protocol_object_id':binding['object_id'],'epoch':binding['coordinator_epoch']}
    transport.arm_release_guard(session);wrapper=transport.prepare_release(session,None)
    proof=confirmed(binding,wrapper['challenge'])['operator_confirmation']
    proof.update(created_ns=wrapper['received_coordinator_ns']-1,received_ns=time.monotonic_ns(),clock_domain='NX:test-boot')
    with pytest.raises(ValueError,match='created after this exact native challenge'):
        transport.submit_observed({**command,'operation':'release'},
            {'native_release_challenge':wrapper,'operator_confirmation_receipt':{'confirmation':proof}})
    assert len(publisher.messages)==1 and not any(path.endswith('/input') for _,path,_ in calls)
