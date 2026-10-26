"""Real native mocked publication ABI; retained policy residue is never hidden."""
from copy import deepcopy
import uuid

import pytest

from explorer_shared.cooperative_action_constraint05 import stationary_constraint,verify_constraint,project_stationary_action
from explorer_shared.cooperative_contract05 import skill_spec,ROLE_CONTRACTS,digest
from test_native_cooperative05 import fixture,start
from test_cooperative_transport05 import setup
from fastapi import FastAPI
from fastapi.testclient import TestClient
from explorer_shared.native_stream03_api import mount_native_stream_api


def constraint_for(binding,model):
    refs={role:{'experiment_slug':'rosmaster_keyboard_learning' if role=='giver' else 'so101_source_learning',
        'skill_id':str(uuid.uuid4()),'skill_version':1,'spec_sha256':model['spec_sha256'] if role=='giver' else 'c'*64,
        'action_contract':deepcopy(ROLE_CONTRACTS[role]),'execution_scope':'whole_skill'} for role in ('giver','receiver')}
    refs['giver'].update(model_id=model['model_id'],checkpoint_sha256=model['checkpoint_sha256'])
    spec=skill_spec({'name':'stationary observed handover','object_description':'actual block',
        'receiver_goal_description':'put into box','outcome_criteria':['independent full placement'],
        'mode':'giver_policy_receiver_manual','subskills':refs})
    session={'session_id':binding['run_id'],'epoch':binding['coordinator_epoch'],'protocol_object_id':binding['object_id'],
        'spec_sha256':spec['spec_sha256'],'stationary_chassis_confirmed':True,'giver_input_mode':'policy'}
    return stationary_constraint(spec,session,model)


def test_actual_retained_nonzero_policy_base_is_projected_only_by_explicit_source_bound_constraint():
    source,mux,teleop,arm,publisher,tap,activation,now,binding=fixture()
    model={'model_id':'actual-source-model','checkpoint_sha256':'a'*64,'spec_sha256':'b'*64}
    constraint=constraint_for(binding,model);registration=source.register_constraint({**binding,'constraint':constraint})
    assert not publisher.messages and not teleop.native_drive and not arm.calls
    epoch,model=start(source,mux,binding);lease=teleop.lease;observation=source.observation()
    raw=[91]*6+[0.3275205791,-0.2608493567,0.3130290806]  # actual retained finite-CPU residue, not measured base velocity
    issued,projection=project_stationary_action(raw,constraint)
    body={**binding,'control_epoch':epoch,'source_sequence':1,'command_id':'projected-command',
        'model':model,'action9':issued,'raw_policy_action9':raw,'coordinator_action_projection':projection,'source_observation':observation}
    assert source.input(body)['accepted'] and teleop.lease==lease and teleop.native_drive[-1]==[0.,0.,0.]
    assert projection['valid_mask']==[True]*9 and len(projection['raw_policy_action9'])==9
    record={'command_generation':mux.generation,'input_received_at':arm.stream_intent['received_at']}
    arm._publish_command([91]*6,80,record)
    actual=tap.since()['receipts'][-1]['source_intent_binding']['source']
    assert actual['raw_policy_action9']==raw and actual['raw_policy_action9_sha256']==digest(raw)
    assert actual['requested_target_action9']==issued and actual['source_target_sha256']==digest(issued)
    assert actual['coordinator_action_projection']==projection and actual['native_constraint_registration']==registration
    assert not actual['measured_feedback_claimed'] and arm.motion_config=={'streaming':{'lookahead_s':.08}}


def test_no_implicit_projection_or_source_session_model_or_receipt_substitution():
    source,mux,teleop,arm,publisher,tap,activation,now,binding=fixture()
    model={'model_id':'actual-source-model','checkpoint_sha256':'a'*64,'spec_sha256':'b'*64};constraint=constraint_for(binding,model)
    wrong=deepcopy(constraint);wrong['source_session']['stationary_chassis_confirmed']=False
    with pytest.raises(ValueError):source.register_constraint({**binding,'constraint':wrong})
    with pytest.raises(ValueError,match='another cooperative'):source.register_constraint({**binding,'run_id':'other','constraint':constraint})
    assert not publisher.messages and not teleop.native_drive
    source.register_constraint({**binding,'constraint':constraint});epoch,model=start(source,mux,binding)
    raw=[91.]*6+[.1,.2,.3];issued,projection=project_stationary_action(raw,constraint)
    body={**binding,'control_epoch':epoch,'source_sequence':1,'command_id':'not-issued','model':model,
        'source_observation':source.observation(),'action9':issued,'raw_policy_action9':raw,'coordinator_action_projection':projection}
    for replacement in ({'raw_policy_action9':[91.]*6+[0.,0.,0.]},{'coordinator_action_projection':None},
                        {'action9':[91.]*6+[.1,0.,0.]},{'authorization_id':'other'}):
        with pytest.raises(ValueError):source.input({**body,**replacement})
    assert not arm.calls and mux.sequence==-1 and teleop.native_drive==[[0.,0.,0.]]
    with pytest.raises(ValueError,match='STOP before explicit begin'):source.register_constraint({**binding,'constraint':constraint})


