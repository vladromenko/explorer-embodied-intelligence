"""Small source-only fixtures: no model, renderer, optimizer or physical IO."""
from copy import deepcopy
import io
import json

import numpy as np
import pytest
from PIL import Image

from explorer_shared.contracts import ACTION_NAMES,canonical,digest
from explorer_shared.learning.sim_object_route import (PHASES,LOW,HIGH,ObjectRouteRejected,
    split_cases,target_split,expert_admission,complete_object_score,canonical_teacher,
    ChunkDispatcher,stationary_endpoint,VisualObjectChunks,learned_skill_card,sha)


def plan():
    return {'scenarios':{partition:[dict(id=partition+'-candy',seed=74101+i*1000,
        object_offset_xy_m=[i*.002,0.],goal_offset_xy_m=[0.,i*.003],object_yaw_rad=i*.01)]
        for i,partition in enumerate(('train','development','sealed_test'))}}


def fixture():
    case=split_cases(plan())[0];boot='real-simulation-boot';rows=[];obs=[];images={}
    image=io.BytesIO();Image.fromarray(np.full((12,16,3),107,np.uint8)).save(image,format='PNG')
    prior=np.array([90,90,90,90,90,30,0,0,0.]).tolist()
    for index,phase in enumerate(PHASES):
        stamp=dict(boot_id=boot,clock_domain=boot,monotonic_ns=index*10000000)
        after=dict(stamp,monotonic_ns=(index+1)*10000000)
        action=prior.copy();action[0]+=.1
        delivery=dict(receipt_id='dispatch-'+str(index),physical_io=False,finite_dynamics_step_completed=True,
            dispatched_action9=action,pre_action_stamp=stamp,dispatch_stamp=after)
        name='rgb/'+str(index)+'.png';images[name]=image.getvalue()
        renderer=dict(stamp=stamp,camera_id='actual-camera',frame_id='frame-'+str(index),rendered_from_target_physics=True)
        rows.append(dict(index=index,phase_id=phase,actual_dispatched_action9=action,preceding_command9=prior,
            dispatch=delivery,renderer_receipt=renderer,target_rgb_file=name,target_rgb_sha256=sha(images[name]),learned_target_actor_executed=False))
        obs.append(dict(preceding_command9=prior,observation_stamp=stamp,joint_feedback6_rad=[0.]*6,joint_feedback_servo6=[90,90,90,90,90,30]))
        prior=action
    report=dict(source='target_finite_physics_evaluator',independent_of_action_commands=True,end_to_end_success=True,
        terminal_goal_verified=True,per_phase={p:dict(success=True) for p in PHASES})
    result=dict(target_split=target_split(case),full_target_simulation_success=True,failure=None,source_method='SCRIPTED_SIM',
        learned_target_actor_executed=False,independent_evaluation=report,optimizer_partition_allowed=True,
        frames=len(rows),target_trace_sha256=digest(rows))
    return case,result,rows,obs,images,dict(full_object_success=True)


def test_split_rejects_renamed_same_geometry_and_historical_seed():
    protocol=plan();protocol['scenarios']['development'][0].update({k:deepcopy(v) for k,v in protocol['scenarios']['train'][0].items() if k not in ('id','seed')})
    with pytest.raises(ObjectRouteRejected,match='Actual geometry overlaps'):split_cases(protocol)
    protocol=plan();protocol['scenarios']['train'][0]['seed']=7321
    with pytest.raises(ObjectRouteRejected,match='Fresh disjoint'):split_cases(protocol)


def test_failed_phase_and_heldout_are_retained_never_optimizer_experts():
    case,result,*_,score=fixture();result['independent_evaluation']['per_phase']['release']['success']=False
    assert not expert_admission(result,case,score)['eligible_for_optimizer']
    case=split_cases(plan())[1];result['target_split']=target_split(case);result['independent_evaluation']['per_phase']['release']['success']=True
    assert expert_admission(result,case,score)['complete_verified_teacher']
    assert not expert_admission(result,case,score)['eligible_for_optimizer']
    result['target_split']['partition']='unknown'
    with pytest.raises(ObjectRouteRejected,match='independently frozen'):expert_admission(result,case,score)


