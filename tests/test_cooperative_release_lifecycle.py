"""Full Root/source release evidence over fake original Publisher, no devices/ML."""
from copy import deepcopy
import time
import uuid
from types import MethodType

from explorer_shared.cooperative_contract05 import ROLE_CONTRACTS
from explorer_shared.handover import HandoverFSM,HoldEvidence
from test_cooperative_observed_scopes import Sources,ACTOR
from test_cooperative_parked_release import body
from test_cooperative_workflow05 import metric_zone
from test_native_cooperative05 import OriginalArm


def release_ready(sources):
    service=sources.service;service._arm_guard(sources.row);now=service.clock()
    fsm=HandoverFSM(sources.row['protocol_object_id'],mode='physical',clock_domain=service.domain,run_id=sources.row['session_id'])
    evidence=HoldEvidence(uuid.uuid4().hex,sources.row['protocol_object_id'],'operator','operator_confirmation','giver_holds_object',False,now,service.domain,now,service.domain)
    for event,facts in [('giver_holding',{'evidence':evidence}),('offer_ready',{'zone_registered':True,'giver_stationary':True}),
            ('receiver_ready',{'receiver_ready':True}),('receiver_at_object',{'both_stationary':True}),('receiver_closed',{})]:
        fsm.apply(event,event_id=uuid.uuid4().hex,now_ns=now,**facts)
    metric_zone(service.workflow,sources.row)  # Explicit fixture calibration, never physical registration proof.
    with service.workflow.lock:
        row=service.workflow.snapshot(sources.row['session_id']);row['protocol_state']=fsm.state;row['protocol_trace']=deepcopy(fsm.trace);service.workflow._save(row)
    return row


def original_policy_fixture(sources):
    service=sources.service;pair=sources.pair;pending=[];predictions=[];native_request=pair.giver
    pair.arm.motion_config=deepcopy(OriginalArm().motion_config)
    pair.arm.model=MethodType(OriginalArm.model,pair.arm)
    pair.mux.original['arm_publish']=MethodType(OriginalArm._publish_command,pair.arm)
    pair.mux.publication_evidence=lambda:deepcopy(pair.owner.guard.last_publication)
    def routed(method,path,payload):
        if method=='GET' and '/retarget/dispatch?' in path and pending:
            incoming=pending.pop(0)
            # A genuine emitted intermediate fixture pose differs from intent.
            published=[round(value) for value in incoming['action9'][:6]];published[5]=85
            pair.arm._publish_command(published,80,
                {'command_generation':pair.mux.generation,'input_received_at':pair.arm.stream_intent['received_at']})
        result=native_request(method,path,payload)
        if method=='POST' and path.endswith('/cooperative/input'):pending.append(deepcopy(payload))
        return result
    pair.giver=routed
    def predict(reference,*,dispatch=False,command):
        assert dispatch is False and reference==sources.spec['subskills']['giver'];predictions.append(deepcopy(command))
        return {'action_contract':ROLE_CONTRACTS['giver'],'model':deepcopy(pair.owner.policy),
            'action9':[90.]*5+[80.,.003,-.001,.002],'source_sequence':pair.mux.sequence+1,
            'source_observation':routed('GET','/legacy/explorer/cooperative/observation',None)}
    service.native_controls._ready_prediction=lambda row,spec,role:predict
    return predictions


def start_release(service,row):
    request=service.workflow.request_step(row['session_id'],'release_requested',expected_epoch=row['epoch'])
    deadline=time.monotonic()+2
    while time.monotonic()<deadline:
        current=service.workflow.snapshot(row['session_id']);pending=current.get('pending_operation') or {}
        if pending.get('native_release_challenge') and pending.get('native_control_checkpoint'):return request,pending['native_release_challenge']
        if current['status']=='STOPPED':raise AssertionError(current['errors'])
        time.sleep(.005)
    raise AssertionError('Actual source release bundle/checkpoint did not become available')


def test_root_control_then_existing_policy_dispatch_returns_actual_original_opening_receipt(tmp_path,monkeypatch):
    sources=Sources(tmp_path,monkeypatch,policy=True);service=sources.service
    try:
        predictions=original_policy_fixture(sources);row=release_ready(sources);before=len(sources.pair.owner.original_publisher.messages)
        request,descriptor=start_release(service,row)
        assert not predictions and not service.transports
        controlled=service.command('native_parked_release_resume',body(row,descriptor),authenticated_actor=ACTOR)['result']
        service.workflow.threads[request['operation_id']].join(3)
        current=service.workflow.snapshot(row['session_id'])
        assert current['status']=='READY_FOR_OBSERVED_STEPS' and current['protocol_state']=='RELEASE',current['errors']
        assert len(predictions)==1 and len(current['dispatch_receipts'])==1
        actual=current['dispatch_receipts'][0];native=actual['source']['native_receipt'];opening=native['original_payload']['cooperative_publication_boundary']
        assert actual['dispatched'] and actual['source']['basis']=='actual_original_native_host_publication'
        assert opening['opening'] and opening['guard_grant']==controlled['completion']['release_grant']
        assert len(sources.pair.owner.original_publisher.messages)==before+1
        assert opening['requested_servo_deg']==[90.]*5+[85.] and not actual['source']['attainment_verified']
        source=native['source_intent_binding']['source']
        assert source['raw_policy_action9']==[90.]*5+[80.,.003,-.001,.002]
        assert source['coordinator_action_projection']['requested_action9']==[90.]*5+[80.,0.,0.,0.]
        assert service._giver_opening(current)==opening
        posts=[path for _,method,path,_ in sources.pair.calls if method=='POST']
        assert not any(path.endswith('/guard/authorize') or path.endswith('/lease') for path in posts)
        assert current['protocol_completed'] is False and sources.wrapper.helper.armed and sources.pair.owner.guard.armed
    finally:sources.close()
