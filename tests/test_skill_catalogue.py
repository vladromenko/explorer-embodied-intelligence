"""Catalogue tests use pure proposals and temporary event databases, never robots."""
from copy import deepcopy
import json
from pathlib import Path
import shutil
import subprocess

import pytest

from explorer.skill_catalogue import (
    PICK_PHASES,SO101_CONTRACT,TEMPLATES,catalogue,confirm_proposal,create_proposal,
    demonstration_plan,list_proposals,propose_skill,readiness,revise_proposal,
    validate_subskill_references,
)
from explorer.skill_workflow import proposal as mobile_proposal
from explorer.store import Store
from explorer_shared.contracts import digest
from explorer_shared.so101_station import NAMES,UNITS
from explorer_shared.experiments import experiment_id
SOURCE_ID=experiment_id('so101_source_learning')


def test_every_06_template_has_real_six_coordinate_station_names_and_distinct_owner():
    metadata=catalogue()
    assert metadata['owner_experiment']==SOURCE_ID and metadata['route']=='/experiments/'+SOURCE_ID
    assert metadata['legacy_route']=='/learning/so101'
    for template in TEMPLATES:
        spec=propose_skill(template_id=template)
        assert spec['status']=='PROPOSED' and spec['owner_experiment']==SOURCE_ID
        assert spec['action_contract']['id']==SO101_CONTRACT
        assert spec['action_contract']['names']==list(NAMES) and spec['action_contract']['units']==list(UNITS)
        assert spec['action_contract']['dimension']==6 and len(spec['robots'])==2
        assert not spec['constraints']['physical_motion_authorized']
        assert spec['constraints']['separate_user_observed_arm_test_required']
        assert spec['demo_plan']['episode_scopes']==['whole_episode','targeted_subskill','recovery']
        assert not spec['execution_method']['implemented_by_catalogue']
    stack=propose_skill(template_id='stack_if_geometry_validated')
    assert any(row['id']=='stable_stack_geometry' for row in stack['missing_facts'])


def test_russian_candy_proposal_exposes_task_owner_roles_queries_camera_phases_and_ACT_semantics():
    spec=propose_skill('взять конфетку и положить в коробку')
    assert spec['template_id']=='pick_object_to_container' and spec['recognized_type']=='pick_object_to_container'
    assert spec['object']['query']=='конфетку' and spec['goal']['query']=='коробку'
    assert spec['owner_experiment']==SOURCE_ID and spec['source']['query']=='Стол'
    assert [row['id'] for row in spec['stages']]==list(PICK_PHASES)
    assert spec['sensors']['primary_camera']['role']=='single_fixed_table_camera'
    assert spec['sensors']['primary_camera']['hardware_identity']=='UNKNOWN'
    assert spec['language_semantics']['text_role']=='editable_SkillSpec_metadata_not_ACT_language_input'
    assert not spec['language_semantics']['ACT_arbitrary_instruction_conditioning']
    assert spec['language_semantics']['per_skill_version_dataset_and_model']
    assert 'not_certain_NLU' in spec['proposal_basis']


def test_give_cookie_from_rosmaster_routes_to_05_composite_without_merged_actions_or_datasets():
    spec=propose_skill('дать мне печенье, которое привёз ROSMASTER')
    assert spec['owner_experiment']=='05' and spec['recognized_type']=='composite_delivery_and_handover'
    assert spec['object']['query']=='печенье' and spec['source']['query']=='ROSMASTER'
    assert 'мне' in spec['recipient']['query']
    assert spec['action_contract'] is None
    assert not spec['composite']['datasets_may_merge'] and not spec['composite']['concatenate_action_spaces']
    assert not spec['composite']['timed_release_allowed'] and not spec['composite']['VLM_is_contact_measurement']
    assert spec['composite']['release_guard']=='source_bound_independent_person_grasp_evidence'
    assert {row['owner_experiment'] for row in spec['subskill_references']}=={'02',SOURCE_ID}
    mobile=propose_skill('ROSMASTER перенеси носок в корзину')
    assert mobile['owner_experiment']=='02' and mobile['action_contract']['dimension']==9
    assert mobile['action_contract']['neutral_channels_retained'] and len(mobile['robots'])==1