def test_full_terminal_score_rejects_latched_success_closed_measured_gripper_or_outside():
    final=dict(free_of_fingers=True,inside_all_points=True,bottom_supported=True,within_rim=True,settled=True,support_force_N=.1)
    report=dict(source='target_finite_physics_evaluator',independent_of_action_commands=True,
        object_facts=dict(grasped=True,lifted=True,carried_above_target=True,success=True,withdrawn=True,final=final))
    assert complete_object_score(report,[90,90,90,90,90,30])['full_object_success']
    assert not complete_object_score(report,[90,90,90,90,90,170])['full_object_success']
    final['inside_all_points']=False
    assert not complete_object_score(report,[90,90,90,90,90,30])['full_object_success']


def test_canonical_teacher_keeps_raw_sensor_dispatch_and_actual_available_masks():
    case,result,rows,obs,images,score=fixture()
    envelope,payloads,report,admission=canonical_teacher(result,rows,obs,images,case,'skill',3,score)
    assert report['admitted_for_dataset'] and report['eligible_as_successful_bc_expert'] and admission['eligible_for_optimizer']
    sample=envelope['samples'][0]
    assert sample['action']['values'][6:]==[0,0,0] and sample['action']['mask']==[True]*9
    assert sample['source_dispatch']==rows[0]['dispatch'] and sample['source_observation']==obs[0]
    assert sample['channels']['joint_feedback']['mask'] is True
    assert sample['channels']['lidar']['value'] is None and sample['channels']['lidar']['mask'] is False
    assert envelope['model_input']['channels']==['camera_rgb','preceding_command9']
    assert envelope['project_id']=='06' and envelope['experiment_numbering_version']==2
    assert payloads['rgb/0.png']==images['rgb/0.png']


@pytest.mark.parametrize('mutation',['rgb','prior','dispatch'])
def test_canonical_teacher_rejects_actual_byte_or_causal_action_tampering(mutation):
    case,result,rows,obs,images,score=fixture()
    if mutation=='rgb':images['rgb/0.png']+=b'changed'
    elif mutation=='prior':obs[1]['preceding_command9']=list(obs[0]['preceding_command9']);rows[1]['preceding_command9']=obs[1]['preceding_command9'];result['target_trace_sha256']=digest(rows)
    else:rows[0]['dispatch']['dispatched_action9']=[0.]*9;result['target_trace_sha256']=digest(rows)
    with pytest.raises(ObjectRouteRejected):canonical_teacher(result,rows,obs,images,case,'skill',3,score)


def test_chunk8_predicts_once_uses_ordered_own_predictions_and_resets_not_teacher():
    calls=[]
    def predict(image,state):
        calls.append((image.copy(),state.copy()));return np.repeat(np.linspace(-.5,.5,16)[:,None],9,axis=1)
    dispatcher=ChunkDispatcher(predict,execute_steps=8,image_size=8)
    rgb=np.full((12,16,3),89,np.uint8);prior=(LOW+HIGH)/2
    for i in range(9):
        actual,meta=dispatcher.next(rgb,prior,dict(boot_id='sim',clock_domain='sim',monotonic_ns=i))
        expected=LOW+(np.linspace(-.5,.5,16)[i%8]+1)*(HIGH-LOW)/2
        np.testing.assert_allclose(actual,expected)
        assert meta['new_neural_prediction']==(i%8==0)
    assert len(calls)==2 and calls[0][0].shape==(8,8,3)
    np.testing.assert_allclose(calls[0][1],np.zeros(9))
    with pytest.raises(ObjectRouteRejected,match='duplicate/reversed'):dispatcher.next(rgb,prior,dict(boot_id='sim',clock_domain='sim',monotonic_ns=8))
    dispatcher.reset();dispatcher.next(rgb,prior,dict(boot_id='new',clock_domain='new',monotonic_ns=0));assert len(calls)==3


def test_stationary_constraint_retains_all_raw9_and_valid_neutral_channels():
    raw=[190,90,90,90,90,30,.1,-.1,.2]
    actual,receipt=stationary_endpoint(raw,verified_named_base_fixture=True)
    assert actual.tolist()==[180,90,90,90,90,30,0,0,0]
    assert receipt['raw_predicted_action9']==raw and receipt['channel_masks']==[True]*9
    with pytest.raises(ObjectRouteRejected,match='actual named base weld'):stationary_endpoint(raw,verified_named_base_fixture=False)


def test_dataset_rejects_holdout_and_duplicate_optimizer_episodes_before_loading(tmp_path):
    case=split_cases(plan())[0];descriptor=dict(id=case['id'],geometry_signature=case['geometry_signature'],split='development',admission={'eligible_for_optimizer':True})
    manifest=dict(schema='explorer_fresh_target_visual_dataset_v1',action_names=list(ACTION_NAMES),split_plan=plan(),episodes=[descriptor])
    path=tmp_path/'manifest.json';path.write_bytes(canonical(manifest))
    with pytest.raises(ObjectRouteRejected,match='Development/sealed'):VisualObjectChunks(path,16,64)


