"""Read-only CAD math and explicit MOCK target providers, never physical evidence."""
from copy import deepcopy
import json
from types import SimpleNamespace

import numpy as np
import pytest

from explorer_shared.contracts import canonical
from explorer_shared.kinematics import URDFChain
from explorer_shared.skill_transfer import load_source_trace,TaskRelativeMapper,record_target_trial,SCHEMA,_path,_free_grid
from explorer_shared.so101_station import NAMES
from explorer_shared.transfer_mapping04 import SourceCoordinates,TargetCoordinates,TransferBlocked,sha
from explorer_shared.transfer_calibration import fit_aperture_reference,register_task_frames
from test_transfer_mapping04 import SOURCE,TARGET,NATIVE,README,CALIBRATION,encoded


def mapper_fixture(world_from_base=None):
    source=URDFChain.from_urdf(SOURCE,'base_link','gripper_frame_link');target=URDFChain.from_urdf(TARGET,'base_footprint','Gripping')
    raw=encoded(CALIBRATION);coordinates=SourceCoordinates(raw,sha(raw));profile=coordinates.profile(source,README.read_bytes())
    curves={}
    for role,commands,gaps in [('source',[0,50,100],[.002,.03,.06]),('target',[30,100,170],[.06,.03,.002])]:
        views=[dict(role=role,native_gripper=value,aperture_m=gap,uncertainty_m=.001,frame_sha256=str(index)*64,calibration_id=role+'-sim-scale') for index,(value,gap) in enumerate(zip(commands,gaps))]
        curves[role]=fit_aperture_reference(views,role=role,provenance='synthetic')
    source_pose=source.forward([0]*5);target_pose=(np.eye(4) if world_from_base is None else world_from_base)@target.forward([0]*5)
    frames=register_task_frames([dict(role=role,base_from_frame=(source_pose if role.startswith('source') else target_pose).tolist(),frame_sha256=str(index)*64,calibration_id=role+'-sim',uncertainty_m=.001) for index,role in enumerate(['source_object','source_goal','target_object','target_goal'])],provenance='synthetic')
    def collision(start,end,base):return dict(collision_free=True,target_urdf_sha256=target.source_sha256,continuous_segment_checked=True,scene_sha256='f'*64,method='MOCK_ONLY_NOT_PHYSICS')
    return TaskRelativeMapper(source,target,coordinates,TargetCoordinates(NATIVE.read_bytes()),source_profile=profile,source_aperture=curves['source'],target_aperture=curves['target'],task_frames=frames,
        target_low=[0,0,0,0,0,30],target_high=[180,180,180,180,270,170],collision_checker=collision)


def source_trace(tmp_path):
    proof=tmp_path/'scripted-provider.json';proof.write_bytes(canonical(dict(method='SCRIPTED_SIM',closed_loop_learned_source=False)))
    frame=dict(native_action6=[0,0,0,0,0,50],phase_id='place_verify',reference='goal',source_kind='SCRIPTED_SIM')
    document=dict(schema=SCHEMA,experiment_slug='so101_source_learning',source_kind='SCRIPTED_SIM',task_type='pick_object_to_container',skill_id='00000000-0000-4000-8000-000000000001',skill_version=1,
        action_contract_id='so101_follower_calibrated_position6_v1',action_names=list(NAMES),split=dict(partition='train',episode_id='original-1',scene_id='source-scene-1',session_id='source-session-1',group_id='source-group-1'),source_files_sha256={proof.name:sha(proof.read_bytes())},frames=[frame])
    path=tmp_path/'source-trace.json';path.write_bytes(canonical(document));return path,document,frame


def test_original_named_source_FK_maps_task_relative_effect_into_real_target_CAD(tmp_path):
    mapper=mapper_fixture();unused,document,frame=source_trace(tmp_path)
    observation=dict(world_from_base=np.eye(4).tolist(),preceding_command9=[90]*5+[30,0,0,0])
    plan=mapper.plan(frame,observation)
    assert len(plan['target_action9'])==9 and plan['target_action9'][-3:]==[0,0,0]
    assert plan['target_action9'][5]==pytest.approx(100) and not plan['navigation_required']
    assert plan['ik']['reached'] and plan['ik']['position_error_m']<.002
    assert np.allclose(mapper.target.forward(mapper.target_coordinates.radians(plan['target_action9'][:6])),mapper.target.forward([0]*5))
    assert frame['native_action6']==[0,0,0,0,0,50]
    missing=deepcopy(observation);missing['preceding_command9']=[90]*6
    with pytest.raises(TransferBlocked,match='ACTUAL_PRECEDING'):mapper.plan(frame,missing)


