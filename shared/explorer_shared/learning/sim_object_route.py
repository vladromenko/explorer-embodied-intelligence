"""ENGINEERING_GLUE / ACT_LEROBOT_0_6_1: fresh target visual-object route.

Actor inputs are only actual target RGB and literal preceding command9. Teacher
truth/phase labels stay outside that boundary. No devices, rollout generation,
optimizer, archived Python or deployment executes at module import.
"""
from __future__ import annotations
from collections import deque
from copy import deepcopy
import hashlib
import io
import json
import math
from pathlib import Path

import numpy as np
from PIL import Image

from explorer_shared.contracts import ACTION_NAMES,ACTION_UNITS,canonical,digest
from explorer_shared.embodied_episode import (SCHEMA,ACTION_CONTRACT_ID,COMMAND_ESTIMATE_PROFILE,
    COMMAND_ESTIMATE_REQUIRED,observation_channels,inspect_embodied_episode)
from explorer_shared.experiments import experiment_identity

LOW=np.array([0.,0.,0.,0.,0.,30.,-.2,-.2,-.6],np.float32)
HIGH=np.array([180.,180.,180.,180.,270.,170.,.2,.2,.6],np.float32)
PHASES=('approach','descend','close','lift','carry','align_release','release','withdraw','settle')
HISTORICAL_SEEDS={7321,7331,7332,7333,7334,7401,18421,18422}


class ObjectRouteRejected(ValueError):pass


def sha(raw):return hashlib.sha256(raw).hexdigest()


def geometry_signature(case):
    fields={key:case[key] for key in ('object_offset_xy_m','goal_offset_xy_m','object_yaw_rad')}
    if any(not isinstance(fields[name],list) or len(fields[name])!=2 or any(type(x) not in (int,float) or not math.isfinite(x) or abs(x)>.05 for x in fields[name]) for name in ('object_offset_xy_m','goal_offset_xy_m')):
        raise ObjectRouteRejected('Explicit bounded object/goal geometry required')
    if type(fields['object_yaw_rad']) not in (int,float) or not math.isfinite(fields['object_yaw_rad']) or abs(fields['object_yaw_rad'])>.5:
        raise ObjectRouteRejected('Explicit bounded object orientation required')
    return digest(fields)


def split_cases(protocol):
    rows=[];ids=set();seeds=set();groups={}
    for partition in ('train','development','sealed_test'):
        cases=protocol.get('scenarios',{}).get(partition)
        if not isinstance(cases,list) or not cases:raise ObjectRouteRejected('Explicit train/development/sealed scenario lists required')
        for case in cases:
            identity=case.get('id');seed=case.get('seed');group=geometry_signature(case)
            if not isinstance(identity,str) or not identity or identity in ids or '/' in identity or identity.startswith(('v9-','v10-')):
                raise ObjectRouteRejected('Fresh unique target episode identity required')
            if type(seed) is not int or seed in HISTORICAL_SEEDS or seed in seeds:raise ObjectRouteRejected('Fresh disjoint target seed required')
            if group in groups and groups[group]!=partition:raise ObjectRouteRejected('Actual geometry overlaps target partitions; relabelling IDs is insufficient')
            groups[group]=partition;ids.add(identity);seeds.add(seed)
            rows.append(dict(case,partition=partition,geometry_signature=group,
                scene_id='geometry-'+group,session_id='independent_sim_reset-'+identity,
                object_instance_id='spawned_candy-'+identity,
                object_geometry_generalization=False,shared_object_asset_across_all_splits=True))
    return rows


def target_split(case):
    return dict(partition=case['partition'],episode_id=case['id'],scene_id=case['scene_id'],
        session_id=case['session_id'],group_id=case['geometry_signature'])


