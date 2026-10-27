"""ENGINEERING_GLUE: explicit C-v2 numeric-admission evaluation driver.

Maintained NEW evaluation snapshot based on run_sim_temporal_learning.py at
6b25d010c5243409c7bb6aa498035d4894617ad4 (SHA23d7b42a1fb190517fad80b7799ec2f1016cb3356ec9848643d31e66acdcf931).
No monkeypatch of that frozen module and no optimizer/teacher execution. Four
original models, cases, metrics and chunk8 stay fixed; only proven input-precheck
roundoff can be admitted by an explicitly selected new dispatcher component.
"""
import argparse
from copy import deepcopy
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid
import xml.etree.ElementTree as ET

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/name) for name in ('shared','00-control-center/src','01-autonomous-learning/src','scripts')]
import run_sim_object_learning as base
from explorer_shared.contracts import canonical,digest,ACTION_NAMES,ACTION_UNITS
from explorer_shared.experiments import experiment_identity
from explorer_shared.learning.sim_object_route import PHASES,split_cases,complete_object_score,stationary_endpoint
from explorer_shared.learning.sim_temporal_route import CELLS
from explorer_shared.learning.sim_numeric_admission import NumericAdmissionDispatcher,command_context,SCHEMA,FORMULA

sha,write=base.sha,base.write
ORIGINAL_COMMIT='6b25d010c5243409c7bb6aa498035d4894617ad4'
ORIGINAL_PROTOCOL_SHA='a2803b074659923374fb38d5f60998593942bafc65d8d344091d9747bb87287b'
ORIGINAL_MODELS_SHA='94310623c1bdd88661cda0a5666bbf1f9a1708a81f56fcd020dc5757bbec74e2'
ORIGINAL_UNKNOWN_SHA='ef3230d456e8f1c93dbcc2a9f3423ec5d791bb4c4d9ef8e9a4b10bd9ae027245'


