"""ENGINEERING_GLUE / ACT_LEROBOT_0_6_1: preregistered fresh TARGET learning.

Each stage is a new process. No original archived Python is executed, no device
or production State is accessed, and teacher geometry never crosses the actor
boundary. EGL is rendering only; optimizer and inference are explicitly CPU.
"""
from __future__ import annotations
import argparse
import gc
import hashlib
import io
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import time
import xml.etree.ElementTree as ET

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/name) for name in ('shared','00-control-center/src','01-autonomous-learning/src')]
from explorer_shared.contracts import ACTION_NAMES,ACTION_UNITS,canonical,digest
from explorer_shared.learning.sim_object_route import (PHASES,LOW,HIGH,split_cases,target_split,
    complete_object_score,export_teacher_view,ChunkDispatcher,stationary_endpoint,VisualObjectChunks)

GIB=1024**3


def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write(path,value):
    path=Path(path);path.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
    raw=value if isinstance(value,bytes) else canonical(value)
    with path.open('xb') as stream:stream.write(raw);stream.flush();os.fsync(stream.fileno())


def resources():
    relative=next(row.split('::',1)[1] for row in Path('/proc/self/cgroup').read_text().splitlines() if row.startswith('0::'))
    folder=Path('/sys/fs/cgroup')/relative.lstrip('/')
    return dict(pid=os.getpid(),cgroup=str(folder),limits={name:(folder/name).read_text().strip() for name in ('cpu.max','memory.max','memory.swap.max')},
        memory_peak_bytes=int((folder/'memory.peak').read_text()),memory_events=(folder/'memory.events').read_text())


def checked_plan(path,expected,unit):
    path=Path(path).resolve();raw=path.read_bytes()
    if hashlib.sha256(raw).hexdigest()!=expected:raise ValueError('Frozen protocol SHA differs')
    plan=json.loads(raw)
    if subprocess.check_output(['git','-C',str(ROOT),'rev-parse','HEAD'],text=True).strip()!=plan['source_commit'] or subprocess.check_output(['git','-C',str(ROOT),'status','--porcelain'],text=True).strip():raise ValueError('Exact clean trusted code required')
    for source,checksum in plan['source_files_sha256'].items():
        if sha(source)!=checksum:raise ValueError('Frozen implementation/input/CAD SHA differs: '+source)
    resource=resources();quota,period=resource['limits']['cpu.max'].split()
    if (Path(resource['cgroup']).name!=unit+'.service' or resource['limits']['memory.max']!=str(4*GIB) or
            resource['limits']['memory.swap.max']!='0' or quota=='max' or int(quota)>2*int(period)):
        raise ValueError('Own CPU<=2/4GiB/swap0 unit required before model import')
    if plan['optimizer_device']!='cpu' or plan['physical_io'] is not False:raise ValueError('CPU/nonphysical only')
    os.environ.update(OMP_NUM_THREADS='2',MKL_NUM_THREADS='2',OPENBLAS_NUM_THREADS='2',HF_HUB_OFFLINE='1',TOKENIZERS_PARALLELISM='false')
    return plan,resource