def test_custom_uncertainty_stable_retry_linear_versions_and_immutable_ownership(tmp_path):
    store=Store(tmp_path);first=create_proposal(store,dict(text='Сделай красиво',request_id='r1'))
    assert create_proposal(store,dict(text='Сделай красиво',request_id='r1'))==first
    with pytest.raises(ValueError,match='identity changed'):create_proposal(store,dict(template_id='pick_object_to_container',request_id='r1'))
    identity=first['spec']['skill_id']
    with pytest.raises(ValueError,match='Resolve'):confirm_proposal(store,identity,1,first['spec_sha256'])
    revised=revise_proposal(store,identity,1,dict(template_id='pick_object_to_container',object=dict(query='Конфетка'),goal=dict(query='Коробка')),first['spec_sha256'])
    assert revised['spec']['skill_id']==identity and revised['spec']['version']==2 and not revised['confirmed']
    assert confirm_proposal(store,identity,2,revised['spec_sha256'])['confirmed']
    assert len(list_proposals(Store(tmp_path)))==2
    with pytest.raises(ValueError,match='refresh'):revise_proposal(store,identity,1,dict(title='stale'),first['spec_sha256'])
    for patch in (dict(owner_experiment='02'),dict(action_contract=dict(dimension=9)),dict(constraints=dict(physical_motion_authorized=True))):
        with pytest.raises(ValueError):revise_proposal(store,identity,2,patch,revised['spec_sha256'])
    with pytest.raises(ValueError,match='SO101 source phase IDs'):revise_proposal(store,identity,2,dict(phase_ids=['release_after_grasp']),revised['spec_sha256'])
    with pytest.raises(ValueError,match='full-task template phases'):revise_proposal(store,identity,2,dict(phase_ids=['observe_select']),revised['spec_sha256'])


def test_composite_confirm_pins_exact_confirmed_child_versions_and_contracts(tmp_path):
    store=Store(tmp_path);parent=create_proposal(store,dict(text='дать мне печенье, которое привёз ROSMASTER'))
    child06=create_proposal(store,dict(template_id='pick_object_to_container'))
    child06=confirm_proposal(store,child06['spec']['skill_id'],1,child06['spec_sha256'])
    child02=dict(spec=mobile_proposal(preset='sock_to_basket'),confirmed=True)
    refs=[dict(role='deliver_to_handoff',owner_experiment='02',skill_id=child02['spec']['skill_id'],version=1,spec_sha256=digest(child02['spec'])),
        dict(role='receive_and_offer',owner_experiment=SOURCE_ID,skill_id=child06['spec']['skill_id'],version=1,spec_sha256=child06['spec_sha256'])]
    revised=revise_proposal(store,parent['spec']['skill_id'],1,dict(subskill_references=refs),parent['spec_sha256'])
    assert not validate_subskill_references(revised['spec'],[child02,child06])
    with pytest.raises(ValueError,match='unresolved'):confirm_proposal(store,revised['spec']['skill_id'],2,revised['spec_sha256'])
    assert confirm_proposal(store,revised['spec']['skill_id'],2,revised['spec_sha256'],known_specs=[child02,child06])['confirmed']
    stale=deepcopy(revised['spec']);stale['subskill_references'][1]['version']=2
    assert validate_subskill_references(stale,[child02,child06])
    swapped=deepcopy(revised['spec']);swapped['subskill_references'][0]['owner_experiment']=SOURCE_ID
    assert validate_subskill_references(swapped,[child02,child06])


def receipts(spec):
    base=dict(skill_id=spec['skill_id'],skill_version=spec['version'],owner_experiment=SOURCE_ID,action_contract_id=SO101_CONTRACT,source_kind='simulation')
    return dict(demos=dict(base,aggregate_manifest_sha256='a'*64,full_teaching_provenance_ready=True,object_expert_eligible=True,global_split_groups_verified=True),
        trained=dict(base,trained=True,framework='LeRobot',framework_version='0.6.0',checkpoint_sha256='b'*64,aggregate_manifest_sha256='a'*64),
        shadow=dict(base,checkpoint_sha256='b'*64,execution_kind='fresh_observation_learned_policy_rollout',shadow=True,hardware_commands_dispatched=False),
        physical=dict(base,checkpoint_sha256='b'*64,execution_kind='fresh_observation_learned_policy_rollout',separate_user_observed_test=True,independent_full_task_success=True,
            coordinate_contract_verified=True,native_executor_qualified=True,existing_controller_settings_preserved=True))


def test_readiness_never_turns_user_booleans_template_or_simulation_into_physical_qualification():
    spec=propose_skill(template_id='pick_object_to_container');declared=receipts(spec)
    result=readiness(spec,confirmed=True,receipts=declared)
    assert result['template']['ready'] and not any(result[k]['ready'] for k in ('demos','trained','shadow','physical'))
    result=readiness(spec,confirmed=True,receipts=declared,receipt_verifier=lambda *args:True)
    assert all(result[k]['ready'] for k in ('demos','trained','shadow'))
    assert not result['physical']['ready'] and not result['physical_execution_authorized']
    assert result['trained']['source_kind']=='simulation'
    assert 'separate_observed_physical_full_task_test_required' in result['physical']['reasons']
    declared['physical']['source_kind']='physical'
    assert readiness(spec,confirmed=True,receipts=declared,receipt_verifier=lambda *args:True)['physical']['ready']
    assert not readiness(spec,confirmed=True,receipts=declared,receipt_verifier=lambda *args:True)['physical_execution_authorized']