def complete_object_score(independent_report,measured_servo6):
    """No action-command success proxy: measured terminal gripper is separate."""
    facts=independent_report.get('object_facts') or {};last=facts.get('final') or {}
    measured=np.asarray(measured_servo6,float)
    if measured.shape!=(6,) or not np.isfinite(measured).all():raise ObjectRouteRejected('Actual measured SIM joint-coordinate6 required for terminal evaluation')
    conditions=dict(grasp=bool(facts.get('grasped')),lift=bool(facts.get('lifted')),
        carry=bool(facts.get('carried_above_target')),placement_dwell=bool(facts.get('success')),
        released=last.get('free_of_fingers') is True,terminal_inside=last.get('inside_all_points') is True,
        terminal_supported=last.get('bottom_supported') is True,terminal_within_rim=last.get('within_rim') is True,
        settled=last.get('settled') is True,measured_gripper_open=bool(measured[5]<=50.),retreated=facts.get('withdrawn') is True)
    independent=independent_report.get('source')=='target_finite_physics_evaluator' and independent_report.get('independent_of_action_commands') is True
    support=float(last.get('support_force_N',0.))
    return dict(full_object_success=bool(independent and all(conditions.values()) and math.isfinite(support) and support>1e-4),
        conditions=conditions,measured_terminal_servo6=measured.tolist(),support_force_N=support,
        observer='independent_native_contact_substeps_and_measured_terminal_joints',physical_io=False,
        task_scope='whole_candy_grasp_lift_carry_release_settle_retreat_in_stationary_unified_ROSMASTER_SIM_fixture')


def expert_admission(result,case,terminal_score):
    explicit=result.get('target_split') or {}
    if explicit!=target_split(case):raise ObjectRouteRejected('Actual target split must match the independently frozen scenario')
    report=result.get('independent_evaluation') or {};phases=report.get('per_phase') or {}
    complete=(result.get('full_target_simulation_success') is True and result.get('failure') is None and
        result.get('source_method')=='SCRIPTED_SIM' and result.get('learned_target_actor_executed') is False and
        report.get('independent_of_action_commands') is True and report.get('end_to_end_success') is True and
        report.get('terminal_goal_verified') is True and set(phases)==set(PHASES) and
        all(phases[name].get('success') is True for name in PHASES) and terminal_score.get('full_object_success') is True)
    train=case['partition']=='train'
    return dict(complete_verified_teacher=bool(complete),eligible_for_optimizer=bool(train and complete and result.get('optimizer_partition_allowed') is True),
        partition=case['partition'],source_method='SCRIPTED_SIM_not_learned_source',
        reason=None if train and complete else 'HELD_OUT_OR_INCOMPLETE_TEACHER_RETAINED_ANALYSIS_ONLY')


def actor_observation(rgb,preceding9,image_size):
    """Only these two arguments cross the learner's observation boundary."""
    image=np.asarray(rgb);prior=np.asarray(preceding9,np.float32)
    if image.dtype!=np.uint8 or image.ndim!=3 or image.shape[2]!=3 or prior.shape!=(9,) or not np.isfinite(prior).all():
        raise ObjectRouteRejected('Actual uint8 RGB and explicit finite prior9 required')
    if np.any(prior<LOW) or np.any(prior>HIGH):raise ObjectRouteRejected('Prior command9 outside declared native limits')
    resized=np.asarray(Image.fromarray(image).resize((image_size,image_size),Image.Resampling.BILINEAR)).copy()
    return resized,((prior-LOW)*2/(HIGH-LOW)-1).astype(np.float32)


class ChunkDispatcher:
    """A declared neural action chunk, never source waypoints or teacher fallback."""
    def __init__(self,predict_chunk,*,execute_steps,image_size):
        if type(execute_steps) is not int or execute_steps not in (1,8):raise ObjectRouteRejected('Preregistered chunk1/chunk8 strategies only')
        self.predict_chunk=predict_chunk;self.execute_steps=execute_steps;self.image_size=image_size;self.reset()
    def reset(self):self.pending=deque();self.origin=None;self.last_stamp=None
    def next(self,rgb,preceding9,stamp):
        image,state=actor_observation(rgb,preceding9,self.image_size)
        if (not isinstance(stamp,dict) or not stamp.get('boot_id') or stamp.get('clock_domain')!=stamp['boot_id'] or
                type(stamp.get('monotonic_ns')) is not int or stamp['monotonic_ns']<0):raise ObjectRouteRejected('Actual causal SIM RGB/prior clock required')
        if self.last_stamp is not None and (stamp['boot_id']!=self.last_stamp['boot_id'] or stamp['monotonic_ns']<=self.last_stamp['monotonic_ns']):
            raise ObjectRouteRejected('New episode requires reset; duplicate/reversed observations forbidden')
        predicted=False
        if not self.pending:
            chunk=np.asarray(self.predict_chunk(image,state),np.float64)
            if chunk.ndim!=2 or chunk.shape[1]!=9 or len(chunk)<self.execute_steps or not np.isfinite(chunk).all():
                raise ObjectRouteRejected('Finite neural chunk[K,9] covering declared execution required')
            self.pending.extend(row.copy() for row in chunk[:self.execute_steps]);self.origin=deepcopy(stamp);predicted=True
        normalized=self.pending.popleft();raw=LOW+(normalized+1)*(HIGH-LOW)/2
        self.last_stamp=deepcopy(stamp)
        return raw,dict(strategy='chunk'+str(self.execute_steps),new_neural_prediction=predicted,
            prediction_origin=deepcopy(self.origin),dispatch_observation=deepcopy(stamp),pending_actions=len(self.pending),
            source_waypoints_or_privileged_truth_used=False)