def freeze(output,original):
    output=Path(output).resolve();original=Path(original).resolve()
    if subprocess.check_output(['git','-C',str(ROOT),'status','--porcelain'],text=True).strip():raise ValueError('Commit clean v2 source before freeze')
    old=json.loads((original/'protocol.json').read_bytes());models=json.loads((original/'four-policy-freeze.json').read_bytes())
    if old['source_commit']!=ORIGINAL_COMMIT or sha(original/'protocol.json')!=ORIGINAL_PROTOCOL_SHA or sha(original/'four-policy-freeze.json')!=ORIGINAL_MODELS_SHA:
        raise ValueError('Exact reviewed C-v1 source/protocol/four-model snapshot required')
    for path,checksum in old['source_files_sha256'].items():
        if sha(path)!=checksum:raise ValueError('Frozen C-v1 source2050/input bytes changed')
    if models['protocol_sha256']!=sha(original/'protocol.json') or models['chunk_execution_steps']!=8 or set(models['cells'])!=set(CELLS):raise ValueError('Actual four-model C-v1 freeze required')
    for cell,model in models['cells'].items():
        if model['cell']!=cell or model['integration_only'] or model['optimizer_steps']!=1200 or model['settings']!=old['settings'] or model['reload_max_abs_error']!=0:raise ValueError('Original1200 fixed models only')
        for name,checksum in model['checkpoint_files_sha256'].items():
            if sha(Path(model['checkpoint_path'])/name)!=checksum:raise ValueError('Original model/config/preprocessing changed')
    if len({r['initial_parameters_sha256'] for r in models['cells'].values()})!=1 or len({r['checkpoint_files_sha256']['config.json'] for r in models['cells'].values()})!=1:raise ValueError('Actual same initial weights and architecture required')
    results=list((original/'rollout').glob('*/*/*/result.json'))
    if len(results)!=1:raise ValueError('C-v1 must retain exactly one UNKNOWN and15 NOT_STARTED')
    first_path=results[0];first=json.loads(first_path.read_bytes())
    if sha(first_path)!=ORIGINAL_UNKNOWN_SHA:raise ValueError('Original UNKNOWN bytes changed')
    started=list((original/'rollout').glob('*/*/*/stage-started.json'))
    if len(started)!=1 or started[0].parent!=first_path.parent:raise ValueError('C-v1 missing/partial attempts cannot be called NOT_STARTED')
    if first['cell']!='ZERO_ABS' or first['case']['id']!='target-temporal-C-development-85101' or first['outcome']!='UNKNOWN' or first['frames']!=163 or first['failure']!={'type':'ObjectRouteRejected','detail':'Prior command9 outside declared native limits'}:
        raise ValueError('Original C-v1 numeric precheck observation differs')
    if (original/'rollout/sealed_test').exists():raise ValueError('C-v1 sealed cases must remain unopened')
    manifest_path=first_path.parent/'initial-state/manifest.json';manifest=json.loads(manifest_path.read_bytes());xml_path=manifest_path.parent/'model.xml'
    if sha(manifest_path)!=first['initial_state']['manifest_sha256'] or sha(xml_path)!=manifest['payloads']['model.xml']['sha256']:raise ValueError('Original actual native model evidence differs')
    timestep=float(ET.fromstring(xml_path.read_bytes()).find('option').attrib['timestep']);period=old['original_rate_profile']['period_s'];n=round(period/timestep)
    cases=[r for r in split_cases(old) if r['partition']!='train'];remaining=[dict(cell=cell,case_id=case['id'],state='NOT_STARTED') for cell in CELLS for case in cases if (cell,case['id'])!=(first['cell'],first['case']['id'])]
    if len(cases)!=4 or any(sum(r['partition']==partition for r in cases)!=2 for partition in ('development','sealed_test')) or len(remaining)!=15:
        raise ValueError('Exactly original DEV2/SEALED2 and remaining15 required')
    disposition=dict(schema='explorer_C_v1_preserved_disposition_v1',original_protocol_sha256=sha(original/'protocol.json'),original_first_result_sha256=sha(first_path),
        first_result_path=str(first_path),first_outcome='UNKNOWN',first_dispatches=163,remaining=remaining,sealed_v1_opened=False,
        no_completed_16_trial_quality_claim=True,original_result_and_code_unchanged=True)
    status_path=original/'preserved-v1-disposition.json'
    if status_path.exists():
        if status_path.read_bytes()!=canonical(disposition):raise ValueError('Existing original disposition differs')
    else:write(status_path,disposition)
    output.mkdir(mode=0o700,parents=True,exist_ok=False)
    sources=dict(old['source_files_sha256'])
    extras=[Path(__file__),ROOT/'shared/explorer_shared/learning/sim_numeric_admission.py',original/'protocol.json',original/'four-policy-freeze.json',
        first_path,first_path.parent/'learned-trace.json',first_path.parent/'strict-prior-roundoff-diagnostic.json',first_path.parent/'v2-source-only-admission-check.json',manifest_path,xml_path,status_path]
    for model in models['cells'].values():extras.extend(Path(model['checkpoint_path'])/name for name in model['checkpoint_files_sha256'])
    sources.update({str(path.resolve()):sha(path) for path in extras})
    plan=deepcopy(old);plan.update(schema='explorer_factorial_C_numeric_admission_protocol_v2',source_commit=subprocess.check_output(['git','-C',str(ROOT),'rev-parse','HEAD'],text=True).strip(),
        runner_sha256=sha(__file__),source_files_sha256=sources,original_C_v1_root=str(original),original_C_v1_source_commit=old['source_commit'],original_C_v1_source_files_sha256=old['source_files_sha256'],
        four_model_freeze_file=str(original/'four-policy-freeze.json'),four_model_freeze_sha256=sha(original/'four-policy-freeze.json'),
        original_v1_disposition=disposition,original_models=models['cells'],models_or_training_changed=False,new_optimizer_updates=0,
        runtime_numeric_admission=dict(schema=SCHEMA,formula=FORMULA,helper_sha256=sha(ROOT/'shared/explorer_shared/learning/sim_numeric_admission.py'),
            selector='explicit NumericAdmissionDispatcher constructor in this NEW maintained driver; no old-module monkeypatch',
            context_inputs=['past actual start9','native command_before6','actual requested9','native requested6','intended endpoint6 from original equation','actual dispatch9','receipt/boot/native stamps','original velocity/period/timestep/substeps','source implementation/model identity'],
            context_is_neural_input=False,unit_roundoff='2**-53',derived_bound='gamma4*(abs(start)+abs(endpoint-start)), gamma4=4u/(1-4u), exact Fraction comparison',
            finite_no_overflow_or_subnormal=True,source_valid_start_request_endpoint_required=True,exact_native_float64_reproduction=True,raw_and_boundary_float32_neural_tensor_bytes_equal=True,
            raw_prior9_and_native_command_preserved=True,increment_decoder_uses_literal_raw_prior9=True,executor_provider_physics_limits_unchanged=True,
            unsupported_or_significant_or_no_evidence_or_tensor_difference='UNKNOWN; no physical/control dispatch from failed preprocessing'),
        native_profile=dict(velocity_deg_s=old['original_rate_profile']['velocity_deg_s'],period_s=period,timestep_s=timestep,substeps=n,
            provider_implementation_sha256=manifest['provider_class']['sha256'],environment_implementation_sha256=manifest['environment_class']['sha256'],engine_version=manifest['runtime']['mujoco']),
        evaluation_policy='All16 matched fresh trial UUIDs and native boot UUIDs under same four original models/cases/chunk8; no model/tuning/selection changes',
        first_development_case_previously_observed_for_numeric_bug_diagnosis=True,original_v1_UNKNOWN_never_replaced=True,sealed_v1_unopened=True,
        original_neural_normalizer_formula_unchanged=True,input_state_dimension=10,physical_io=False)
    write(output/'protocol.json',plan)
    evaluation=dict(schema='explorer_C_v2_evaluation_freeze_v1',protocol_sha256=sha(output/'protocol.json'),original_four_model_freeze_sha256=plan['four_model_freeze_sha256'],
        model_weights={cell:r['checkpoint_sha256'] for cell,r in models['cells'].items()},same_initial_parameters_sha256=models['common_initial_parameters_sha256'],
        identical_config_sha256=models['same_architecture_config_sha256'],numeric_admission=plan['runtime_numeric_admission'],source_commit=plan['source_commit'],
        no_v2_trial_started=True,original_first_DEV_disclosed=True,sealed_still_unopened=True,new_optimizer_updates=0,physical_io=False)
    write(output/'evaluation-freeze.json',evaluation)
    print(json.dumps(dict(protocol=str(output/'protocol.json'),protocol_sha256=sha(output/'protocol.json'),evaluation_freeze_sha256=sha(output/'evaluation-freeze.json'),source_commit=plan['source_commit'],runner_sha256=plan['runner_sha256'],helper_sha256=plan['runtime_numeric_admission']['helper_sha256'],v1_unknown=1,v1_not_started=15)),flush=True)