def freeze(output,mesh_dir,template,calibration):
    """No model/renderer/Torch import: exact sources, original bytes and split."""
    output=Path(output).resolve();output.mkdir(mode=0o700,parents=True,exist_ok=False)
    mesh_dir=Path(mesh_dir).resolve();template=Path(template).resolve();calibration=Path(calibration).resolve()
    if subprocess.check_output(['git','-C',str(ROOT),'status','--porcelain'],text=True).strip():raise ValueError('Commit code/tests before freezing')
    scenarios={}
    values={
        'train':[(74101,[-.003,-.001],[.002,-.001],-.02),(74102,[.003,.001],[-.002,.001],.02),
            (74103,[-.001,.003],[.001,.002],.01),(74104,[.001,-.003],[-.001,-.002],-.01)],
        'development':[(75101,[-.006,.002],[.004,-.003],-.04),(75102,[.006,-.002],[-.004,.003],.04)],
        'sealed_test':[(76101,[-.009,-.004],[.006,.004],-.06),(76102,[.009,.004],[-.006,-.004],.06)]}
    for partition,rows in values.items():scenarios[partition]=[dict(id='target-v3-'+partition+'-'+str(seed),seed=seed,object_offset_xy_m=obj,goal_offset_xy_m=goal,object_yaw_rad=yaw) for seed,obj,goal,yaw in rows]
    source_names=['scripts/run_sim_object_learning.py']
    for directory in ('shared/explorer_shared','01-autonomous-learning/src/explorer_sim','01-autonomous-learning/src/legacy'):
        source_names.extend(str(path.relative_to(ROOT)) for path in (ROOT/directory).rglob('*.py'))
    source_names.extend(['shared/robotics/so101/so101_new_calib.urdf','shared/robotics/so101/README.md',
        '01-autonomous-learning/src/legacy/config/explorer.urdf','01-autonomous-learning/src/legacy/config/arm_model.json',
        '01-autonomous-learning/src/legacy/config/arm-motion.json'])
    sources={str(ROOT/name):sha(ROOT/name) for name in sorted(set(source_names))}
    expected=json.loads((ROOT/'01-autonomous-learning/src/legacy/config/arm_model.json').read_bytes())['asset_sha256'];meshes={}
    for name,checksum in expected.items():
        if name.endswith('.STL'):
            source=mesh_dir/Path(name).name
            if sha(source)!=checksum:raise ValueError('Verified private CAD required: '+str(source))
            target=output/'inputs/CAD'/source.name;write(target,source.read_bytes());meshes[str(target)]=checksum
    template_doc=json.loads((template/'source-trace.json').read_bytes())
    for name in ('source-trace.json','source-geometry-proof.json','method-change.json','parameters.json'):
        write(output/'inputs/template'/name,(template/name).read_bytes())
    write(output/'inputs/so101_follower.json',calibration.read_bytes())
    sources.update(meshes)
    sources.update({str(path):sha(path) for path in (output/'inputs').rglob('*') if path.is_file()})
    plan=dict(schema='explorer_fresh_target_visual_learning_protocol_v1',source_commit=subprocess.check_output(['git','-C',str(ROOT),'rev-parse','HEAD'],text=True).strip(),
        runner_sha256=sha(__file__),source_files_sha256=sources,mesh_files_sha256=meshes,
        source_template_file=str(output/'inputs/template/source-trace.json'),source_template_sha256=sha(output/'inputs/template/source-trace.json'),
        source_template_kind='SCRIPTED_SIM_geometric_template_not_learned_source04',skill_id=template_doc['skill_id'],source_skill_version=template_doc['skill_version'],target_skill_version=3,
        source_calibration_file=str(output/'inputs/so101_follower.json'),phase_criteria=json.loads((template/'parameters.json').read_bytes())['phase_criteria'],
        scenarios=scenarios,optimizer_device='cpu',render_context='EGL_GPU_RENDER_NOT_CUDA_OPTIMIZER',physical_io=False,
        teacher_max_steps=1500,learned_max_steps=800,strategies=[dict(id='chunk1',execute_steps=1),dict(id='chunk8',execute_steps=8)],
        settings=dict(seed=77101,device='cpu',image_size=64,chunk_size=16,batch_size=8,act_steps=1200),smoke_steps=8,
        actor_inputs=['actual_target_RGB_resized64_bilinear_ImageNet_once','literal_preceding_command9_fixed_native_normalization'],
        excluded_actor_inputs=['teacher_geometry','object_truth','phase','evaluator','source_waypoints','goal_pose_truth','future_action_or_timestamp'],
        base_constraint='actual_named_manipulation_base_fixture_stationary_velocity_projection_raw9_retained_all_masks_true',
        evaluator='unchanged_independent_FullTaskEvaluator_substeps_plus_measured_terminal_open_gripper_and_retreat',
        claim_scope='fresh_target_visual_student_on_known_candy_CAD_geometry_regions_not_learned_source04_H2_or_navigation_or_physical_mastery',
        split_policy='actual_geometry_offset_yaw_groups_disjoint_same_asset_explicit_not_new_object_generalization',
        sealed_policy='one_paired_chunk1_chunk8_series_after_checkpoint_and_both_configs_frozen_no_selection_or_tuning_on_sealed',
        stages_resources=dict(teacher='CPU2/4GiB/swap0/EGL max1800s serial per-case child EXIT',
            smoke='CPU2/4GiB/swap0/noEGL/noCUDA max180s optimizer8 integration_only',
            train='CPU2/4GiB/swap0/noEGL/noCUDA max5400s optimizer1200 fixed',
            evaluation='CPU2/4GiB/swap0/EGL max1800s serial per-case child EXIT'),
        numerical_reproduction_claim=False,all_started_attempts_retained=True,failures_analysis_only=True)
    split_cases(plan);write(output/'protocol.json',plan)
    print(json.dumps(dict(protocol_path=str(output/'protocol.json'),sha256=sha(output/'protocol.json'),runner_sha256=plan['runner_sha256'],source_commit=plan['source_commit'])),flush=True)