def stationary_endpoint(raw9,*,verified_named_base_fixture):
    raw=np.asarray(raw9,float)
    if raw.shape!=(9,) or not np.isfinite(raw).all():raise ObjectRouteRejected('Finite raw prediction9 required before executor')
    if verified_named_base_fixture is not True:raise ObjectRouteRejected('Stationary projection requires actual named base weld')
    bounded=np.clip(raw,LOW,HIGH);issued=bounded.copy();issued[6:]=0.
    return issued,dict(raw_predicted_action9=raw.tolist(),bounded_prediction9=bounded.tolist(),issued_request9=issued.tolist(),
        joint_limit_clipped=(bounded!=raw).tolist(),chassis_constraint='explicit_immutable_manipulation_base_fixture_zero_velocity',
        raw_chassis_predictions_retained=raw[6:].tolist(),channel_masks=[True]*9,controller_settings_changed=False)


def package_facts(facts,payloads):
    """Retain source facts once and bind canonical fields to exact original bytes."""
    payloads=dict(payloads);payloads['actual-source-facts.json']=canonical(facts);checksum=sha(payloads['actual-source-facts.json'])
    envelope=deepcopy(facts);envelope['source_files_sha256']={name:sha(raw) for name,raw in payloads.items()}
    def bind(record,pointer):record['provenance']=dict(source_file='actual-source-facts.json',sha256=checksum,json_pointer=pointer)
    for key in ('identity','causal_protocol','recording','split','model_input','admission_profile','state_estimate_reference'):bind(envelope[key],'/'+key)
    for name,domain in envelope['clock_domains'].items():bind(domain,'/clock_domains/'+name)
    for index,sample in enumerate(envelope['samples']):
        prefix='/samples/'+str(index);bind(sample['action'],prefix+'/action');bind(sample['action']['delivery'],prefix+'/action/delivery')
        for name,record in sample['channels'].items():bind(record,prefix+'/channels/'+name)
    for index,phase in enumerate(envelope['phases']):bind(phase,'/phases/'+str(index))
    bind(envelope['outcomes']['end_to_end'],'/outcomes/end_to_end')
    for name,outcome in envelope['outcomes']['per_phase'].items():bind(outcome,'/outcomes/per_phase/'+name)
    return envelope,payloads