def checked(path,expected,unit):
    plan,before=base.checked_plan(path,expected,unit)
    helper=ROOT/'shared/explorer_shared/learning/sim_numeric_admission.py'
    if plan['schema']!='explorer_factorial_C_numeric_admission_protocol_v2' or plan['cells']!=CELLS or plan['runtime_numeric_admission']['schema']!=SCHEMA or plan['runtime_numeric_admission']['formula']!=FORMULA or plan['runtime_numeric_admission']['helper_sha256']!=sha(helper):raise ValueError('Exact registered numeric admission protocol/helper required')
    if sha(plan['four_model_freeze_file'])!=plan['four_model_freeze_sha256']:raise ValueError('Original models freeze changed')
    return plan,before


def expectation(plan,target):
    profile=plan['native_profile'];actual=target.provider_identity
    if actual['implementation_sha256']!=profile['provider_implementation_sha256'] or actual['environment_implementation_sha256']!=profile['environment_implementation_sha256'] or actual['engine_version']!=profile['engine_version']:raise ValueError('Original native implementation/runtime changed')
    import numpy as np
    if not np.array_equal(target.env.velocity,profile['velocity_deg_s']) or target.env.period!=profile['period_s'] or target.env.model.opt.timestep!=profile['timestep_s']:raise ValueError('Original actual native rate/timestep changed')
    return dict(source_identity={key:deepcopy(actual[key]) for key in ('implementation_sha256','environment_implementation_sha256','target_model_sha256','engine_version')},
        velocity_deg_s=deepcopy(profile['velocity_deg_s']),period_s=profile['period_s'],timestep_s=profile['timestep_s'],substeps=profile['substeps'])


