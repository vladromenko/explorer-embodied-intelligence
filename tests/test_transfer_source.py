"""Structural SOFTWARE fixtures only; no optimizer, policy weights, device or execution."""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import uuid

import numpy as np
from PIL import Image
import pytest

from explorer.store import Store
from explorer import transfer_source as source
from explorer_shared.contracts import digest
from explorer_shared.so101_station import NAMES,UNITS,LOW,HIGH
from explorer_shared.source_archive import canonical,sha


def fixture(tmp_path,backend='act',source_kind='SIMULATION'):
    store=Store(tmp_path/'state');skill_id=str(uuid.uuid4());phases=['approach','grasp','lift','transport','place_release','retreat']
    spec=dict(skill_id=skill_id,version=1,title='SOFTWARE object fixture',task_type='pick_object_to_container',stages=[dict(id=p) for p in phases])
    calibration=dict(calibration_metadata=dict(follower=dict(calibration_sha256=sha(b'SOFTWARE_calibration'))))
    outer=dict(skill_id=skill_id,skill_version=1,skill_spec=spec,spec_sha256=digest(spec),action_contract_id=source.CONTRACT,
        action_names=list(NAMES),action_units=list(UNITS),source_kind=source_kind,source_calibration_metadata=calibration)
    data=store.root/'datasets'/'source';data.mkdir(parents=True)
    manifest=data/'skill.json';manifest.write_bytes(canonical(outer))
    view=dict(source_records=[dict(source_evidence=dict(camera_ids=['SOFTWARE-camera']))],episodes=[
        dict(id='train-view',source_episode_id='train-original',split='train'),
        dict(id='dev-view',source_episode_id='dev-original',split='development')])
    view_path=data/'view.json';view_path.write_bytes(canonical(view))
    config=dict(backend=backend,aggregate_manifest=str(manifest),aggregate_manifest_sha256=sha(manifest.read_bytes()),
        skill_id=skill_id,skill_version=1,spec_sha256=digest(spec))
    job=store.create_job('so101_skill_train','04',config)
    folder=store.root/'jobs'/job['id'];checkpoint=folder/('student_act' if backend=='act' else 'student_diffusion');checkpoint.mkdir(parents=True)
    saved=dict(type=backend,device='cpu',input_features={'observation.state':dict(shape=[6]),'observation.images.overview':dict(shape=[3,64,64])},output_features={'action':dict(shape=[6])},n_action_steps=1,temporal_ensemble_coeff=None,normalization_mapping={'VISUAL':'IDENTITY','STATE':'IDENTITY','ACTION':'IDENTITY'})
    pre=dict(action_contract_id=source.CONTRACT,action_names=list(NAMES),action_units=list(UNITS),state_low=list(LOW),state_high=list(HIGH),state_basis='measured_state',official_robot_use_degrees=False)
    if backend=='act':
        pre.update(state_transform='2*(state-low)/(high-low)-1',action_transform='same fixed endpoint normalization',
            rgb_layout='NCHW',rgb_scale='uint8 / 255',mean=[.485,.456,.406],std=[.229,.224,.225],
            temporal_ensembling=False,chunk_execution_steps=1,source_camera_ids=['SOFTWARE-camera'],source_calibration_metadata=calibration)
        for name in ('policy_preprocessor.json','policy_postprocessor.json'):(checkpoint/name).write_bytes(canonical(dict(steps=[],software_fixture_only=True)))
    else:
        from explorer_shared.learning.diffusion_backend import PROFILE_ID,compact_profile
        saved.update({key:list(value) if isinstance(value,tuple) else value for key,value in compact_profile()['configuration'].items()})
        pre.update(profile_id=PROFILE_ID,history_steps=2,chunk_prediction_steps=4,image_size=64,RGB_transform='uint8/255_NCHW_bilinear_resize_once')
    (checkpoint/'config.json').write_bytes(canonical(saved));(checkpoint/'explorer_preprocessing.json').write_bytes(canonical(pre))
    (checkpoint/'model.safetensors').write_bytes(b'SOFTWARE STRUCTURAL FIXTURE; NOT AN ACTUALLY TRAINED MODEL')
    hashes={p.name:sha(p.read_bytes()) for p in checkpoint.iterdir()}
    card=dict(id='source-fixture-'+job['id'],task='software fixture',source_type=source_kind,source_kind=source_kind,action_contract=dict(id=source.CONTRACT,names=list(NAMES),units=list(UNITS),state_basis='measured_state',official_robot_use_degrees=False),
        lineage=dict(job_id=job['id'],skill_id=skill_id,skill_version=1,spec_sha256=digest(spec),dataset_manifest_sha256=config['aggregate_manifest_sha256'],checkpoint_sha256=digest(hashes)),evaluation={'physical_run_ids':[]},status='software_fixture_only',limitations=['NOT_A_REAL_TRAINED_SOURCE'])
    result=dict(skill_id=skill_id,skill_version=1,spec_sha256=digest(spec),action_contract_id=source.CONTRACT,source_kind=source_kind,
        backend=backend,aggregate_manifest_sha256=config['aggregate_manifest_sha256'],source_manifest_sha256=config['aggregate_manifest_sha256'],
        trained=True,weights_changed=True,save_reload_passed=True,optimizer_steps=8,sealed_payload_used=False,framework='LeRobot',framework_version='0.6.1',
        reload_max_abs_error=0.,official_dataset_readback=dict(official_readback_passed=True),
        official_saved_processors_executed=True,saved_processor_reload_max_abs_error=0.,
        checkpoint_dir=str(checkpoint),checkpoint_sha256=hashes,checkpoint_content_sha256=digest(hashes),skill_card=card,
        evaluation=dict(rollout_success_claimed=False),software_fixture_only=True)
    if backend=='diffusion':result['profile_id']=pre['profile_id']
    (folder/'result.json').write_bytes(canonical(result));store.add_skill(card)
    store.update_job(job['id'],status='completed',result=result)
    admission=lambda store,config:(manifest,outer,view_path,view)
    return store,store.job(job['id']),admission,outer,view