def canonical_teacher(result,rows,observations,rgb_payloads,case,skill_id,skill_version,terminal_score):
    """SIM collector facts, not invented sensors/human labels or old source RGB."""
    admission=expert_admission(result,case,terminal_score)
    if not rows or len(rows)!=len(observations):raise ObjectRouteRejected('Complete actual target rows/observations required')
    if digest(rows)!=result.get('target_trace_sha256') or result.get('frames')!=len(rows):raise ObjectRouteRejected('Immutable original target trace SHA/count required')
    boot=rows[0]['renderer_receipt']['stamp']['boot_id'];reference='sim-initial-reference-'+boot
    estimator='literal_MuJoCo_command9_log';samples=[];previous=reference;last_tick=-1
    for index,(row,observation) in enumerate(zip(rows,observations)):
        captured=row['renderer_receipt']['stamp'];delivery=row['dispatch'];stamp=delivery['dispatch_stamp'];before=delivery['pre_action_stamp']
        prior=np.asarray(observation['preceding_command9'],float);action=np.asarray(row['actual_dispatched_action9'],float)
        if (row['index']!=index or prior.shape!=(9,) or action.shape!=(9,) or not np.isfinite([prior,action]).all() or
                row.get('learned_target_actor_executed') is not False or
                delivery.get('dispatched_action9')!=row['actual_dispatched_action9'] or delivery.get('physical_io') is not False or
                delivery.get('finite_dynamics_step_completed') is not True or not delivery.get('receipt_id') or
                any(value.get('boot_id')!=boot or value.get('clock_domain')!=boot for value in (captured,stamp,before)) or
                not last_tick<stamp['monotonic_ns'] or not captured['monotonic_ns']<=before['monotonic_ns']<stamp['monotonic_ns'] or
                captured!=observation['observation_stamp'] or row['preceding_command9']!=observation['preceding_command9'] or
                row['renderer_receipt'].get('rendered_from_target_physics') is not True):
            raise ObjectRouteRejected('Actual finite target9 and source-causal RGB/prior/dispatch required')
        if index and not np.array_equal(prior,np.asarray(rows[index-1]['actual_dispatched_action9'])):
            raise ObjectRouteRejected('Literal prior9 must follow actual previous dispatch9')
        name=row['target_rgb_file'];raw=rgb_payloads[name]
        if sha(raw)!=row['target_rgb_sha256']:raise ObjectRouteRejected('Original target RGB SHA differs')
        image=Image.open(io.BytesIO(raw));image.load()
        if image.mode!='RGB':raise ObjectRouteRejected('Actual target RGB image required')
        channels={name:dict(present=False,available=False,value=None,mask=False,source_id='sim-no-produced-'+name,
            unavailable_reason='not_produced_as_an_observation_sensor_in_this_SIM_fixture',stamp=deepcopy(captured)) for name in observation_channels()}
        values=dict(camera_rgb=dict(source_file=name,sha256=sha(raw),camera_id=row['renderer_receipt']['camera_id'],
            frame_id=row['renderer_receipt']['frame_id'],encoding='png',width=image.width,height=image.height),
            clocks=dict(clock_domain=boot,boot_id=boot),joint_command=dict(values=prior[:6].tolist(),units='deg'),
            preceding_command9=dict(values=prior.tolist(),names=list(ACTION_NAMES),state_source='command_estimate',
                reference_id=reference,estimator_id=estimator,last_dispatch_receipt_id=previous),
            goal=dict(goal_id='candy_to_tray',description='Положить конфету в коробку; текст — metadata, не ACT input'))
        if 'joint_feedback_servo6' in observation:
            measured=np.asarray(observation['joint_feedback_servo6'],float)
            if measured.shape!=(6,) or not np.isfinite(measured).all():raise ObjectRouteRejected('Source measured feedback6 differs')
            values['joint_feedback']=dict(values=measured.tolist(),units='deg',state_source='simulated',
                basis='actual_MuJoCo_qpos_converted_by_pinned_SIM_joint_to_servo_not_physical_encoder')
        for name,value in values.items():channels[name]=dict(present=True,available=True,value=value,mask=True,
            source_id='actual-sim-'+name,stamp=deepcopy(captured))
        action_stamp=deepcopy(stamp);receipt=delivery['receipt_id']
        samples.append(dict(sample_id=case['id']+':'+str(index),source_observation=deepcopy(observation),source_dispatch=deepcopy(delivery),action=dict(values=action.tolist(),mask=[True]*9,
            stage='simulated_dispatch',source_id='actual_MuJoCo_native_step',command_id=receipt,stamp=action_stamp,
            delivery=dict(level='simulated_dispatch',command_id=receipt,stamp=action_stamp,observer_kind='simulation_step',receipt_id=receipt)),channels=channels))
        previous=receipt;last_tick=stamp['monotonic_ns']
    phases=[]
    for index,row in enumerate(rows):
        phase=row['phase_id']
        if not phases or phases[-1]['id']!=phase:
            if phase in {item['id'] for item in phases}:raise ObjectRouteRejected('Teacher phases must be contiguous')
            phases.append(dict(id=phase,start_sample=index,end_sample=index,transition_reason='actual_annotated_privileged_teacher_phase',recovery_of=None))
        phases[-1]['end_sample']=index
    independent=result['independent_evaluation'];outcomes={}
    def outcome(success,metrics):return dict(status='success' if success else 'failure',independent_of_action_commands=True,
        method='simulation_oracle',observer_id='actual_FullTaskEvaluator_native_substeps',metrics=deepcopy(metrics))
    for phase in phases:
        actual=independent['per_phase'][phase['id']];outcomes[phase['id']]=outcome(actual.get('success') is True,actual)
    reference_stamp=deepcopy(rows[0]['renderer_receipt']['stamp']);full=admission['complete_verified_teacher']
    facts=dict(schema=SCHEMA,**experiment_identity('cross_embodiment_transfer'),episode_id=case['id'],run_id='fresh-target-train-'+case['id'],
        source_kind='simulation',episode_scope='whole_episode',skill=dict(id=skill_id,version=skill_version,variant_id=case['geometry_signature'],session_id=case['session_id']),
        action_contract=dict(id=ACTION_CONTRACT_ID,names=list(ACTION_NAMES),units=list(ACTION_UNITS),simultaneous=True),
        clock_domains={boot:dict(clock_type='monotonic',boot_id=boot)},causal_protocol=dict(max_observation_age_ns=100000000),
        recording=dict(status='completed',source_quality_usable=True,operator_outcome='success' if full else 'failure',
            outcome_origin='simulation_oracle_not_a_human_operator'),
        admission_profile=dict(id=COMMAND_ESTIMATE_PROFILE,required_channels=list(COMMAND_ESTIMATE_REQUIRED)),
        state_estimate_reference=dict(state_source='command_estimate',reference_id=reference,estimator_id=estimator,
            reference_kind='simulation_initial_state',reference_action9=observations[0]['preceding_command9'],units=list(ACTION_UNITS),
            command_log_contiguous_since_reference=True,reference_receipt_id=reference,stamp=reference_stamp),
        samples=samples,phases=phases,outcomes=dict(end_to_end=outcome(full,terminal_score),per_phase=outcomes),
        split=dict(**target_split(case),object_instance_id=case['object_instance_id'],group_by=['scene_id','session_id','episode_id','object_instance_id']),
        model_input=dict(implementation_id='official_ACT9_RGB_literal_prior9_baseline',channels=['camera_rgb','preceding_command9'],
            action_contract_id=ACTION_CONTRACT_ID,output_names=list(ACTION_NAMES),usage='declared_plan'),
        actual_geometry=dict(geometry_signature=case['geometry_signature'],object_offset_xy_m=case['object_offset_xy_m'],
            goal_offset_xy_m=case['goal_offset_xy_m'],object_yaw_rad=case['object_yaw_rad'],same_candy_asset_generalization=False))
    facts['identity']={key:deepcopy(facts[key]) for key in ('episode_id','run_id','project_id','experiment_numbering_version','experiment_slug','source_kind','skill','episode_scope','action_contract')}
    envelope,payloads=package_facts(facts,rgb_payloads);report=inspect_embodied_episode(envelope,payloads)
    if not report['admitted_for_dataset']:raise ObjectRouteRejected('Canonical target source rejected: '+','.join(report['missing_facts']))
    if report['eligible_as_successful_bc_expert']!=admission['eligible_for_optimizer']:
        raise ObjectRouteRejected('Canonical/executor independent expert eligibility disagrees')
    return envelope,payloads,report,admission


