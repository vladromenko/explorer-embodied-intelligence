"""Simple user buttons reuse existing metadata/recorder contracts, no devices."""
import uuid
import pytest
from explorer.store import Store
from explorer.operator_context import prepare
from explorer.skill_workflow import prepare_capture_request


@pytest.mark.parametrize('project',['02','03','04'])
def test_simple_recording_context_is_accepted_by_existing_workflow(tmp_path,project):
    store=Store(tmp_path/'state')
    result=prepare(store,dict(project_id=project,task='Мой показ',request_id=str(uuid.uuid4())))
    assert result['project_id']==project and result['skill']['spec']['title']=='Мой показ'
    assert result['skill']['confirmed'] and result['phases']
    if project=='04':
        from explorer.source_demo_workflow import prepare as source_prepare
        # Source prepare is metadata only; same native SourceDemo contract.
        body=result['context']['record_start_body']
        row=source_prepare(store,dict(skill_id=body['skill_id'],skill_version=body['skill_version'],request_id=uuid.uuid4().hex,context=dict(variant_id=body['context']['variant_id'],session_id=body['session_id'],object_instance_id=body['context']['object_instance_id'])),actor='LOCAL_FIXTURE')
        assert row['binding']['skill_id']==body['skill_id']
        assert body['project_id']=='04' and body['task']=='Мой показ'
    else:
        intent,native=prepare_capture_request(store,result['context'],project_id=project)
        assert native['project_id']==project and native['action_contract_id']=='rosmaster_joint9_position6_velocity3_v1'
        assert not intent['stationary_chassis_confirmed']


@pytest.mark.parametrize('project',['02','03','04'])
def test_next_demo_uses_same_skill_and_new_original_session(tmp_path,project):
    store=Store(tmp_path/'state')
    first=prepare(store,dict(project_id=project,task='Навык',request_id=str(uuid.uuid4())))
    second=prepare(store,dict(project_id=project,task='Навык',request_id=str(uuid.uuid4()),skill_id=first['skill']['spec']['skill_id'],skill_version=first['skill']['spec']['version']))
    assert first['skill']['spec']['skill_id']==second['skill']['spec']['skill_id']
    a=first['context'].get('record_start_body',first['context']);b=second['context'].get('record_start_body',second['context'])
    assert a['session_id']!=b['session_id']