def inspect(store,job,admission,**kwargs):
    return source.inspect_source_model(store,job['id'],admission_verifier=admission,**kwargs)


def test_controlplane_import_has_no_torch_robot_or_camera_factory():
    code="import sys;import explorer.transfer_source;assert 'torch' not in sys.modules;assert not any(n.startswith('lerobot.robots') for n in sys.modules)"
    root=Path(__file__).resolve().parents[1]
    environment=dict(os.environ,PYTHONPATH=os.pathsep.join(str(root/name) for name in ('00-control-center/src','shared')))
    run=subprocess.run([sys.executable,'-c',code],capture_output=True,text=True,timeout=15,env=environment)
    assert run.returncode==0,run.stderr


@pytest.mark.parametrize('backend',['act','diffusion'])
def test_current_native_models_visible_but_training_loss_never_qualifies_object_source(tmp_path,backend):
    store,job,admission,outer,view=fixture(tmp_path,backend)
    value=inspect(store,job,admission,owner_capabilities={'implemented':False})
    assert value['technical_source_ready'] and not value['eligible_for_transfer']
    assert value['backend']==backend and value['history_steps']==(1 if backend=='act' else 2)
    assert {r['code'] for r in value['blockers']}=={'A_SOURCE_EVALUATION_OWNER_NOT_IMPLEMENTED','C_NO_QUALIFIED_OBJECT_TRACE'}
    assert value['source_domain']=='SIMULATION' and not value['physical_execution_authorized']