def export_teacher_view(trial_folder,folder,case,skill_id,skill_version,terminal_score,image_size):
    """Source-bound canonical packet plus sample-exact internal RGB/prior9 view."""
    from explorer_shared.source_archive import _write
    trial_folder=Path(trial_folder);folder=Path(folder)
    result=json.loads((trial_folder/'result.json').read_bytes());rows=json.loads((trial_folder/'target-trace.json').read_bytes())
    observations=[json.loads((trial_folder/'observations'/(str(i).zfill(6)+'.json')).read_bytes()) for i in range(len(rows))]
    # The collector saves this actual coordinate conversion alongside raw rad6;
    # it never comes from the future endpoint or object/evaluator state.
    feedback=json.loads((trial_folder.parent/'measured-feedback-view.json').read_bytes())
    if len(feedback)!=len(observations):raise ObjectRouteRejected('Measured feedback view count differs')
    for observation,view in zip(observations,feedback):observation['joint_feedback_servo6']=view
    images={row['target_rgb_file']:(trial_folder/row['target_rgb_file']).read_bytes() for row in rows}
    envelope,payloads,report,admission=canonical_teacher(result,rows,observations,images,case,skill_id,skill_version,terminal_score)
    folder.mkdir(mode=0o700,parents=True,exist_ok=False)
    # Preserve raw teacher provenance beside the admitted view. These are never
    # network inputs: their hashes/bytes permit a later independent audit.
    for name in ('result.json','target-trace.json'):_write(folder/'raw-teacher'/name,(trial_folder/name).read_bytes())
    for name,raw in payloads.items():_write(folder/'canonical'/name,raw)
    _write(folder/'canonical/envelope.json',canonical(envelope));_write(folder/'canonical/admission.json',canonical(report))
    descriptor=dict(id=case['id'],source_episode_id=case['id'],split=case['partition'],seed=case['seed'],geometry_signature=case['geometry_signature'],
        scene_id=case['scene_id'],session_id=case['session_id'],object_instance_id=case['object_instance_id'],
        canonical_envelope_file='canonical/envelope.json',canonical_envelope_sha256=digest(envelope),
        canonical_admission_file='canonical/admission.json',canonical_admission_sha256=digest(report),
        raw_target_result_sha256=sha((trial_folder/'result.json').read_bytes()),raw_target_trace_sha256=result['target_trace_sha256'],
        admission=admission,source_kind='simulation',source_method='SCRIPTED_SIM_verified_target_execution_not_learned_source',
        canonical_sample_correspondence_verified=True,frames=len(rows))
    if admission['eligible_for_optimizer']:
        rgb=[];state=[];actions=[];ticks=[];links=[]
        for index,(row,observation) in enumerate(zip(rows,observations)):
            image=np.asarray(Image.open(io.BytesIO(images[row['target_rgb_file']])))
            resized,_=actor_observation(image,observation['preceding_command9'],image_size)
            rgb.append(resized);state.append(observation['preceding_command9']);actions.append(row['actual_dispatched_action9'])
            ticks.append(row['renderer_receipt']['stamp']['monotonic_ns'])
            links.append(dict(original_sample_index=index,canonical_sample_id=envelope['samples'][index]['sample_id'],
                source_RGB_sha256=row['target_rgb_sha256'],target_action_pointer='/samples/'+str(index)+'/action/values',
                prior9_pointer='/samples/'+str(index)+'/channels/preceding_command9/value/values'))
        path=folder/'RGB-prior9-actions9.npz'
        with path.open('xb') as stream:
            np.savez_compressed(stream,rgb=np.asarray(rgb,np.uint8),state_native=np.asarray(state,np.float32),
                action_native=np.asarray(actions,np.float32),frame_monotonic_ns=np.asarray(ticks,np.int64))
        _write(folder/'sample-links.json',canonical(links))
        descriptor.update(file=path.name,sha256=sha(path.read_bytes()),sample_links_sha256=digest(links),actor_inputs=['actual_target_RGB','literal_preceding_command9'])
    _write(folder/'descriptor.json',canonical(descriptor));return descriptor


