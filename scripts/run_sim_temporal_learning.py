"""ENGINEERING_GLUE / ACT_LEROBOT_0_6_1: fresh 2x2 TARGET SIM candidate.

Same state10 architecture/data/seed/budget: ZERO versus observed ELAPSED clock,
ABSOLUTE9 versus actual DISPATCHED_INCREMENT9 labels. Chunk8 stays fixed. No
teacher, evaluator truth, phase or future timestamp enters learned inference.
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

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/name) for name in ('shared','00-control-center/src','01-autonomous-learning/src','scripts')]
import run_sim_object_learning as base
from explorer_shared.contracts import ACTION_NAMES,ACTION_UNITS,canonical,digest
from explorer_shared.experiments import experiment_identity
from explorer_shared.learning.sim_object_route import PHASES,LOW,HIGH,split_cases,target_split,complete_object_score,stationary_endpoint
from explorer_shared.learning.sim_temporal_route import CELLS,source_rate_profile,TemporalObjectChunks,TemporalChunkDispatcher

sha,write=base.sha,base.write


def freeze(output,baseline):
    """Source-only inventory; all old B inputs and source bytes stay unchanged."""
    output=Path(output).resolve();baseline=Path(baseline).resolve()
    if subprocess.check_output(['git','-C',str(ROOT),'status','--porcelain'],text=True).strip():raise ValueError('Commit clean candidate code before freeze')
    old=json.loads((baseline/'protocol.json').read_bytes())
    for path,checksum in old['source_files_sha256'].items():
        if sha(path)!=checksum:raise ValueError('Old B source/input bytes changed; never silently adopt')
    dataset=baseline/'dataset/manifest.json';manifest=json.loads(dataset.read_bytes())
    published=ROOT/'research/audits/target-visual-object-v3-results-20261027.json'
    proof=json.loads(published.read_bytes())
    if sha(baseline/'campaign-public-results.json')!=sha(published) or proof['training']['dataset_manifest_sha256']!=sha(dataset) or proof['frozen_protocol']['sha256']!=sha(baseline/'protocol.json'):
        raise ValueError('Exact published B cohort/aggregate binding required')
    if [row['id'] for row in manifest['episodes']]!=['target-v3-train-74104'] or manifest['episodes'][0]['frames']!=439:raise ValueError('Exact one verified train source required')
    rate=source_rate_profile(baseline/'teacher/target-v3-train-74104/target',ROOT/'01-autonomous-learning/src/legacy/config/arm-motion.json')
    output.mkdir(mode=0o700,parents=True,exist_ok=False);plan=deepcopy(old)
    meshes={}
    for source,checksum in old['mesh_files_sha256'].items():
        path=output/'inputs/CAD'/Path(source).name;write(path,Path(source).read_bytes());meshes[str(path)]=checksum
    template=Path(old['source_template_file']).parent
    for name in ('source-trace.json','source-geometry-proof.json','method-change.json','parameters.json'):write(output/'inputs/template'/name,(template/name).read_bytes())
    write(output/'inputs/so101_follower.json',Path(old['source_calibration_file']).read_bytes())
    write(output/'inputs/baseline-B-protocol.json',(baseline/'protocol.json').read_bytes())
    source_files=dict(old['source_files_sha256'])
    for directory in ('shared/explorer_shared','01-autonomous-learning/src/explorer_sim','01-autonomous-learning/src/legacy'):
        source_files.update({str(path):sha(path) for path in (ROOT/directory).rglob('*.py')})
    source_files.update({str(path):sha(path) for path in (output/'inputs').rglob('*') if path.is_file()})
    source_files.update({str(dataset.parent/name):checksum for name,checksum in manifest['source_files_sha256'].items()})
    rate_sources=[baseline/'teacher/target-v3-train-74104/target'/name for name in ('target-trace.json','result.json','initial-state/manifest.json','initial-state/profile-state.json')]
    for path in [Path(__file__),ROOT/'scripts/run_sim_object_learning.py',published,baseline/'campaign-public-results.json',dataset,*rate_sources]:source_files[str(path)]=sha(path)
    scenarios=dict(train=deepcopy(old['scenarios']['train']))
    rows={'development':[(85101,[-.004,.005],[.003,.004],-.03),(85102,[.004,-.005],[-.003,-.004],.03)],
        'sealed_test':[(86101,[-.008,.005],[.005,-.005],-.07),(86102,[.008,-.005],[-.005,.005],.07)]}
    for partition,values in rows.items():scenarios[partition]=[dict(id='target-temporal-C-'+partition+'-'+str(seed),seed=seed,object_offset_xy_m=obj,goal_offset_xy_m=goal,object_yaw_rad=yaw) for seed,obj,goal,yaw in values]
    old_cases=split_cases(old);new_cases=split_cases(dict(scenarios=scenarios));old_ids={r['seed'] for r in old_cases};old_geometries={r['geometry_signature'] for r in old_cases}
    if any(row['partition']!='train' and (row['seed'] in old_ids or row['geometry_signature'] in old_geometries) for row in new_cases):raise ValueError('Opened old heldout cannot be relabelled as a fresh cohort')
    plan.update(schema='explorer_TARGET_temporal_increment_factorial_protocol_v1',source_commit=subprocess.check_output(['git','-C',str(ROOT),'rev-parse','HEAD'],text=True).strip(),
        runner_sha256=sha(__file__),source_files_sha256=source_files,mesh_files_sha256=meshes,source_template_file=str(output/'inputs/template/source-trace.json'),
        source_template_sha256=sha(output/'inputs/template/source-trace.json'),source_calibration_file=str(output/'inputs/so101_follower.json'),
        baseline_B_root=str(baseline),baseline_B_protocol_sha256=sha(baseline/'protocol.json'),baseline_B_source_files_sha256=old['source_files_sha256'],
        training_source_manifest=str(dataset),training_source_manifest_sha256=sha(dataset),training_source_skill_version=3,target_skill_version=4,
        original_rate_profile=rate,clock_horizon_s=old['learned_max_steps']*rate['period_s'],cells=CELLS,scenarios=scenarios,
        actor_inputs=['actual_target_RGB','literal_preceding_command9','observed_elapsed_native_clock_or_ZERO_ablation'],
        actor_state_dimension=10,action_dimension=9,chunk_execution_steps=8,
        strategies=[dict(id='fixed_chunk8',execute_steps=8)],strategy_choice_basis='B_DEVELOPMENT_partial_grasp_not_B_sealed_selection',
        factorial_scope='Matched architecture/source/seed/sampler/budget; clock x label representation; no novelty/physical/H2/vision-causality claim',
        settings=dict(old['settings']),smoke_steps=8,smoke_per_cell=True,
        freeze_policy='ALL4_1200step_checkpoints_and_configs_and_identical_initial_parameter_digests_before_ANY_new_heldout',
        sealed_policy='One preregistered16trial paired series4cells_x_DEV2_SEALED2; no tuning/selection on consumed sealed',
        model_clock_zero_is_ablation_not_sensor_unavailability=True,
        reused_train_expert=['target-v3-train-74104'],negative_train_exclusions=['target-v3-train-74101','target-v3-train-74102','target-v3-train-74103'],
        stages_resources=dict(smoke='4 serial CPU2/4GiB/swap0/noCUDA/noEGL/max180s,8updates integration only',
            train='4 serial CPU2/4GiB/swap0/noCUDA/noEGL/max5400s,1200fixed updates each',
            rollout='16 serial CPU2/4GiB/swap0/EGL/max1800s,800steps max each'),
        physical_io=False,clock_can_memorize_timing_limitation=True,learned_source04=False,vision_causality_claim=False)
    write(output/'protocol.json',plan);print(json.dumps(dict(protocol=str(output/'protocol.json'),sha256=sha(output/'protocol.json'),runner_sha256=plan['runner_sha256'],source_commit=plan['source_commit'],rate=rate)),flush=True)


def checked(path,expected,unit):
    plan,before=base.checked_plan(path,expected,unit)
    if plan['schema']!='explorer_TARGET_temporal_increment_factorial_protocol_v1' or plan['cells']!=CELLS or plan['chunk_execution_steps']!=8:raise ValueError('Frozen factorial candidate required')
    if sha(plan['training_source_manifest'])!=plan['training_source_manifest_sha256']:raise ValueError('Original TRAIN aggregate changed')
    return plan,before


def train(plan,output,folder,cell,smoke):
    import torch
    from torch.utils.data import default_collate
    from explorer_shared.learning.act_backend import make_act_policy,optimize_act,save_reload_act,preprocessing_config,framework_info
    from explorer_shared.learning.artifacts import state_digest
    torch.set_num_threads(2);settings=dict(plan['settings']);settings['act_steps']=plan['smoke_steps'] if smoke else settings['act_steps']
    data=TemporalObjectChunks(plan['training_source_manifest'],cell=cell,chunk_size=settings['chunk_size'],image_size=settings['image_size'],rate_profile=plan['original_rate_profile'],horizon_s=plan['clock_horizon_s'])
    sources={row['path']:row['sha256'] for row in data.resume_context['source_files']}
    sources.update(plan['source_files_sha256']);sources[str(output/'protocol.json')]=sha(output/'protocol.json')
    data.resume_context['source_files']=[dict(path=path,sha256=checksum) for path,checksum in sorted(sources.items())]
    policy=make_act_policy(settings,10,9);initial=state_digest(policy)
    optimizer,losses,started=optimize_act(policy,data,settings,folder,lambda progress,stage:print(json.dumps(dict(progress=progress,stage=stage,cell=cell)),flush=True),checkpoint_interval=8 if smoke else 100)
    preprocessing=preprocessing_config(LOW,HIGH);preprocessing.update(schema='explorer_factorial_TARGET_ACT10_preprocessing_v1',cell=cell,
        actual_clock_available=True,clock_value_used=CELLS[cell]['clock']=='ELAPSED',clock_value_formula='observed_elapsed_native_s/declared_horizon_s',clock_horizon_s=plan['clock_horizon_s'],
        state_dimension=10,first9_state_low=LOW.tolist(),first9_state_high=HIGH.tolist(),last_state_feature='clock_ZERO_or_ELAPSED',
        action_representation=CELLS[cell]['labels'],original_rate_profile=plan['original_rate_profile'],chunk_execution_steps=8,
        decoded_action_contract='one_simultaneous_native_position6_velocity3_9D',source_phase_or_truth_in_actor=False)
    preprocessing.update(state_basis='literal_prior9_plus_actual_elapsed_clock_or_explicit_ZERO_ablation',
        state_low=LOW.tolist()+[0.],state_high=HIGH.tolist()+[plan['clock_horizon_s']],
        state_transform='first9:2*(prior9-LOW)/(HIGH-LOW)-1; last:observed_elapsed_s/horizon_s or explicit ZERO model ablation',
        state_feature_names=['prior_'+name for name in ACTION_NAMES]+['clock_model_value'],
        action_transform='2*(actual_endpoint9-LOW)/(HIGH-LOW)-1' if CELLS[cell]['labels']=='ABSOLUTE9' else 'first6:(actual_dispatched6-literal_prior6)/(original_velocity6*period); last3:actual_base_velocity3/declared_BASE_SPEED',
        action_decoder='absolute9 fixed inverse normalization' if CELLS[cell]['labels']=='ABSOLUTE9' else 'current_literal_prior6 + selected_increment6*original_velocity6*period; selected_base3*declared_BASE_SPEED')
    reloaded,checkpoint,error=save_reload_act(policy,optimizer,folder,preprocessing,default_collate([data[0]]),'cpu')
    result=dict(schema='explorer_factorial_TARGET_ACT10_training_v1',cell=cell,settings=settings,source_kind='simulation',training_source_skill_version=3,target_skill_version=4,
        source_manifest_sha256=plan['training_source_manifest_sha256'],official_implementation='lerobot0.6.1.ACTPolicy',initial_parameters_sha256=initial,
        state_dimension=10,action_dimension=9,checkpoint_path=str(checkpoint),checkpoint_sha256=sha(checkpoint/'model.safetensors'),
        checkpoint_files_sha256={str(path.relative_to(checkpoint)):sha(path) for path in checkpoint.rglob('*') if path.is_file()},
        reload_max_abs_error=error,optimizer_steps=len(losses),losses=losses,framework=framework_info(),integration_only=smoke,
        CUDA_optimizer=False,optimizer_device='cpu',heldout_optimizer_examples=0,training_samples=len(data),physical_io=False)
    write(folder/'training-result.json',result);return result


def seal(plan,output):
    results={cell:json.loads((output/'train'/cell/'training-result.json').read_bytes()) for cell in CELLS}
    if len({row['initial_parameters_sha256'] for row in results.values()})!=1:raise ValueError('Four factorial cells must share identical initial parameters')
    if len({row['checkpoint_files_sha256']['config.json'] for row in results.values()})!=1:raise ValueError('ACT architecture/config must be exactly identical across four cells')
    for cell,row in results.items():
        if row['cell']!=cell or row['integration_only'] or row['optimizer_steps']!=1200 or row['settings']!=plan['settings'] or row['heldout_optimizer_examples']!=0 or row['state_dimension']!=10 or row['action_dimension']!=9 or row['source_manifest_sha256']!=plan['training_source_manifest_sha256'] or not 0<=row['reload_max_abs_error']<=1e-6 or row['checkpoint_sha256']!=row['checkpoint_files_sha256']['model.safetensors']:raise ValueError('Every actual fixed1200/state10/action9 train result required before heldout')
        for name,checksum in row['checkpoint_files_sha256'].items():
            if sha(Path(row['checkpoint_path'])/name)!=checksum:raise ValueError('Frozen candidate checkpoint/config changed')
        config=json.loads((Path(row['checkpoint_path'])/'config.json').read_bytes())
        if config['input_features']['observation.state']['shape']!=[10] or config['output_features']['action']['shape']!=[9] or config['chunk_size']!=plan['settings']['chunk_size']:
            raise ValueError('Actual saved model architecture must be state10/action9 with the fixed chunk')
    if (output/'rollout').exists():raise ValueError('Never create four-model freeze after heldout has begun')
    result=dict(schema='explorer_factorial_TARGET_four_policy_freeze_v1',protocol_sha256=sha(output/'protocol.json'),cells=results,
        common_initial_parameters_sha256=next(iter(results.values()))['initial_parameters_sha256'],same_architecture_config_sha256=next(iter(results.values()))['checkpoint_files_sha256']['config.json'],
        chunk_execution_steps=8,heldout_not_yet_opened=True,physical_io=False)
    write(output/'four-policy-freeze.json',result);return dict(cells=list(results),four_policy_freeze_sha256=sha(output/'four-policy-freeze.json'))


def rollout(plan,output,folder,cell,case):
    import numpy as np
    import torch
    from PIL import Image
    from lerobot.policies.act.modeling_act import ACTPolicy
    from explorer_shared.learning.act_backend import IMAGE_KEY,prepare_rgb
    from explorer_shared.skill_transfer import _capture_initial_state,initial_state_capture_config,load_source_trace
    from legacy.simulator.explorer_model import joint_to_servo
    frozen=json.loads((output/'four-policy-freeze.json').read_bytes());model=frozen['cells'][cell];checkpoint=Path(model['checkpoint_path'])
    for name,checksum in model['checkpoint_files_sha256'].items():
        if sha(checkpoint/name)!=checksum:raise ValueError('Actual frozen model/config differs')
    if frozen['protocol_sha256']!=sha(output/'protocol.json') or frozen['chunk_execution_steps']!=8:raise ValueError('Original four-policy freeze required')
    torch.set_num_threads(2);policy=ACTPolicy.from_pretrained(checkpoint,local_files_only=True,strict=True).to('cpu').eval();policy.reset()
    def predict(image,state10):
        batch={IMAGE_KEY:prepare_rgb(image[None]),'observation.state':torch.from_numpy(state10[None])}
        with torch.inference_mode():return policy.predict_action_chunk(batch)[0].cpu().numpy()
    dispatcher=TemporalChunkDispatcher(predict,cell=cell,image_size=plan['settings']['image_size'],rate_profile=plan['original_rate_profile'],horizon_s=plan['clock_horizon_s'])
    target=None;rows=[];failure=None;report=None;score=None;initial=dict(status='NOT_RECORDED')
    try:
        target=base.build_target(plan,case,{'learned_policy':['settled_in_goal']})
        if not np.array_equal(target.env.velocity,plan['original_rate_profile']['velocity_deg_s']) or target.env.period!=plan['original_rate_profile']['period_s']:raise ValueError('Actual target original executor rate differs; no adjustment allowed')
        target.reset(case['seed']);source=load_source_trace(plan['source_template_file'],plan['source_template_sha256'])
        spec=dict(skill_id=plan['skill_id'],version=4,stages=[dict(id=phase) for phase in PHASES],actor_state_dimension=10,action_dimension=9,source_training_skill_version=3)
        capture=initial_state_capture_config(source_bindings=dict(factorial_driver=dict(path=str(Path(__file__).resolve()),sha256=sha(__file__))),scenario=dict(case,cell=cell,protocol_sha256=sha(output/'protocol.json'),checkpoint_sha256=model['checkpoint_sha256']))
        initial=_capture_initial_state(target,folder,capture,source,target.provider_identity,4,spec,case['seed'],target_split(case))
        for index in range(plan['learned_max_steps']):
            observation=target.observe();image=observation['target_rgb'];stamp=observation['observation_stamp'];raw,neural=dispatcher.next(image,observation['preceding_command9'],stamp)
            endpoint,projection=stationary_endpoint(raw,verified_named_base_fixture=target.env.model.equality('manipulation_base_fixture').id>=0);requested,rate=target.prepare_dispatch(endpoint)
            buffer=io.BytesIO();Image.fromarray(image).save(buffer,format='PNG');name='rgb/'+str(index).zfill(6)+'.png';write(folder/name,buffer.getvalue())
            write(folder/'observations'/(str(index).zfill(6)+'.json'),{key:(value.tolist() if isinstance(value,np.ndarray) else value) for key,value in observation.items() if key!='target_rgb'})
            dispatch=target.advance(requested,'learned_policy')
            if dispatch['pre_action_stamp']!=stamp or dispatch['finite_dynamics_step_completed'] is not True or dispatch['physical_io'] is not False:raise ValueError('Actual same-clock native dispatch required')
            rows.append(dict(index=index,target_rgb_file=name,target_rgb_sha256=hashlib.sha256(buffer.getvalue()).hexdigest(),renderer_receipt=observation['renderer_receipt'],preceding_command9=observation['preceding_command9'],neural=neural,
                raw_native_endpoint9=raw.tolist(),projection=projection,rate_preparation=rate,actual_requested9=requested,actual_dispatched_action9=dispatch['dispatched_action9'],dispatch=dispatch,
                learned_target_actor_executed=True,teacher_query_count=0,phase_or_truth_actor_input=False))
            facts,_=target._facts();score=complete_object_score(dict(source='target_finite_physics_evaluator',independent_of_action_commands=True,object_facts=facts),joint_to_servo(target.env.data.qpos[target.env.qids]))
            if score['full_object_success']:break
        report=target.report()
    except BaseException as error:
        failure=dict(type=type(error).__name__,detail=str(error))
        if target is not None:
            try:report=target.report()
            except Exception as unavailable:report=dict(source='unavailable',detail=str(unavailable))
    finally:
        if target is not None:target.env.close()
    write(folder/'learned-trace.json',rows)
    result=dict(schema='explorer_factorial_TARGET_learned_trial_v1',cell=cell,case=case,checkpoint_sha256=model['checkpoint_sha256'],four_policy_freeze_sha256=sha(output/'four-policy-freeze.json'),
        frames=len(rows),initial_state=initial,failure=failure,outcome='UNKNOWN' if failure or report is None else ('SUCCESS' if score and score['full_object_success'] else 'FAILURE'),
        terminal_score=score,independent_evaluation=report,raw_trace_sha256=digest(rows),learned_target_actor_executed=bool(rows),teacher_query_count=0,privileged_actor_input=False,
        actual_clock_available=True,clock_value_used=CELLS[cell]['clock']=='ELAPSED',action_representation=CELLS[cell]['labels'],chunk_execution_steps=8,
        source_kind='simulation',skill_id=plan['skill_id'],training_source_skill_version=3,target_skill_version=4,learned_source04=False,physical_io=False)
    write(folder/'result.json',result);return dict(cell=cell,case_id=case['id'],frames=len(rows),outcome=result['outcome'],terminal_score=score,failure=failure,raw_result_sha256=sha(folder/'result.json'))


def summary(plan,output,folder):
    cases={case['id']:case for case in split_cases(plan) if case['partition']!='train'}
    expected={(cell,identity) for cell in CELLS for identity in cases};rows=[];seen=set()
    frozen=json.loads((output/'four-policy-freeze.json').read_bytes());frozen_sha=sha(output/'four-policy-freeze.json')
    for path in sorted((output/'rollout').glob('*/*/*/result.json')):
        row=json.loads(path.read_bytes());key=(row['cell'],row['case']['id'])
        if key in seen or key not in expected:raise ValueError('Every factorial trial must be retained once')
        if row['case']!=cases[row['case']['id']] or row['checkpoint_sha256']!=frozen['cells'][row['cell']]['checkpoint_sha256'] or row['four_policy_freeze_sha256']!=frozen_sha or row['teacher_query_count']!=0 or row['privileged_actor_input'] is not False or row['chunk_execution_steps']!=8:
            raise ValueError('Actual trial must match frozen case/model/chunk8 and no teacher/oracle input')
        seen.add(key);rows.append(dict(cell=row['cell'],case=row['case'],outcome=row['outcome'],frames=row['frames'],failure=row['failure'],terminal_score=row['terminal_score'],
            teacher_query_count=row['teacher_query_count'],privileged_actor_input=row['privileged_actor_input'],checkpoint_sha256=row['checkpoint_sha256'],
            raw_result=dict(relative_path=str(path.relative_to(output)),sha256=sha(path),bytes=path.stat().st_size),raw_trace_sha256=row['raw_trace_sha256'],initial_manifest_sha256=row['initial_state'].get('manifest_sha256')))
    if seen!=expected:raise ValueError('All16 paired trials required; no dropping negative/unknown attempts')
    counts={partition:{cell:{status:sum(row['outcome']==status for row in rows if row['cell']==cell and row['case']['partition']==partition) for status in ('SUCCESS','FAILURE','UNKNOWN')} for cell in CELLS} for partition in ('development','sealed_test')}
    models=json.loads((output/'four-policy-freeze.json').read_bytes())['cells']
    for cell,model in models.items():
        card=dict(id='target-factorial-'+cell.lower()+'-'+model['checkpoint_sha256'][:20],**experiment_identity('cross_embodiment_transfer'),
            task='Конфета в коробку — preregistered temporal/increment TARGET ACT SIM',source_type='simulation',status='SIM_learned_candidate_factorial_assessed',
            action_contract=dict(id='rosmaster_joint9_position6_velocity3_v1',names=list(ACTION_NAMES),units=list(ACTION_UNITS),dimension=9,neutral_channels_retained=True),
            lineage=dict(skill_id=plan['skill_id'],skill_version=4,training_source_skill_version=3,source_manifest_sha256=plan['training_source_manifest_sha256'],checkpoint_sha256=model['checkpoint_sha256'],protocol_sha256=sha(output/'protocol.json'),four_policy_freeze_sha256=sha(output/'four-policy-freeze.json'),cell=cell),
            model_input_schema=dict(state_dimension=10,action_dimension=9,clock_sensor_available=True,clock_value_used=CELLS[cell]['clock']=='ELAPSED',action_representation=CELLS[cell]['labels'],raw_valid_clock_mask_never_falsified=True),
            evaluation=dict(trials=[row for row in rows if row['cell']==cell],all_cell_counts=counts,physical_run_ids=[],whole_skill_mastered=False,sealed_used_for_tuning=False),
            limitations=['Only one complete train demonstration; known shared candy CAD asset','Clock may memorize timing; no novelty/vision causality/H2/physical claim','Base explicitly welded stationary; no navigation or physical deployment qualification'])
        write(folder/('skill-card-'+cell+'.json'),card)
    result=dict(schema='explorer_factorial_TARGET_summary_v1',total_trials=len(rows),all16_completed=True,counts=counts,trials=rows,physical_io=False,whole_skill_mastered=False)
    write(folder/'summary.json',result);return result


def main():
    parser=argparse.ArgumentParser();parser.add_argument('stage',choices=['freeze','smoke','train','seal','rollout','summary'])
    for name in ('output','baseline','plan','sha','unit','cell','case'):parser.add_argument('--'+name)
    args=parser.parse_args()
    if args.stage=='freeze':return freeze(args.output,args.baseline)
    plan,before=checked(args.plan,args.sha,args.unit);output=Path(args.plan).resolve().parent;case=next((r for r in split_cases(plan) if r['id']==args.case),None)
    if args.stage in ('smoke','train'):
        if args.cell not in CELLS:raise ValueError('Exact preregistered cell required')
        folder=output/args.stage/args.cell
    elif args.stage=='rollout':
        if args.cell not in CELLS or case is None or case['partition']=='train':raise ValueError('Actual fresh heldout cell/case required')
        if case['partition']=='sealed_test' and len(list((output/'rollout/development').glob('*/*/result.json')))!=8:raise ValueError('Finish all8 DEV trials before opening sealed')
        folder=output/'rollout'/case['partition']/args.cell/case['id']
    else:folder=output/args.stage
    folder.mkdir(mode=0o700,parents=True,exist_ok=False);write(folder/'stage-started.json',dict(stage=args.stage,cell=args.cell,case=case,protocol_sha256=args.sha,resources=before,wall_start=time.time(),physical_io=False))
    try:
        if args.stage in ('smoke','train'):result=train(plan,output,folder,args.cell,args.stage=='smoke')
        elif args.stage=='seal':result=seal(plan,output)
        elif args.stage=='rollout':os.environ['MUJOCO_GL']='egl';result=rollout(plan,output,folder,args.cell,case)
        else:result=summary(plan,output,folder)
        write(folder/'stage-result.json',result);print(json.dumps({k:v for k,v in result.items() if k not in ('trials','losses')},default=str),flush=True)
    except BaseException as error:write(folder/'stage-error.json',dict(type=type(error).__name__,detail=str(error)));raise
    finally:write(folder/'resources-final.json',base.resources())


if __name__=='__main__':main()