def test_current_catalogue_lists_native_train_kind_and_keeps_legacy_analysis_separate(tmp_path,monkeypatch):
    store,job,admission,*_=fixture(tmp_path)
    legacy=store.create_job('train_act','04',{})
    store.update_job(legacy['id'],status='completed',result={'feature_dimension':6,'trained':True})
    monkeypatch.setattr(source,'_admission',admission)
    choices=source.current_source_choices(store)
    assert choices['choices'][0]['job_id']==job['id']
    assert choices['legacy_analysis_sources'][0]['job_id']==legacy['id']
    assert not choices['legacy_analysis_sources'][0]['eligible_for_transfer']
    assert choices['source_action_dimension']==6 and choices['target_action_dimension']==9
    assert not choices['joint_head_copy_allowed']


def test_checkpoint_extra_tamper_missing_processors_and_changed_reviews_have_typed_blockers(tmp_path):
    store,job,admission,*_=fixture(tmp_path)
    checkpoint=Path(job['result']['checkpoint_dir']);model=checkpoint/'model.safetensors';raw=model.read_bytes()
    model.write_bytes(raw+b'changed')
    assert not inspect(store,job,admission)['technical_source_ready']
    model.write_bytes(raw);(checkpoint/'unbound-extra.json').write_text('{}')
    assert not inspect(store,job,admission)['technical_source_ready']
    (checkpoint/'unbound-extra.json').unlink();(checkpoint/'policy_preprocessor.json').unlink()
    assert not inspect(store,job,admission)['technical_source_ready']
    def changed(*args):raise ValueError('Source review cohort changed')
    value=inspect(store,job,changed)
    assert 'Source review cohort changed' in value['blockers'][0]['detail']


def test_probe_selects_only_actual_unsealed_episode_and_does_not_qualify_source(tmp_path):
    store,job,admission,*_=fixture(tmp_path)
    binding=source.prepare_source_probe(store,job['id'],'dev-view',admission_verifier=admission)
    assert binding['source_episode']['split']=='development'
    assert not binding['qualifies_object_source'] and not binding['physical_execution_authorized']
    with pytest.raises(ValueError,match='unsealed'):
        source.prepare_source_probe(store,job['id'],'sealed-original',admission_verifier=admission)


def observations(images,calibration):
    return [dict(camera_id='SOFTWARE-camera',frame_id='rgb-'+str(i),frame_sha256=sha(b'encoded-'+bytes([i])),decoded_rgb_sha256=sha(image.tobytes()),
        follower_calibration_sha256=calibration,state_basis='measured_state',frame_clock_domain='native-source',state_clock_domain='native-source',source_cutoff_clock_domain='native-source',
        frame_boot_id='boot-one',state_boot_id='boot-one',source_cutoff_boot_id='boot-one',frame_monotonic_ns=100+i*100,
        state_monotonic_ns=110+i*100,source_cutoff_monotonic_ns=120+i*100,source_cutoff_basis='original_native_dispatch_time_for_offline_evaluation') for i,image in enumerate(images)]


@pytest.mark.parametrize('backend',['act','diffusion'])
def test_offline_probe_validates_actual_backend_history_causality_masks_before_fake_predictor(tmp_path,backend):
    store,job,admission,*_=fixture(tmp_path,backend)
    binding=source.prepare_source_probe(store,job['id'],'dev-view',admission_verifier=admission)
    class Fake:
        def predict_history(self,images,states,**kwargs):
            return np.tile(np.asarray([0.,0.,0.,0.,0.,50.]),(binding['prediction_steps'],1))
    probe=source.SourcePolicyProbe(binding,camera_id='SOFTWARE-camera',loader=lambda *args:Fake())
    n=binding['history_steps'];images=np.zeros((n,2,2,3),np.uint8);states=np.zeros((n,6),np.float32);masks=np.ones((n,6),bool)
    facts=observations(images,binding['source_calibration_sha256'])
    result=probe.predict_history(images,states,valid_mask=masks,observations=facts)
    assert len(result['predicted_native6'])==binding['prediction_steps']
    assert not result['source_or_target_commands_dispatched'] and not result['qualifies_object_source']
    facts[0]['frame_monotonic_ns']=facts[0]['source_cutoff_monotonic_ns']+1
    with pytest.raises(ValueError,match='noncausal'):
        probe.predict_history(images,states,valid_mask=masks,observations=facts)
    with pytest.raises(ValueError,match='history'):
        probe.predict_history(np.zeros((3,2,2,3),np.uint8),np.zeros((3,6)),valid_mask=np.ones((3,6),bool),observations=[])