class VisualObjectChunks:
    """Bounded small-episode cache; train only and no truth/phase input fields."""
    def __init__(self,manifest_path,chunk_size,image_size):
        from collections import OrderedDict
        self.path=Path(manifest_path).resolve();self.root=self.path.parent;self.manifest=json.loads(self.path.read_bytes())
        if self.manifest.get('schema')!='explorer_fresh_target_visual_dataset_v1' or self.manifest.get('action_names')!=list(ACTION_NAMES):
            raise ObjectRouteRejected('Fresh unified target9 dataset required')
        if type(chunk_size) is not int or chunk_size<1 or type(image_size) is not int or not 8<=image_size<=256:raise ObjectRouteRejected('Bounded image/chunk configuration required')
        plan={case['id']:case for case in split_cases(self.manifest['split_plan'])};seen=set()
        self.chunk_size,self.image_size=chunk_size,image_size;self.cache=OrderedDict();self.cached_bytes=0;self.indices=[];self.episodes=[]
        for descriptor in self.manifest['episodes']:
            if descriptor['id'] in seen or descriptor['id'] not in plan or plan[descriptor['id']]['partition']!='train' or descriptor.get('geometry_signature')!=plan[descriptor['id']]['geometry_signature']:
                raise ObjectRouteRejected('Unique source episode must match frozen train geometry')
            seen.add(descriptor['id'])
            if descriptor.get('split')!='train' or descriptor.get('admission',{}).get('eligible_for_optimizer') is not True:
                raise ObjectRouteRejected('Development/sealed/unknown/failed source cannot enter optimizer')
            source=(self.root/descriptor['file']).resolve()
            if not source.is_relative_to(self.root) or sha(source.read_bytes())!=descriptor['sha256']:raise ObjectRouteRejected('Actual training view SHA/path differs')
            envelope=(self.root/descriptor['canonical_envelope_file']).resolve();admission=(self.root/descriptor['canonical_admission_file']).resolve()
            if not all(path.is_relative_to(self.root) for path in (envelope,admission)) or sha(envelope.read_bytes())!=descriptor['canonical_envelope_sha256'] or sha(admission.read_bytes())!=descriptor['canonical_admission_sha256']:
                raise ObjectRouteRejected('Canonical source/admission SHA differs')
            report=json.loads(admission.read_bytes())
            if not report.get('eligible_as_successful_bc_expert') or (report.get('split') or {}).get('partition')!='train':raise ObjectRouteRejected('Canonical expert train admission required')
            index=len(self.episodes);self.episodes.append(dict(descriptor=descriptor,path=source))
            data=self._load(index);self.indices.extend((index,i) for i in range(len(data['rgb'])))
        if not self.indices:raise ObjectRouteRejected('No independently complete target train expert')
        bound=[]
        for name,checksum in self.manifest['source_files_sha256'].items():
            path=(self.root/name).resolve()
            if not path.is_relative_to(self.root) or sha(path.read_bytes())!=checksum:raise ObjectRouteRejected('Dataset source inventory differs')
            bound.append(dict(path=str(path),sha256=checksum))
        bound.append(dict(path=str(self.path),sha256=sha(self.path.read_bytes())))
        self.resume_context=dict(source_signature=sha(self.path.read_bytes()),source_files=bound,
            training_episode_ids=[row['descriptor']['id'] for row in self.episodes],actor_inputs=['actual_target_RGB','literal_preceding_command9'],
            source_phase_or_truth_in_actor=False,chunk_size=chunk_size,image_size=image_size)
    def _load(self,index):
        if index in self.cache:
            value=self.cache.pop(index);self.cache[index]=value;return value
        entry=self.episodes[index];path=entry['path']
        if path.stat().st_size>64*1024**2 or sha(path.read_bytes())!=entry['descriptor']['sha256']:raise ObjectRouteRejected('Bounded immutable view required')
        with np.load(path,allow_pickle=False) as data:
            value={name:data[name].copy() for name in ('rgb','state_native','action_native','frame_monotonic_ns')}
        count=len(value['rgb'])
        if (not 0<count<=2000 or value['rgb'].dtype!=np.uint8 or value['rgb'].shape!=(count,self.image_size,self.image_size,3) or
                any(value[name].shape!=(count,9) or not np.isfinite(value[name]).all() for name in ('state_native','action_native')) or
                value['frame_monotonic_ns'].shape!=(count,) or np.any(np.diff(value['frame_monotonic_ns'])<=0) or
                not np.array_equal(value['state_native'][1:],value['action_native'][:-1])):
            raise ObjectRouteRejected('Causal RGB/prior9/actualaction9 view differs')
        size=sum(item.nbytes for item in value.values())
        if size>128*1024**2:raise ObjectRouteRejected('Training episode cache bound')
        while self.cache and self.cached_bytes+size>128*1024**2:
            _,old=self.cache.popitem(last=False);self.cached_bytes-=sum(item.nbytes for item in old.values())
        self.cache[index]=value;self.cached_bytes+=size;return value
    def __len__(self):return len(self.indices)
    def __getitem__(self,index):
        import torch
        from .act_backend import IMAGE_KEY,prepare_rgb
        episode,position=self.indices[index];source=self._load(episode)
        _,state=actor_observation(source['rgb'][position],source['state_native'][position],self.image_size)
        positions=position+np.arange(self.chunk_size);padded=positions>=len(source['rgb']);positions=np.minimum(positions,len(source['rgb'])-1)
        action=(source['action_native'][positions]-LOW)*2/(HIGH-LOW)-1
        return {IMAGE_KEY:prepare_rgb(source['rgb'][position][None])[0],
            'observation.state':torch.from_numpy(state),'action':torch.from_numpy(action.astype(np.float32)),
            'action_is_pad':torch.from_numpy(padded)}