def rollout(plan,output,folder,cell,case,trial_id,*,dispatcher_factory=NumericAdmissionDispatcher):
    """Explicit component factory belongs to this new source version only."""
    import numpy as np
    import torch
    from PIL import Image
    from lerobot.policies.act.modeling_act import ACTPolicy
    from explorer_shared.learning.act_backend import IMAGE_KEY,prepare_rgb
    from explorer_shared.skill_transfer import _capture_initial_state,initial_state_capture_config,load_source_trace
    from legacy.simulator.explorer_model import joint_to_servo
    model=plan['original_models'][cell];checkpoint=Path(model['checkpoint_path'])
    for name,checksum in model['checkpoint_files_sha256'].items():
        if sha(checkpoint/name)!=checksum:raise ValueError('Original fixed model/config/preprocessing changed')
    torch.set_num_threads(2);policy=ACTPolicy.from_pretrained(checkpoint,local_files_only=True,strict=True).to('cpu').eval();policy.reset()
    def predict(image,state10):
        if state10.shape!=(10,):raise ValueError('Original neural state10 required')
        batch={IMAGE_KEY:prepare_rgb(image[None]),'observation.state':torch.from_numpy(state10[None])}
        with torch.inference_mode():return policy.predict_action_chunk(batch)[0].cpu().numpy()
    target=None;rows=[];failure=None;report=None;score=None;initial=dict(status='NOT_RECORDED');previous_context=None;attempt_index=0
    try:
        target=base.build_target(plan,case,{'learned_policy':['settled_in_goal']});target.reset(case['seed']);expected=expectation(plan,target)
        dispatcher=dispatcher_factory(predict,cell=cell,image_size=plan['settings']['image_size'],rate_profile=plan['original_rate_profile'],horizon_s=plan['clock_horizon_s'],admission_expectation=expected)
        source=load_source_trace(plan['source_template_file'],plan['source_template_sha256'])
        spec=dict(skill_id=plan['skill_id'],version=4,stages=[dict(id=phase) for phase in PHASES],actor_state_dimension=10,action_dimension=9,source_training_skill_version=3)
        split=dict(partition=case['partition'],episode_id=trial_id,scene_id=case['scene_id'],session_id='fresh-native-reset-'+trial_id,group_id=case['geometry_signature'])
        capture=initial_state_capture_config(source_bindings=dict(numeric_driver=dict(path=str(Path(__file__).resolve()),sha256=sha(__file__)),numeric_admission_helper=dict(path=str(ROOT/'shared/explorer_shared/learning/sim_numeric_admission.py'),sha256=plan['runtime_numeric_admission']['helper_sha256'])),
            scenario=dict(case,trial_id=trial_id,cell=cell,protocol_sha256=sha(output/'protocol.json'),checkpoint_sha256=model['checkpoint_sha256'],numeric_admission=SCHEMA))
        initial=_capture_initial_state(target,folder,capture,source,target.provider_identity,4,spec,case['seed'],split)
        write(folder/'admission-expectation.json',expected)
        for index in range(plan['learned_max_steps']):
            attempt_index=index;observation=target.observe();image=observation['target_rgb'];stamp=observation['observation_stamp']
            buffer=io.BytesIO();Image.fromarray(image).save(buffer,format='PNG');name='rgb/'+str(index).zfill(6)+'.png';write(folder/name,buffer.getvalue())
            write(folder/'observations'/(str(index).zfill(6)+'.json'),{key:(value.tolist() if isinstance(value,np.ndarray) else value) for key,value in observation.items() if key!='target_rgb'})
            raw,neural=dispatcher.next(image,observation['preceding_command9'],stamp,previous_command_context=previous_context)
            endpoint,projection=stationary_endpoint(raw,verified_named_base_fixture=target.env.model.equality('manipulation_base_fixture').id>=0);requested,rate=target.prepare_dispatch(endpoint)
            request=dict(index=index,raw_literal_prior9=observation['preceding_command9'],raw_native_endpoint9=raw.tolist(),projection=projection,actual_requested9=requested,rate_preparation=rate,neural=neural,
                past_command_context_sha256=None if previous_context is None else digest(previous_context))
            write(folder/'requests'/(str(index).zfill(6)+'.json'),request)
            dispatch=target.advance(requested,'learned_policy');write(folder/'dispatch'/(str(index).zfill(6)+'.json'),dispatch)
            # Record actual completion before validation that could itself fail.
            rows.append(dict(request,target_rgb_file=name,target_rgb_sha256=hashlib.sha256(buffer.getvalue()).hexdigest(),renderer_receipt=observation['renderer_receipt'],
                preceding_command9=deepcopy(observation['preceding_command9']),actual_dispatched_action9=deepcopy(dispatch.get('dispatched_action9')),dispatch=deepcopy(dispatch),
                learned_target_actor_executed=True,teacher_query_count=0,phase_or_truth_actor_input=False))
            if dispatch['pre_action_stamp']!=stamp or dispatch['finite_dynamics_step_completed'] is not True or dispatch['physical_io'] is not False:raise ValueError('Actual same-clock finite native dispatch required')
            previous_context=command_context(target,observation,requested,dispatch,rate)
            write(folder/'past-command-contexts'/(str(index).zfill(6)+'.json'),previous_context)
            rows[-1]['native_command_context_sha256']=digest(previous_context)
            facts,_=target._facts();score=complete_object_score(dict(source='target_finite_physics_evaluator',independent_of_action_commands=True,object_facts=facts),joint_to_servo(target.env.data.qpos[target.env.qids]))
            if score['full_object_success']:break
        report=target.report()
    except BaseException as error:
        failure=dict(type=type(error).__name__,detail=str(error),attempt_index=attempt_index,input_RGB_retained=(folder/'rgb'/(str(attempt_index).zfill(6)+'.png')).is_file())
        if target is not None:
            try:report=target.report()
            except Exception as unavailable:report=dict(source='unavailable',detail=str(unavailable))
    finally:
        if target is not None:target.env.close()
    write(folder/'learned-trace.json',rows)
    result=dict(schema='explorer_factorial_C_numeric_admission_trial_v2',cell=cell,case=case,trial_id=trial_id,checkpoint_sha256=model['checkpoint_sha256'],
        original_four_model_freeze_sha256=plan['four_model_freeze_sha256'],evaluation_freeze_sha256=sha(output/'evaluation-freeze.json'),protocol_sha256=sha(output/'protocol.json'),
        frames=len(rows),initial_state=initial,failure=failure,outcome='UNKNOWN' if failure or report is None else ('SUCCESS' if score and score['full_object_success'] else 'FAILURE'),
        terminal_score=score,independent_evaluation=report,raw_trace_sha256=digest(rows),learned_target_actor_executed=bool(rows),teacher_query_count=0,privileged_actor_input=False,
        numerical_admission=SCHEMA,numerical_admissions=sum(row['neural']['numeric_admission']['kind']=='PROVEN_NATIVE_INTERPOLATION_ROUNDOFF' for row in rows),
        raw_prior9_preserved=True,requested_issued_and_native_values_preserved=True,original_executor_and_physics_unchanged=True,
        actual_clock_available=True,clock_value_used=CELLS[cell]['clock']=='ELAPSED',action_representation=CELLS[cell]['labels'],chunk_execution_steps=8,
        source_kind='simulation',skill_id=plan['skill_id'],training_source_skill_version=3,target_skill_version=4,learned_source04=False,physical_io=False)
    write(folder/'result.json',result);return dict(cell=cell,case_id=case['id'],trial_id=trial_id,frames=len(rows),outcome=result['outcome'],terminal_score=score,failure=failure,numerical_admissions=result['numerical_admissions'],raw_result_sha256=sha(folder/'result.json'))