def qualified_fixture(store,job,outer,*,source_partition='development'):
    phases=[row['id'] for row in outer['skill_spec']['stages']]
    model=job['result'];evaluation=store.create_job('so101_skill_eval','04',{'source_model_job_id':job['id']})
    folder=store.root/'jobs'/evaluation['id']/'source-bundle';folder.mkdir(parents=True)
    inventory={}
    def save(name,raw):
        path=folder/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(raw);inventory[name]=sha(raw)
    for name in model['checkpoint_sha256']:save('checkpoint/'+name,(Path(model['checkpoint_dir'])/name).read_bytes())
    frames=[];steps=[]
    for i,phase in enumerate(phases):
        rgb=folder/('rgb/'+str(i)+'.png');rgb.parent.mkdir(exist_ok=True)
        Image.new('RGB',(2,2),(i*20,100,120)).save(rgb);inventory[rgb.relative_to(folder).as_posix()]=sha(rgb.read_bytes())
        command=[0.,0.,0.,0.,0.,50.];reference='goal' if i>=3 else 'object'
        frame=dict(native_action6=command,phase_id=phase,reference=reference,source_kind='QUALIFIED_SOURCE_MODEL',source_rgb_file=rgb.relative_to(folder).as_posix())
        frames.append(frame)
        steps.append(dict(index=i,phase_id=phase,reference=reference,actual_dispatched_native6=list(command),
            prediction_kind='fresh_observation_learned_policy_inference',checkpoint_content_sha256=model['checkpoint_content_sha256'],
            camera_id='SOFTWARE-camera',frame_id='fixture-'+str(i),frame_sha256=inventory[frame['source_rgb_file']],model_input_frame_ids=['fixture-'+str(i)],
            native_dispatch_receipt_id='dispatch-'+str(i),observation_stamp=dict(clock_domain='source',boot_id='boot',monotonic_ns=i*100+10),
            dispatch_stamp=dict(clock_domain='source',boot_id='boot',monotonic_ns=i*100+20)))
    receipt=dict(schema=source.EVALUATION_SCHEMA,source_model_job_id=job['id'],checkpoint_content_sha256=model['checkpoint_content_sha256'],
        source_domain='SIMULATION',execution_kind='fresh_observation_learned_policy_rollout',independent_full_task_success=True,
        per_phase={phase:dict(status='success',independent_of_action_commands=True) for phase in phases},steps=steps,
        software_fixture_only=True,actual_execution_claimed_by_this_test=False)
    save('evaluation-receipt.json',canonical(receipt))
    trace=dict(schema='explorer_skill_transfer_source_trace_v1',experiment_slug='so101_source_learning',source_kind='QUALIFIED_SOURCE_MODEL',
        task_type='pick_object_to_container',skill_id=outer['skill_id'],skill_version=outer['skill_version'],action_contract_id=source.CONTRACT,action_names=list(NAMES),
        split=dict(partition=source_partition,episode_id='new-source-policy-attempt',session_id='new-policy-session',scene_id='same-scene-new-reset',group_id='new-policy-group'),
        qualification=dict(independent_closed_loop_object_success=True,source_model_sha256=model['checkpoint_sha256']['model.safetensors'],
            source_checkpoint_content_sha256=model['checkpoint_content_sha256'],processor_sha256=model['checkpoint_sha256']['explorer_preprocessing.json'],
            evaluation_receipt_sha256=inventory['evaluation-receipt.json'],evaluation_run_ids=[evaluation['id']],raw_episode_replay_only=False),
        source_files_sha256=inventory,frames=frames)
    path=folder/'source-trace.json';path.write_bytes(canonical(trace))
    result=dict(schema=source.EVALUATION_SCHEMA,source_model_job_id=job['id'],execution_kind='fresh_observation_learned_policy_rollout',independent_full_task_success=True,
        raw_episode_replay_only=False,source_domain='SIMULATION',skill_id=outer['skill_id'],skill_version=outer['skill_version'],spec_sha256=outer['spec_sha256'],
        aggregate_manifest_sha256=model['aggregate_manifest_sha256'],checkpoint_content_sha256=model['checkpoint_content_sha256'],
        source_calibration_sha256=outer['source_calibration_metadata']['calibration_metadata']['follower']['calibration_sha256'],camera_id='SOFTWARE-camera',
        source_trace_manifest=str(path),source_trace_sha256=sha(path.read_bytes()),software_fixture_only=True)
    store.update_job(evaluation['id'],status='completed',result=result)
    return store.job(evaluation['id']),path,receipt,trace