def build_target(plan,case,criteria):
    """Same concrete class/physics: only declared scene geometry changes.

    Copy small ABI, release old model/data BEFORE compiling replacement CAD.
    No provider/env calls occur while the environment is temporarily unbound.
    """
    import numpy as np
    import mujoco
    from explorer_sim.manipulation_env import ManipulationEnv
    from legacy.simulator.explorer_model import ARM_JOINTS
    from explorer_shared.skill_transfer_mujoco import MuJoCoTargetProvider
    mesh=Path(next(iter(plan['mesh_files_sha256']))).parent
    os.environ['EXPLORER_SIM_MESH_DIR']=str(mesh)
    env=ManipulationEnv(mesh,'candy_to_tray',horizon=plan['learned_max_steps'])
    document=ET.fromstring(env.xml);candy=document.find("./worldbody/body[@name='candy']");container=document.find("./worldbody/body[@name='bin']")
    if candy is None or container is None:raise ValueError('Pinned direct-worldbody scene required')
    pos=np.fromstring(candy.get('pos'),sep=' ');pos[:2]+=case['object_offset_xy_m'];candy.set('pos',' '.join(map(str,pos)))
    yaw=case['object_yaw_rad'];candy.set('quat',f'{math.cos(yaw/2)} 0 0 {math.sin(yaw/2)}')
    pos=np.fromstring(container.get('pos'),sep=' ');pos[:2]+=case['goal_offset_xy_m'];container.set('pos',' '.join(map(str,pos)))
    xml=ET.tostring(document,encoding='unicode')
    abi={name:getattr(env.model,name) for name in ('nq','nv','nu','nbody','ngeom','neq')};qids=env.qids.copy();dids=env.dids.copy()
    env.data=None;env.model=None;gc.collect()
    env.xml=xml;env.model=mujoco.MjModel.from_xml_string(xml);env.data=mujoco.MjData(env.model)
    if any(getattr(env.model,name)!=value for name,value in abi.items()) or not np.array_equal(qids,[env.model.joint(name).qposadr[0] for name in ARM_JOINTS]) or not np.array_equal(dids,[env.model.joint(name).dofadr[0] for name in ARM_JOINTS]):raise ValueError('Scene-only ABI changed')
    if env.model.equality('manipulation_base_fixture').id<0:raise ValueError('Actual stationary fixture required')
    env.goal=env.model.body('bin').pos.copy();env.reset(seed=case['seed'])
    provider=MuJoCoTargetProvider(env,target_urdf_sha256=sha(ROOT/'01-autonomous-learning/src/legacy/config/explorer.urdf'),
        mesh_files=plan['mesh_files_sha256'],phase_evidence_map=criteria)
    return provider