def summary(plan,output,folder):
    cases={r['id']:r for r in split_cases(plan) if r['partition']!='train'};expected={(cell,identity) for cell in CELLS for identity in cases};seen=set();uuids=set();rows=[]
    if len(expected)!=16 or any(sum(r['partition']==partition for r in cases.values())!=2 for partition in ('development','sealed_test')):raise ValueError('Exactly16 matched trials under DEV2/SEALED2 required')
    for path in sorted((output/'rollout').glob('*/*/*/result.json')):
        r=json.loads(path.read_bytes());key=(r['cell'],r['case']['id']);trial=r['trial_id']
        if key not in expected or key in seen or trial in uuids or str(uuid.UUID(trial))!=trial or r['case']!=cases[r['case']['id']]:raise ValueError('All16 matched unique case/cell/fresh-UUID trials required')
        if r['checkpoint_sha256']!=plan['original_models'][r['cell']]['checkpoint_sha256'] or r['evaluation_freeze_sha256']!=sha(output/'evaluation-freeze.json') or r['teacher_query_count']!=0 or r['privileged_actor_input'] is not False or r['chunk_execution_steps']!=8:raise ValueError('Fixed original model/chunk8/no-truth contract differs')
        seen.add(key);uuids.add(trial);rows.append({key:deepcopy(r[key]) for key in ('cell','case','trial_id','checkpoint_sha256','frames','failure','outcome','terminal_score','raw_trace_sha256','numerical_admissions','teacher_query_count','privileged_actor_input')})
        rows[-1]['raw_result']=dict(relative_path=str(path.relative_to(output)),sha256=sha(path),bytes=path.stat().st_size);rows[-1]['initial_manifest_sha256']=r['initial_state'].get('manifest_sha256')
    if seen!=expected:raise ValueError('Never drop a negative/unknown/missing trial from all16 report')
    counts={partition:{cell:{status:sum(row['outcome']==status for row in rows if row['case']['partition']==partition and row['cell']==cell) for status in ('SUCCESS','FAILURE','UNKNOWN')} for cell in CELLS} for partition in ('development','sealed_test')}
    for cell,model in plan['original_models'].items():
        card=dict(id='target-factorial-v2-'+cell.lower()+'-'+model['checkpoint_sha256'][:20],**experiment_identity('cross_embodiment_transfer'),
            task='Конфета в коробку — C2×2 TARGET ACT with proven numeric input admission',source_type='simulation',status='SIM_learned_candidate_factorial_v2_assessed',
            action_contract=dict(id='rosmaster_joint9_position6_velocity3_v1',names=list(ACTION_NAMES),units=list(ACTION_UNITS),dimension=9,neutral_channels_retained=True),
            lineage=dict(skill_id=plan['skill_id'],skill_version=4,training_source_skill_version=3,original_training_source_commit=plan['original_C_v1_source_commit'],checkpoint_sha256=model['checkpoint_sha256'],
                source_manifest_sha256=plan['training_source_manifest_sha256'],protocol_sha256=sha(output/'protocol.json'),evaluation_freeze_sha256=sha(output/'evaluation-freeze.json'),runtime_adapter=SCHEMA,cell=cell),
            model_input_schema=dict(state_dimension=10,action_dimension=9,original_normalizer_formula_unchanged=True,raw_clock_available=True,clock_value_used=CELLS[cell]['clock']=='ELAPSED',action_representation=CELLS[cell]['labels'],raw_valid_clock_mask_never_falsified=True),
            evaluation=dict(trials=[r for r in rows if r['cell']==cell],all_cell_counts=counts,physical_run_ids=[],whole_skill_mastered=False,sealed_used_for_tuning=False,
                original_v1_UNKNOWN163_retained=True,original_v1_remaining15_not_started=True,first_DEV_seen_for_numerical_bug=True),
            limitations=['One verified train demonstration; same known candy CAD asset','Common numeric-admission correction is not a model gain; firstDEV previously observed','Clock may memorize timing; no vision causality/novelty/H2/physical claim','Stationary welded base; no navigation qualification'])
        write(folder/('skill-card-'+cell+'.json'),card)
    result=dict(schema='explorer_factorial_C_numeric_admission_summary_v2',all16_completed=True,total_trials=16,paired_scenario_count=4,counts=counts,trials=rows,
        original_v1_outcome=dict(UNKNOWN=1,NOT_STARTED=15),first_DEV_previously_observed_for_bug=True,model_or_training_changes=False,new_optimizer_updates=0,physical_io=False,whole_master_prompt_complete=False)
    write(folder/'summary.json',result);return result