def test_actual_train_view_reloads_causal_pixels_prior9_and_future_chunk_padding(tmp_path):
    case,result,rows,obs,images,score=fixture();envelope,payloads,report,admission=canonical_teacher(result,rows,obs,images,case,'skill',3,score)
    # Explicitly sample-exact fixture rather than implementation-mirroring mocks:
    # each future action differs, so a stale/current-label substitution is seen.
    arrays=dict(rgb=np.full((9,8,8,3),107,np.uint8),state_native=np.asarray([o['preceding_command9'] for o in obs],np.float32),
        action_native=np.asarray([r['actual_dispatched_action9'] for r in rows],np.float32),frame_monotonic_ns=np.arange(9,dtype=np.int64))
    with (tmp_path/'view.npz').open('wb') as stream:np.savez_compressed(stream,**arrays)
    (tmp_path/'envelope.json').write_bytes(canonical(envelope));(tmp_path/'admission.json').write_bytes(canonical(report))
    descriptor=dict(id=case['id'],geometry_signature=case['geometry_signature'],split='train',admission=admission,file='view.npz',sha256=sha((tmp_path/'view.npz').read_bytes()),
        canonical_envelope_file='envelope.json',canonical_envelope_sha256=digest(envelope),canonical_admission_file='admission.json',canonical_admission_sha256=digest(report))
    manifest=dict(schema='explorer_fresh_target_visual_dataset_v1',action_names=list(ACTION_NAMES),split_plan=plan(),episodes=[descriptor],source_files_sha256={'view.npz':descriptor['sha256']})
    path=tmp_path/'manifest.json';path.write_bytes(canonical(manifest));dataset=VisualObjectChunks(path,4,8)
    # Loading source arrays itself is Torch-lazy. Training access uses the frozen
    # shared preprocessing exactly once and actual future labels in order.
    item=dataset[7]
    assert item['action_is_pad'].tolist()==[False,False,True,True]
    np.testing.assert_allclose(item['action'][0],(arrays['action_native'][7]-LOW)*2/(HIGH-LOW)-1)
    assert dataset.resume_context['source_files'][-1]==dict(path=str(path),sha256=sha(path.read_bytes()))
    manifest['episodes'].append(descriptor);path.write_bytes(canonical(manifest))
    with pytest.raises(ObjectRouteRejected,match='Unique source episode'):VisualObjectChunks(path,4,8)
    manifest['episodes']=[descriptor];path.write_bytes(canonical(manifest));(tmp_path/'view.npz').write_bytes(b'tampered')
    with pytest.raises(ObjectRouteRejected,match='SHA/path differs'):VisualObjectChunks(path,4,8)


def test_skill_card_retains_all_paired_negatives_and_rejects_checkpoint_cherry_picking(tmp_path):
    from explorer.store import Store
    protocol=dict(plan(),strategies=[dict(id='chunk1'),dict(id='chunk8')],skill_id='skill',source_skill_version=1,source_template_sha256='a'*64,source_commit='f'*40,settings={'image_size':64})
    training=dict(integration_only=False,heldout_samples_optimized=0,checkpoint_sha256='b'*64)
    results=[dict(case=case,strategy=strategy,checkpoint_sha256='b'*64,paired_policy_freeze_sha256='c'*64,teacher_query_count=0,privileged_actor_input=False,outcome='FAILURE') for case in split_cases(protocol) if case['partition']!='train' for strategy in ('chunk1','chunk8')]
    def card(rows):return learned_skill_card(protocol,training,rows,dataset_sha256='d'*64,protocol_sha256='e'*64,paired_freeze_sha256='c'*64)
    result=card(results);assert result['evaluation']['outcomes']['sealed_test']['chunk8']['FAILURE']==1
    assert not result['evaluation']['whole_skill_mastered'] and result['source_type']=='simulation'
    store=Store(tmp_path/'state');store.add_skill(result);assert len(store.skills())==1
    with pytest.raises(ObjectRouteRejected,match='retained exactly once'):card(results[:-1])
    changed=deepcopy(results);changed[0]['checkpoint_sha256']='0'*64
    with pytest.raises(ObjectRouteRejected,match='Same frozen'):card(changed)