def test_raw_replay_wrong_checkpoint_and_missing_official_training_cannot_masquerade_as_rollout():
    spec=propose_skill(template_id='pick_object_to_container');declared=receipts(spec)
    declared['shadow']['execution_kind']='raw_episode_replay'
    result=readiness(spec,confirmed=True,receipts=declared,receipt_verifier=lambda *args:True)
    assert result['trained']['ready'] and not result['shadow']['ready']
    assert 'raw_episode_replay_or_open_loop_is_not_policy_rollout' in result['shadow']['reasons']
    declared=receipts(spec);declared['trained']['framework_version']='0.5.0'
    result=readiness(spec,confirmed=True,receipts=declared,receipt_verifier=lambda *args:True)
    assert not result['trained']['ready'] and not result['shadow']['ready']
    declared=receipts(spec);declared['shadow']['checkpoint_sha256']='c'*64
    assert not readiness(spec,confirmed=True,receipts=declared,receipt_verifier=lambda *args:True)['shadow']['ready']


def report(spec,name,variant,split,*,scope='whole_episode',outcome='success',targets=None):
    phases=targets or [row['id'] for row in spec['stages']]
    return dict(original_episode_id=name,skill_id=spec['skill_id'],skill_version=1,variant_id=variant,split=split,episode_scope=scope,
        phase_outcomes={phase:outcome for phase in phases},target_phase_ids=phases,source_quality_usable=True,operator_success=outcome=='success',independent_full_task_success=outcome=='success')


def test_adaptive_phase_variant_progress_preserves_failed_unknown_targeted_and_duplicate_episodes():
    spec=propose_skill(template_id='pick_object_to_container')
    initial=demonstration_plan(spec)
    assert initial['next_action']['show_number']==1 and initial['next_action']['initial_technical_target']==5
    failed=report(spec,'failed','variant-001','train',outcome='failure')
    unknown=report(spec,'unknown','variant-001','train',outcome='unknown')
    targeted=report(spec,'targeted','variant-001','train',scope='targeted_subskill',targets=['grasp'])
    state=demonstration_plan(spec,[failed,unknown,targeted,targeted],receipt_verifier=lambda *args:True)
    assert state['covered_variants']==0 and state['phase_variant_coverage']['grasp']['variant-001']==1
    assert state['next_action']['kind']=='repeat_variant' and state['next_action']['repeat_number']==4
    assert len(state['rejected'])==3 and not state['expert_targets_generated_from_failures']
    complete=[report(spec,'episode-'+str(i),variant['variant_id'],variant['split']) for i,variant in enumerate(spec['demo_plan']['variants'])]
    state=demonstration_plan(spec,complete,receipt_verifier=lambda *args:True)
    assert state['covered_variants']==5 and state['next_action']['kind']=='independent_model_evaluation'
    assert not state['skill_mastery_claimed'] and state['sealed_episode_ids']==['episode-4']
    assert demonstration_plan(spec,complete)['covered_variants']==0
    with pytest.raises(ValueError,match='never merge'):demonstration_plan(propose_skill('дать мне печенье, которое привёз ROSMASTER'))


def test_standalone_panel_renders_actual_owner_queries_and_separate_readiness_without_IO(tmp_path):
    node=shutil.which('node')
    if node is None:pytest.skip('Node optional renderer regression')
    spec=propose_skill('дать мне печенье, которое привёз ROSMASTER');spec['object']['query']='<unsafe-object>'
    view=dict(catalogue=catalogue(),selected=dict(spec=spec,spec_sha256=digest(spec),confirmed=False),readiness=readiness(spec))
    fixture=tmp_path/'view.json';fixture.write_text(json.dumps(view))
    script=Path(__file__).parents[1]/'00-control-center/src/explorer/static/skill-catalogue.js'
    runner=tmp_path/'render.cjs';runner.write_text(r'''
const fs=require('fs'),vm=require('vm'),assert=require('assert');
const context={};vm.createContext(context);
vm.runInContext(fs.readFileSync(require('path').join(require('path').dirname(process.argv[3]),'experiment-registry.js'),'utf8'),context);
vm.runInContext(fs.readFileSync(process.argv[3],'utf8'),context);
context.view=JSON.parse(fs.readFileSync(process.argv[2],'utf8'));
const html=vm.runInContext('skillCataloguePanel(view)',context);
assert(html.includes('/experiments/05'));assert(html.includes('&lt;unsafe-object&gt;'));assert(!html.includes('<unsafe-object>'));
assert(html.includes('SO101_follower'));assert(html.includes('reference:deliver_to_handoff'));assert(html.includes('reference:receive_and_offer'));
assert(html.includes('language input ACT'));assert(html.includes('Raw replay'));assert(/data-catalogue-action="confirm" disabled/.test(html));
assert(html.includes('Независимый физический навык'));assert(!html.includes('data-job-kind'));assert(!html.includes('data-native'));
''')
    result=subprocess.run([node,str(runner),str(fixture),str(script)],capture_output=True,text=True)
    assert result.returncode==0,result.stdout+result.stderr