def test_observed_Astar_unknown_cells_footprint_and_neutral_arm_preserved(tmp_path):
    free=np.ones((5,5),bool);free[2,:4]=False
    assert (2,4) in _path(free,(0,0),(4,0))
    free[2,4]=False;assert _path(free,(0,0),(4,0)) is None
    cells=np.zeros((6,6),int);cells[3,3]=-1
    grid=dict(cells=cells.tolist(),resolution_m=.1,origin_xy_m=[-.2,-.2],robot_radius_m=.06,frame_id='world',source_id='observed-test-map',robot_geometry_sha256='f'*64)
    inflated,unused,unused=_free_grid(grid)
    assert not inflated[3,2] and not inflated[0,0] and not inflated[3,3]
    mapper=mapper_fixture();unused,document,frame=source_trace(tmp_path)
    # Controlled reachability oracle isolates the real grid/path/controller logic.
    def reachable(tool,base,prior,gripper):
        return None if base[0,3]<.15 else dict(arm_command6=[90]*5+[gripper],ik=dict(reached=True),collision=dict(collision_free=True))
    mapper._solve=reachable;grid['robot_radius_m']=.01
    observation=dict(world_from_base=np.eye(4).tolist(),preceding_command9=[83,85,86,87,88,100,0,0,0],observed_navigation_map=grid)
    plan=mapper.plan(frame,observation)
    assert plan['navigation_required'] and len(plan['navigation_path_world_xy'])>1
    assert plan['target_action9'][:6]==observation['preceding_command9'][:6]
    assert any(abs(value)>0 for value in plan['target_action9'][6:])
    assert np.linalg.norm(plan['target_action9'][6:8])<=.120001
    observation.pop('observed_navigation_map')
    with pytest.raises(TransferBlocked,match='NEEDS_OBSERVED_BASE_NAVIGATION'):mapper.plan(frame,observation)


def test_source_hold_bimanual_and_forged_qualification_cannot_become_object_model(tmp_path):
    path,document,frame=source_trace(tmp_path);raw=path.read_bytes();bundle=load_source_trace(path,sha(raw))
    assert bundle['document']['source_kind']=='SCRIPTED_SIM'
    document['task_type']='hold';path.write_bytes(canonical(document))
    with pytest.raises(TransferBlocked,match='NEW_QUALIFIED_OBJECT'):load_source_trace(path,sha(path.read_bytes()))
    document['task_type']='pick_object_to_container';document['action_names']=list(NAMES)*2;path.write_bytes(canonical(document))
    with pytest.raises(TransferBlocked,match='BIMANUAL'):load_source_trace(path,sha(path.read_bytes()))
    document['action_names']=list(NAMES);document['source_kind']='QUALIFIED_SOURCE_MODEL';document['frames'][0]['source_kind']='QUALIFIED_SOURCE_MODEL'
    document['qualification']=dict(independent_closed_loop_object_success=True,source_model_sha256='a'*64,processor_sha256='b'*64,evaluation_receipt_sha256='c'*64,evaluation_run_ids=['made-up'],raw_episode_replay_only=False)
    path.write_bytes(canonical(document))
    with pytest.raises(TransferBlocked,match='BIND_RETAINED'):load_source_trace(path,sha(path.read_bytes()))


class MockTarget:
    physical_io=False
    provider_identity=dict(kind='MOCK',implementation_sha256='MOCK_NOT_A_PHYSICS_TEST',target_model_sha256='MOCK_NOT_ACTUAL_PHYSICS')
    def __init__(self,*,phase_success=False,render=True,fail=False):
        self.prior=[90]*5+[30,0,0,0];self.phase_success=phase_success;self.render=render;self.fail=fail;self.tick=0
    def reset(self,seed):self.seed=seed
    def observe(self):
        return dict(target_rgb=np.full((12,16,3),100+self.tick,np.uint8),preceding_command9=list(self.prior),world_from_base=np.eye(4).tolist(),
            renderer_receipt=dict(embodiment='ROSMASTER' if self.render else 'SO101',rendered_from_target_physics=self.render,target_model_sha256='MOCK_NOT_ACTUAL_PHYSICS',frame_id=str(self.tick)))
    def advance(self,command,phase_id):
        if self.fail:raise RuntimeError('Injected simulator failure')
        self.prior=list(command);self.tick+=1
        return dict(dispatched_action9=list(command),physical_io=False,finite_dynamics_step_completed=True,receipt_id='MOCK-'+str(self.tick),pre_action_tick=self.tick-1,post_action_tick=self.tick)
    def report(self):
        return dict(source='target_finite_physics_evaluator',independent_of_action_commands=True,end_to_end_success=self.phase_success,
            per_phase=dict(place_verify=dict(success=self.phase_success)),method='MOCK_ONLY_NOT_SCIENCE')