def mapper_for(plan,target):
    from explorer_shared.kinematics import URDFChain,transform
    from explorer_shared.transfer_mapping import SourceCoordinates,TargetCoordinates
    from explorer_shared.transfer_calibration import fit_aperture_reference,register_task_frames
    from explorer_shared.skill_transfer import TaskRelativeMapper,derive_cad_tool_adapter
    from explorer_sim.manipulation_env import LOW as ARM_LOW,HIGH as ARM_HIGH
    source=URDFChain.from_urdf(ROOT/'shared/robotics/so101/so101_new_calib.urdf','base_link','gripper_frame_link')
    arm=URDFChain.from_urdf(ROOT/'01-autonomous-learning/src/legacy/config/explorer.urdf','base_footprint','Gripping')
    raw=Path(plan['source_calibration_file']).read_bytes();coords=SourceCoordinates(raw,hashlib.sha256(raw).hexdigest());profile=coords.profile(source,(ROOT/'shared/robotics/so101/README.md').read_bytes())
    curves={}
    for role,commands,gaps in [('source',[0,50,100],[.002,.03,.06]),('target',[30,100,170],[.06,.03,.002])]:
        curves[role]=fit_aperture_reference([dict(role=role,native_gripper=value,aperture_m=gap,uncertainty_m=.001,frame_sha256=digest([role,value,gap]),calibration_id='virtual-CAD-aperture-'+role) for value,gap in zip(commands,gaps)],role=role,provenance='synthetic')
    transforms=dict(source_object=transform([.25,0,.014]),source_goal=transform([.25,.2,0]),target_object=transform(target.env.points().mean(0)),target_goal=transform(target.env.goal))
    transforms['target_object'][:3,:3]=target.env.data.body('candy').xmat.reshape(3,3)
    taskframes=register_task_frames([dict(role=role,base_from_frame=matrix.tolist(),frame_sha256=digest([role,matrix.tolist()]),calibration_id='declared-virtual-task-frame-'+role,uncertainty_m=.001) for role,matrix in transforms.items()],provenance='synthetic')
    mapper=TaskRelativeMapper(source,arm,coords,TargetCoordinates((ROOT/'01-autonomous-learning/src/legacy/src/servo_coordinates.py').read_bytes()),
        source_profile=profile,source_aperture=curves['source'],target_aperture=curves['target'],task_frames=taskframes,target_low=ARM_LOW,target_high=ARM_HIGH,
        collision_checker=target.collision_check,tool_frame_adapter=derive_cad_tool_adapter(source,arm,source_closing_axis_tip=[0,1,0],target_closing_axis_tip=[0,1,0]))
    return mapper,dict(source_profile=profile,task_frames=taskframes,apertures=curves,tool_adapter=mapper.tool_frame_adapter)


def teacher(plan,case,folder,protocol_sha):
    from explorer_shared.skill_transfer import load_source_trace,record_target_trial,initial_state_capture_config
    from legacy.simulator.explorer_model import joint_to_servo
    target=None
    try:
        target=build_target(plan,case,plan['phase_criteria']);mapper,parameters=mapper_for(plan,target)
        write(folder/'target-model.xml',target.env.xml.encode());write(folder/'parameters.json',dict(parameters,scenario=case,physics_defaults_changed=False))
        bundle=load_source_trace(plan['source_template_file'],plan['source_template_sha256'])
        spec=dict(skill_id=plan['skill_id'],version=3,stages=[dict(id=phase) for phase in PHASES],actor_inputs=plan['actor_inputs'])
        capture=initial_state_capture_config(source_bindings=dict(driver=dict(path=str(Path(__file__).resolve()),sha256=sha(__file__))),scenario=dict(case,protocol_sha256=protocol_sha,purpose='fresh_explicit_target_train_teacher'))
        result=record_target_trial(bundle,mapper,target,folder/'target',target_version=3,target_skill_spec=spec,seed=case['seed'],max_steps=plan['teacher_max_steps'],target_split=target_split(case),initial_state_capture=capture)
        measured=joint_to_servo(target.env.data.qpos[target.env.qids]);score=complete_object_score(target.report(),measured)
        observations=[json.loads(path.read_bytes()) for path in sorted((folder/'target/observations').glob('*.json'))]
        # A failed mapper can leave one observed but undispatched final sample.
        feedback=[joint_to_servo(__import__('numpy').asarray(row['joint_feedback6_rad'])).tolist() for row in observations[:result['frames']]]
        write(folder/'measured-feedback-view.json',feedback);write(folder/'terminal-score.json',score)
        # RGB shape/backend facts from actual first frame, not an assumed camera.
        from PIL import Image
        frames=sorted((folder/'target/rgb').glob('*.png'))
        if frames:
            with Image.open(frames[0]) as image:write(folder/'render-preflight.json',dict(shape=[image.height,image.width,3],mode=image.mode,context=os.environ['MUJOCO_GL'],GPU_render_only=True,CUDA_optimizer=False,actual_png_sha256=sha(frames[0])))
        return dict(case_id=case['id'],frames=result['frames'],complete_object_success=score['full_object_success'],target_result_sha256=sha(folder/'target/result.json'),failure=result['failure'])
    finally:
        if target is not None:target.env.close()