def test_qualified_trace_is_separate_exact_model_source_and_runtime_owner_receipt(tmp_path):
    store,job,admission,outer,*_=fixture(tmp_path)
    evaluation,path,receipt,trace=qualified_fixture(store,job,outer)
    before=path.read_bytes()
    blocked=inspect(store,job,admission,owner_capabilities={'implemented':False})
    assert blocked['source_object_rollout_qualified'] and not blocked['eligible_for_transfer']
    assert any(r['code']=='A_SOURCE_EVALUATION_OWNER_NOT_IMPLEMENTED' for r in blocked['blockers'])
    structural=inspect(store,job,admission,owner_capabilities={'implemented':True,'software_fixture_only':True})
    assert structural['eligible_for_transfer'] and structural['qualified_trace_choices'][0]['evaluation_job_id']==evaluation['id']
    assert path.read_bytes()==before and not structural['physical_execution_authorized']


def test_sealed_qualified_trace_replay_and_foreign_model_cannot_enter_transfer(tmp_path):
    store,job,admission,outer,*_=fixture(tmp_path)
    evaluation,path,receipt,trace=qualified_fixture(store,job,outer,source_partition='sealed_test')
    value=inspect(store,job,admission,owner_capabilities={'implemented':True})
    assert not value['eligible_for_transfer'] and not value['qualified_trace_choices']
    assert any(row['code']=='C_SOURCE_EVALUATION_NOT_QUALIFIED' for row in value['blockers'])


def test_default_admission_does_not_accept_self_attested_current_model(tmp_path):
    store,job,admission,*_=fixture(tmp_path)
    value=source.inspect_source_model(store,job['id'])
    assert not value['technical_source_ready'] and not value['eligible_for_transfer']
    assert value['blockers'][0]['code']=='A_SOURCE_MODEL_OR_ADMISSION_NOT_VERIFIED'


def rebind_structural_evaluation(store,evaluation,path,receipt,trace):
    """Explicit TEMP SQL corruption; production terminal jobs remain immutable."""
    raw=canonical(receipt);(path.parent/'evaluation-receipt.json').write_bytes(raw)
    trace['source_files_sha256']['evaluation-receipt.json']=sha(raw)
    trace['qualification']['evaluation_receipt_sha256']=sha(raw)
    path.write_bytes(canonical(trace))
    result=deepcopy(evaluation['result']);result['source_trace_sha256']=sha(path.read_bytes())
    with store.connect() as db:db.execute('UPDATE jobs SET result=? WHERE id=?',(json.dumps(result),evaluation['id']))