def test_actual_dispatched_endpoint_cannot_override_negative_object_evaluation(tmp_path):
    path,document,frame=source_trace(tmp_path);bundle=load_source_trace(path,sha(path.read_bytes()));mapper=mapper_fixture()
    spec=dict(skill_id=document['skill_id'],version=2,stages=[dict(id='place_verify')])
    result=record_target_trial(bundle,mapper,MockTarget(),tmp_path/'target',target_version=2,target_skill_spec=spec,seed=10)
    assert result['frames']==1 and result['failure'] is None
    assert not result['eligible_as_simulation_expert'] and not result['full_target_simulation_success']
    assert not result['source_model_full_skill_claim'] and result['source_method']=='SCRIPTED_SIM'
    row=json.loads((tmp_path/'target'/'target-trace.json').read_bytes())[0]
    assert len(row['actual_dispatched_action9'])==9 and row['actual_dispatched_action9'][6:]==[0,0,0]
    assert (tmp_path/'target'/row['target_rgb_file']).is_file()
    assert (tmp_path/'target'/'requests'/'000000.json').is_file() and (tmp_path/'target'/'dispatch'/'000000.json').is_file()


def test_source_camera_and_missing_full_phase_cannot_claim_target_success(tmp_path):
    path,document,frame=source_trace(tmp_path);bundle=load_source_trace(path,sha(path.read_bytes()));mapper=mapper_fixture()
    spec=dict(skill_id=document['skill_id'],version=2,stages=[dict(id='place_verify'),dict(id='grasp_confirm')])
    result=record_target_trial(bundle,mapper,MockTarget(phase_success=True),tmp_path/'incomplete',target_version=2,target_skill_spec=spec,seed=10)
    assert not result['eligible_as_simulation_expert']
    result=record_target_trial(bundle,mapper,MockTarget(render=False),tmp_path/'source-camera',target_version=2,target_skill_spec=spec,seed=10)
    assert result['failure']['code']=='FRESH_TARGET_ROSMASTER_RENDER_REQUIRED'
    assert not result['full_target_simulation_success'] and not result['physical_success_validated']


def test_target_provider_failure_keeps_pre_dispatch_request_and_target_image(tmp_path):
    path,document,frame=source_trace(tmp_path);bundle=load_source_trace(path,sha(path.read_bytes()));mapper=mapper_fixture()
    spec=dict(skill_id=document['skill_id'],version=2,stages=[dict(id='place_verify')])
    result=record_target_trial(bundle,mapper,MockTarget(fail=True),tmp_path/'failed',target_version=2,target_skill_spec=spec,seed=10)
    assert result['failure']['code']=='TARGET_PROVIDER_FAILURE' and result['frames']==0
    assert (tmp_path/'failed'/'requests'/'000000.json').is_file() and (tmp_path/'failed'/'rgb'/'000000.png').is_file()
    with pytest.raises(TransferBlocked,match='NEW_IMMUTABLE'):
        record_target_trial(bundle,mapper,MockTarget(),tmp_path/'failed',target_version=2,target_skill_spec=spec,seed=10)


def test_source_RGB_pixel_reuse_is_rejected_despite_target_named_receipt(tmp_path):
    from PIL import Image
    path,document,frame=source_trace(tmp_path)
    image=tmp_path/'source.png';Image.fromarray(np.full((12,16,3),100,np.uint8)).save(image)
    document['source_files_sha256'][image.name]=sha(image.read_bytes());document['frames'][0]['source_rgb_file']=image.name
    path.write_bytes(canonical(document));bundle=load_source_trace(path,sha(path.read_bytes()))
    result=record_target_trial(bundle,mapper_fixture(),MockTarget(phase_success=True),tmp_path/'reused',target_version=2,
        target_skill_spec=dict(skill_id=document['skill_id'],version=2,stages=[dict(id='place_verify')]),seed=1)
    assert result['failure']['code']=='SOURCE_RGB_PIXELS_CANNOT_SUBSTITUTE_TARGET_RENDER'
    assert not result['eligible_as_simulation_expert']