def test_genuine_all_nine_zeros_are_retained_without_unavailable_masks_or_arm_only_contract():
    model={'model_id':'m','checkpoint_sha256':'a'*64,'spec_sha256':'b'*64}
    constraint=constraint_for({'run_id':'run','coordinator_epoch':'epoch','object_id':'block'},model)
    raw=[0.]*9;issued,receipt=project_stationary_action(raw,constraint)
    assert issued==raw and receipt['raw_policy_action9']==raw and receipt['raw_base_was_neutral']
    assert receipt['action_contract']['dimension']==9 and receipt['valid_mask']==[True]*9
    assert not receipt['unavailable_values_replaced'] and not constraint['physical_stationarity_claimed']
    with pytest.raises(ValueError):project_stationary_action([0.]*8,constraint)
    with pytest.raises(ValueError):project_stationary_action([0.]*8+[float('nan')],constraint)


def test_transport_requires_actual_registered_raw_and_projected_publication_receipt():
    transport,command,source,mux,teleop,arm,publisher,tap,calls,binding=setup(constraint_factory=constraint_for)
    constraint=source.action_constraint['constraint']
    previous=transport.predict
    def predict(command):
        result=previous(command);result['action9'][6:]=[.3275205791,-.2608493567,.3130290806];return result
    transport.predict=predict
    actual=transport.submit_observed(command)
    receipt=actual.source['native_receipt']['source_intent_binding']['source']
    assert actual.dispatched and receipt['raw_policy_action9'][6:]==[.3275205791,-.2608493567,.3130290806]
    assert receipt['requested_target_action9'][6:]==[0.,0.,0.] and teleop.native_drive[-1]==[0.,0.,0.]
    assert receipt['coordinator_action_projection']['constraint_sha256']==constraint['constraint_sha256']


def test_authenticated_registration_is_metadata_only_default_runtime_has_no_registered_constraint():
    source,mux,teleop,arm,publisher,tap,activation,now,binding=fixture()
    constraint=constraint_for(binding,{'model_id':'actual-source-model','checkpoint_sha256':'a'*64,'spec_sha256':'b'*64})
    app=FastAPI();mount_native_stream_api(app,mux,token='constraint-test-only-private',observed_test_permit=activation.allowed,
        activation=activation,cooperative=source,reference_reader=source.reference)
    with TestClient(app) as client:
        path='/api/explorer/cooperative/constraint/register'
        assert client.post(path,json={**binding,'constraint':constraint}).status_code==401
        headers={'Authorization':'Bearer constraint-test-only-private'}
        assert client.get('/api/explorer/cooperative/status',headers=headers).json()['registered_action_constraint'] is None
        reply=client.post(path,json={**binding,'constraint':constraint},headers=headers)
        assert reply.status_code==200 and reply.json()['constraint']==constraint and not reply.json()['native_command_sent']
    assert publisher.messages==arm.calls==teleop.native_drive==[] and not mux.active


def test_correlated_publication_with_altered_raw_prediction_digest_is_not_dispatch_qualification():
    transport,command,source,mux,teleop,arm,publisher,tap,calls,binding=setup(constraint_factory=constraint_for)
    previous=transport.request
    def altered(method,path,body):
        result=previous(method,path,body)
        if '/dispatch?' in path:
            for receipt in result.get('receipts',[]):
                incoming=(receipt.get('source_intent_binding') or {}).get('source') or {}
                if incoming.get('command_id')==command['command_id']:incoming['raw_policy_action9_sha256']='0'*64
        return result
    transport.request=altered
    with pytest.raises(ValueError,match='raw9/constraint projection provenance'):transport.submit_observed(command)
    assert len(publisher.messages)==2  # genuine fake publication occurred; altered evidence was still rejected.