def dataset(plan,output):
    """CPU-only conversion AFTER every teacher child has exited/reaped."""
    descriptors=[];withheld=[]
    for case in split_cases(plan):
        if case['partition']!='train':
            withheld.append(dict(id=case['id'],partition=case['partition'],teacher_queried=False));
        else:
            trial=output/'teacher'/case['id'];target=trial/'target'
            if not (target/'result.json').is_file() or json.loads((target/'result.json').read_bytes()).get('frames',0)==0:
                withheld.append(dict(id=case['id'],partition='train',reason='NO_COMPLETED_CAUSAL_DISPATCH',raw_attempt_retained=True))
            else:
                descriptor=export_teacher_view(target,output/'dataset/episodes'/case['id'],case,plan['skill_id'],3,json.loads((trial/'terminal-score.json').read_bytes()),plan['settings']['image_size'])
                for name in ('file','canonical_envelope_file','canonical_admission_file'):
                    if name in descriptor:descriptor[name]='episodes/'+case['id']+'/'+descriptor[name]
                if descriptor['admission']['eligible_for_optimizer']:descriptors.append(descriptor)
                else:withheld.append(dict(id=case['id'],partition='train',reason='INDEPENDENT_FULL_OBJECT_TEACHER_NOT_SUCCESSFUL',descriptor=descriptor))
    if not descriptors:raise ValueError('No complete independent target teacher; raw failures retained, no optimizer permitted')
    files={str(path.relative_to(output/'dataset')):sha(path) for path in (output/'dataset/episodes').rglob('*') if path.is_file()}
    manifest=dict(schema='explorer_fresh_target_visual_dataset_v1',skill_id=plan['skill_id'],skill_version=3,action_names=list(ACTION_NAMES),action_units=list(ACTION_UNITS),
        episodes=descriptors,withheld=withheld,split_plan=dict(scenarios=plan['scenarios']),source_files_sha256=files,source_kind='simulation',
        actor_inputs=plan['actor_inputs'],source_template_sha256=plan['source_template_sha256'],learned_source04=False,heldout_teacher_queried=False)
    write(output/'dataset/manifest.json',manifest);return dict(accepted_train=len(descriptors),withheld=withheld,manifest_sha256=sha(output/'dataset/manifest.json'))


def train(plan,output,folder,smoke):
    import numpy as np
    import torch
    from torch.utils.data import default_collate
    from explorer_shared.learning.act_backend import make_act_policy,optimize_act,save_reload_act,preprocessing_config,framework_info
    torch.set_num_threads(2);settings=dict(plan['settings']);settings['act_steps']=plan['smoke_steps'] if smoke else settings['act_steps']
    samples=VisualObjectChunks(output/'dataset/manifest.json',settings['chunk_size'],settings['image_size'])
    policy=make_act_policy(settings,9,9)
    def update(progress,stage):
        print(json.dumps(dict(progress=progress,stage=stage),default=str),flush=True)
    optimizer,losses,started=optimize_act(policy,samples,settings,folder,update,checkpoint_interval=100 if not smoke else 8)
    batch=default_collate([samples[0]]);preprocessing=preprocessing_config(LOW,HIGH)
    preprocessing.update(actor_inputs=plan['actor_inputs'],execution_strategies=plan['strategies'],source_phase_or_truth_in_actor=False,
        valid_channels_omitted_from_model_not_masked=True,state9_literal_preceding_command=True)
    reloaded,checkpoint,reload_error=save_reload_act(policy,optimizer,folder,preprocessing,batch,'cpu')
    files={str(path.relative_to(checkpoint)):sha(path) for path in checkpoint.rglob('*') if path.is_file()}
    result=dict(schema='explorer_fresh_target_ACT9_training_v1',official_ACT=True,settings=settings,framework=framework_info(),checkpoint_files_sha256=files,
        checkpoint_path=str(checkpoint),checkpoint_sha256=sha(checkpoint/'model.safetensors'),dataset_manifest_sha256=sha(output/'dataset/manifest.json'),
        reload_max_abs_error=reload_error,losses=losses,training_samples=len(samples),integration_only=smoke,optimizer_device='cpu',CUDA_used=False,
        heldout_samples_optimized=0,source_kind='simulation',skill_id=plan['skill_id'],target_skill_version=3,learned_source04=False,
        learned_rollout_executed=False,full_task_success=None,physical_io=False)
    write(folder/'training-result.json',result)
    # Both strategy configurations and the SAME checkpoint are frozen before any
    # independent development/sealed rollout. No held-out observations enter it.
    if not smoke:write(output/'paired-policy-freeze.json',dict(checkpoint=result,strategies=plan['strategies'],protocol_source_commit=plan['source_commit'],sealed_policy=plan['sealed_policy']))
    return result


