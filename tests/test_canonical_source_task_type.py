"""Both consumers must read canonical04 task identity without rewriting its Spec."""
from copy import deepcopy
import hashlib
from pathlib import Path
import pytest
from explorer import transfer_source as source
from explorer import so101_source_evaluation as evaluation
from test_transfer_source import fixture


def canonical(spec):
    spec['recognized_type']=spec.pop('task_type')
    spec['phase_ids']=['observe_select','approach','grasp','lift_verify','transport','place_release','retreat']
    spec['stages']=[dict(id=name) for name in spec['phase_ids']]
    return spec


def test_canonical_source_object_type_is_read_by_catalogue_and_evaluation_without_false_hold_or_mastery(tmp_path):
    store,job,admission,outer,_=fixture(tmp_path,spec_transform=canonical)
    original=Path(job['config']['aggregate_manifest']);before=original.read_bytes()
    choice=source.inspect_source_model(store,job['id'],admission_verifier=admission,owner_capabilities={'implemented':True})
    assert choice['technical_source_ready'] and choice['task_type']=='pick_object_to_container'
    assert 'C_OBJECT_SKILL_SOURCE_REQUIRED' not in {r['code'] for r in choice['blockers']}
    assert 'C_NO_QUALIFIED_OBJECT_TRACE' in {r['code'] for r in choice['blockers']} and not choice['eligible_for_transfer']
    settings=evaluation.request_settings(store,job['id'],dict(duration_s=.5,object_instance_id='SOFTWARE-object',scene_id='SOFTWARE-scene',goal_instance_id='SOFTWARE-bin'),admission_verifier=admission)
    assert settings['source_phases']==outer['skill_spec']['phase_ids'] and settings['phase_reference']['lift_verify']=='object' and settings['phase_reference']['place_release']=='goal'
    assert settings['hardware_permission_is_not_granted_by_this_request'] and store.jobs()==[job]
    assert original.read_bytes()==before


@pytest.mark.parametrize('kind',['hold','trajectory','joint_trajectory'])
def test_canonical_non_object_tasks_still_fail_object_evaluation_before_control(tmp_path,kind):
    def nonobject(spec):spec=canonical(spec);spec['recognized_type']=kind;return spec
    store,job,admission,outer,_=fixture(tmp_path,spec_transform=nonobject)
    result=source.inspect_source_model(store,job['id'],admission_verifier=admission,owner_capabilities={'implemented':True})
    assert 'C_OBJECT_SKILL_SOURCE_REQUIRED' in {r['code'] for r in result['blockers']}
    with pytest.raises(evaluation.EvaluationBlocked):evaluation.request_settings(store,job['id'],{},admission_verifier=admission)


def test_conflicting_fields_are_not_silently_resolved_to_an_object_skill(tmp_path):
    def contradictory(spec):spec['recognized_type']='hold';return spec
    store,job,admission,outer,_=fixture(tmp_path,spec_transform=contradictory)
    choice=source.inspect_source_model(store,job['id'],admission_verifier=admission,owner_capabilities={'implemented':True})
    assert not choice['technical_source_ready'] and not choice['eligible_for_transfer']
    assert choice['blockers'][0]['code']=='A_SOURCE_MODEL_OR_ADMISSION_NOT_VERIFIED'
    with pytest.raises(ValueError,match='Conflicting'):evaluation.request_settings(store,job['id'],{},admission_verifier=admission)