def main():
    parser=argparse.ArgumentParser();parser.add_argument('stage',choices=['freeze','rollout','summary'])
    for name in ('output','original','plan','sha','unit','cell','case'):parser.add_argument('--'+name)
    args=parser.parse_args()
    if args.stage=='freeze':return freeze(args.output,args.original)
    plan,before=checked(args.plan,args.sha,args.unit);output=Path(args.plan).resolve().parent;trial_id=None;case=None
    if args.stage=='rollout':
        case=next((r for r in split_cases(plan) if r['id']==args.case and r['partition']!='train'),None)
        if args.cell not in CELLS or case is None:raise ValueError('Exact original factorial cell/case required')
        if case['partition']=='sealed_test' and len(list((output/'rollout/development').glob('*/*/result.json')))!=8:raise ValueError('Finish all8 DEV attempts before sealed')
        folder=output/'rollout'/case['partition']/args.cell/case['id'];trial_id=str(uuid.uuid4())
    else:folder=output/'summary'
    folder.mkdir(mode=0o700,parents=True,exist_ok=False);write(folder/'stage-started.json',dict(stage=args.stage,cell=args.cell,case=case,trial_id=trial_id,protocol_sha256=args.sha,resources=before,wall_start=time.time(),physical_io=False))
    try:
        if args.stage=='rollout':os.environ['MUJOCO_GL']='egl';result=rollout(plan,output,folder,args.cell,case,trial_id)
        else:result=summary(plan,output,folder)
        write(folder/'stage-result.json',result);print(json.dumps({k:v for k,v in result.items() if k!='trials'},default=str),flush=True)
    except BaseException as error:write(folder/'stage-error.json',dict(type=type(error).__name__,detail=str(error)));raise
    finally:write(folder/'resources-final.json',base.resources())


if __name__=='__main__':main()