def rollout(plan,case,folder,output,strategy,protocol_sha):
    import numpy as np
    import torch
    from PIL import Image
    from lerobot.policies.act.modeling_act import ACTPolicy
    from explorer_shared.learning.act_backend import IMAGE_KEY,prepare_rgb
    from legacy.simulator.explorer_model import joint_to_servo
    freeze=json.loads((output/'paired-policy-freeze.json').read_bytes());checkpoint=Path(freeze['checkpoint']['checkpoint_path'])
    for name,checksum in freeze['checkpoint']['checkpoint_files_sha256'].items():
        if sha(checkpoint/name)!=checksum:raise ValueError('Frozen learned checkpoint/config changed before paired series')
    if freeze['strategies']!=plan['strategies'] or freeze['checkpoint']['integration_only']:raise ValueError('Meaningful trained paired checkpoint required')
    torch.set_num_threads(2);policy=ACTPolicy.from_pretrained(checkpoint,local_files_only=True,strict=True).to('cpu').eval();policy.reset()
    def predict(image,state):
        batch={IMAGE_KEY:prepare_rgb(image[None]),'observation.state':torch.from_numpy(state[None])}
        with torch.inference_mode():return policy.predict_action_chunk(batch)[0].cpu().numpy()
    declared=next(item for item in plan['strategies'] if item['id']==strategy)
    dispatcher=ChunkDispatcher(predict,execute_steps=declared['execute_steps'],image_size=plan['settings']['image_size'])
    target=None;rows=[];failure=None;initial=dict(status='NOT_RECORDED');score=None
    try:
        target=build_target(plan,case,{'learned_policy':['settled_in_goal']});target.reset(case['seed'])
        # The exact concrete provider/profile captures after reset before any
        # observe/advance. Unknown state/profile must fail before dispatch.
        from explorer_shared.skill_transfer import _capture_initial_state,initial_state_capture_config,load_source_trace
        source=load_source_trace(plan['source_template_file'],plan['source_template_sha256'])
        spec=dict(skill_id=plan['skill_id'],version=3,stages=[dict(id=phase) for phase in PHASES],learned_policy=True)
        capture=initial_state_capture_config(source_bindings=dict(driver=dict(path=str(Path(__file__).resolve()),sha256=sha(__file__))),scenario=dict(case,protocol_sha256=protocol_sha,strategy=strategy,checkpoint_sha256=freeze['checkpoint']['checkpoint_sha256']))
        initial=_capture_initial_state(target,folder,capture,source,target.provider_identity,3,spec,case['seed'],target_split(case))
        for index in range(plan['learned_max_steps']):
            observation=target.observe();image=np.asarray(observation['target_rgb']);stamp=observation['observation_stamp']
            raw,chunk=dispatcher.next(image,observation['preceding_command9'],stamp)
            endpoint,projection=stationary_endpoint(raw,verified_named_base_fixture=target.env.model.equality('manipulation_base_fixture').id>=0)
            requested,rate=target.prepare_dispatch(endpoint)
            frame=io.BytesIO();Image.fromarray(image).save(frame,format='PNG');name='rgb/'+str(index).zfill(6)+'.png';write(folder/name,frame.getvalue())
            native_observation={key:(value.tolist() if isinstance(value,np.ndarray) else value) for key,value in observation.items() if key!='target_rgb'}
            write(folder/'observations'/(str(index).zfill(6)+'.json'),native_observation)
            dispatch=target.advance(requested,'learned_policy')
            if dispatch['finite_dynamics_step_completed'] is not True or dispatch['physical_io'] is not False or dispatch['pre_action_stamp']!=stamp:raise ValueError('Actual same-clock native dispatch required')
            rows.append(dict(index=index,target_rgb_file=name,target_rgb_sha256=hashlib.sha256(frame.getvalue()).hexdigest(),renderer_receipt=observation['renderer_receipt'],
                preceding_command9=observation['preceding_command9'],raw_neural_action9=raw.tolist(),projection=projection,chunk=chunk,rate_preparation=rate,
                actual_requested9=requested,actual_dispatched_action9=dispatch['dispatched_action9'],dispatch=dispatch,learned_target_actor_executed=True,
                teacher_query_executed=False,truth_or_phase_actor_input=False))
            # This evaluator owns termination only. It never changes actor state,
            # actions, chunk cadence or fallback controller.
            # Read the unchanged independent evaluator without repeatedly copying
            # its ever-growing raw substep trace/re-hashing 20 CAD meshes.
            facts,_=target._facts()
            score=complete_object_score(dict(source='target_finite_physics_evaluator',independent_of_action_commands=True,object_facts=facts),joint_to_servo(target.env.data.qpos[target.env.qids]))
            if score['full_object_success']:break
        report=target.report()
    except BaseException as error:
        failure=dict(error_type=type(error).__name__,detail=str(error))
        report=target.report() if target is not None else None
    finally:
        if target is not None:target.env.close()
    write(folder/'learned-trace.json',rows)
    result=dict(schema='explorer_fresh_target_learned_object_rollout_v1',case=case,strategy=strategy,checkpoint_sha256=freeze['checkpoint']['checkpoint_sha256'],
        paired_policy_freeze_sha256=sha(output/'paired-policy-freeze.json'),protocol_sha256=protocol_sha,initial_state=initial,frames=len(rows),failure=failure,
        outcome='SUCCESS' if score and score['full_object_success'] and failure is None else ('FAILURE' if report else 'UNKNOWN'),terminal_score=score,
        independent_evaluation=report,learned_target_actor_executed=bool(rows),teacher_query_count=0,privileged_actor_input=False,source04_learned=False,
        physical_io=False,physical_success_validated=False,raw_trace_sha256=digest(rows),source_kind='simulation',skill_id=plan['skill_id'],skill_version=3)
    write(folder/'result.json',result);return result