@pytest.mark.parametrize('mutation',['failed_phase','future_observation','false_native_action','wrong_frame_sha'])
def test_rebound_self_consistent_hashes_do_not_hide_wrong_source_execution_facts(tmp_path,mutation):
    store,job,admission,outer,*_=fixture(tmp_path)
    evaluation,path,receipt,trace=qualified_fixture(store,job,outer)
    if mutation=='failed_phase':receipt['per_phase']['grasp']['status']='failure'
    if mutation=='future_observation':receipt['steps'][0]['observation_stamp']['monotonic_ns']=999999
    if mutation=='false_native_action':receipt['steps'][0]['actual_dispatched_native6'][0]=33.
    if mutation=='wrong_frame_sha':receipt['steps'][0]['frame_sha256']='0'*64
    rebind_structural_evaluation(store,evaluation,path,receipt,trace)
    selected=inspect(store,job,admission,owner_capabilities={'implemented':True})
    assert not selected['eligible_for_transfer'] and not selected['qualified_trace_choices']


def test_failed_historical_evaluation_does_not_poison_separate_qualified_attempt(tmp_path):
    store,job,admission,outer,*_=fixture(tmp_path)
    failed=store.create_job('so101_skill_eval','04',{'source_model_job_id':job['id']})
    store.update_job(failed['id'],status='failed',result={'independent_full_task_success':False})
    evaluation,path,receipt,trace=qualified_fixture(store,job,outer)
    chosen=inspect(store,job,admission,owner_capabilities={'implemented':True})
    assert chosen['eligible_for_transfer'] and len(chosen['evaluation_attempts'])==2
    assert sum(row['qualified'] for row in chosen['evaluation_attempts'])==1
    binding=source.prepare_qualified_source(store,job['id'],evaluation['id'],expected_source_trace_sha256=sha(path.read_bytes()),
        admission_verifier=admission,owner_capabilities={'implemented':True})
    assert binding['skill_id']==outer['skill_id'] and binding['minimum_new_target_skill_version']==2
    assert binding['source_experiment']=='04' and binding['target_experiment']=='06'
    assert not binding['direct_action_head_transfer_allowed'] and not binding['physical_execution_authorized']
    with pytest.raises(ValueError,match='exact current'):
        source.prepare_qualified_source(store,job['id'],evaluation['id'],expected_source_trace_sha256='0'*64,
            admission_verifier=admission,owner_capabilities={'implemented':True})


@pytest.mark.parametrize('mutation',['rgb_slot','normalization','foreign_card_skill','nonfinite_reload'])
def test_self_consistent_hash_rebinding_does_not_qualify_wrong_source_profile_or_card(tmp_path,mutation):
    store,job,admission,*_=fixture(tmp_path)
    result=deepcopy(job['result']);checkpoint=Path(result['checkpoint_dir']);card=result['skill_card']
    if mutation in ('rgb_slot','normalization'):
        path=checkpoint/'config.json';body=json.loads(path.read_bytes())
        if mutation=='rgb_slot':body['input_features']['observation.images.overview']['shape']=[1,64,64]
        else:body['normalization_mapping']['STATE']='MEAN_STD'
        path.write_bytes(canonical(body))
        result['checkpoint_sha256']={path.name:sha(path.read_bytes()) for path in checkpoint.iterdir()}
        result['checkpoint_content_sha256']=digest(result['checkpoint_sha256'])
        card['lineage']['checkpoint_sha256']=result['checkpoint_content_sha256']
    if mutation=='foreign_card_skill':card['lineage']['skill_id']=str(uuid.uuid4())
    if mutation=='nonfinite_reload':result['reload_max_abs_error']=float('nan')
    # Explicit test corruption bypasses immutable terminal APIs and rebinds all
    # byte hashes, so failure must be semantic rather than merely stale SHA.
    raw=json.dumps(result).encode();(store.root/'jobs'/job['id']/'result.json').write_bytes(raw)
    with store.connect() as db:
        db.execute('UPDATE jobs SET result=? WHERE id=?',(raw.decode(),job['id']))
        db.execute('UPDATE skills SET body=? WHERE id=?',(json.dumps(card),card['id']))
    selected=inspect(store,store.job(job['id']),admission,owner_capabilities={'implemented':True})
    assert not selected['technical_source_ready'] and not selected['eligible_for_transfer']