def main():
    parser=argparse.ArgumentParser();parser.add_argument('stage',choices=['freeze','teacher','dataset','smoke','train','rollout','summary'])
    for name in ('output','meshes','template','calibration','plan','sha','unit','case','strategy'):parser.add_argument('--'+name)
    args=parser.parse_args()
    if args.stage=='freeze':return freeze(args.output,args.meshes,args.template,args.calibration)
    plan,before=checked_plan(args.plan,args.sha,args.unit);output=Path(args.plan).resolve().parent
    case=next((case for case in split_cases(plan) if case['id']==args.case),None)
    if args.stage=='dataset':folder=output/'conversion'
    elif args.stage in ('smoke','train'):folder=output/args.stage
    elif args.stage=='summary':folder=output/'summary'
    elif args.stage=='teacher':
        if case is None or case['partition']!='train':raise ValueError('Teacher queries allowed only frozen train cases')
        folder=output/'teacher'/case['id']
    else:
        if case is None or case['partition'] not in ('development','sealed_test') or args.strategy not in ('chunk1','chunk8'):raise ValueError('Paired independent evaluation scope required')
        if case['partition']=='sealed_test' and len(list((output/'rollout/development').glob('*/*/result.json')))!=4:raise ValueError('Finish all paired development episodes before opening sealed')
        folder=output/'rollout'/case['partition']/args.strategy/case['id']
    folder.mkdir(mode=0o700,parents=True,exist_ok=False);write(folder/'stage-started.json',dict(stage=args.stage,case=case,strategy=args.strategy,protocol_sha256=args.sha,resources=before,wall_start=time.time(),physical_io=False))
    try:
        if args.stage in ('teacher','rollout'):os.environ['MUJOCO_GL']='egl'
        if args.stage=='teacher':result=teacher(plan,case,folder,args.sha)
        elif args.stage=='dataset':result=dataset(plan,output)
        elif args.stage in ('smoke','train'):result=train(plan,output,folder,args.stage=='smoke')
        elif args.stage=='rollout':result=rollout(plan,case,folder,output,args.strategy,args.sha)
        else:
            results=[json.loads(path.read_bytes()) for path in sorted((output/'rollout').glob('*/*/*/result.json'))]
            result=dict(total=len(results),all_eight_completed=len(results)==8,successes={partition:{strategy:sum(row['outcome']=='SUCCESS' for row in results if row['case']['partition']==partition and row['strategy']==strategy) for strategy in ('chunk1','chunk8')} for partition in ('development','sealed_test')},
                results=results,learned_source04=False,physical_io=False)
        write(folder/'stage-result.json',result);print(json.dumps({key:result[key] for key in result if key not in ('independent_evaluation','losses','results','checkpoint')},default=str),flush=True)
    except BaseException as error:
        write(folder/'stage-error.json',dict(type=type(error).__name__,detail=str(error)));raise
    finally:write(folder/'resources-final.json',resources())


if __name__=='__main__':main()
