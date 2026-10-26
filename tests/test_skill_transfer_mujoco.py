"""Actual finite CAD target physics/rendering, no optimizer or hardware IO."""
import json
from pathlib import Path

import numpy as np

from explorer_shared.contracts import canonical
from explorer_shared.skill_transfer import load_source_trace,record_target_trial
from explorer_shared.skill_transfer_mujoco import MuJoCoTargetProvider
from explorer_shared.transfer_mapping04 import sha
from test_transfer_mapping04 import TARGET,ROOT
from test_skill_transfer import mapper_fixture,source_trace


def mesh_inventory(mesh):
    hashes=json.loads((ROOT/'01-autonomous-learning/src/legacy/config/arm_model.json').read_text())['asset_sha256']
    return {mesh/Path(name).name:expected for name,expected in hashes.items() if name.endswith('.STL')}


def test_actual_ROSMASTER_render_and_finite_target9_are_retained_but_not_object_success(tmp_path):
    from explorer_sim.manipulation_env import ManipulationEnv
    from legacy.simulator.explorer_model import resolve_mesh_dir
    mesh=resolve_mesh_dir();env=ManipulationEnv(mesh,'candy_to_tray',horizon=10)
    try:
        provider=MuJoCoTargetProvider(env,target_urdf_sha256=sha(TARGET.read_bytes()),mesh_files=mesh_inventory(mesh),phase_evidence_map={'place_verify':['success']})
        path,document,frame=source_trace(tmp_path);document['frames'][0]['native_action6'][5]=100;path.write_bytes(canonical(document));bundle=load_source_trace(path,sha(path.read_bytes()))
        mapper=mapper_fixture(provider._base());mapper.collision_checker=provider.collision_check
        result=record_target_trial(bundle,mapper,provider,tmp_path/'actual-target',target_version=2,
            target_skill_spec=dict(skill_id=document['skill_id'],version=2,stages=[dict(id='place_verify')]),seed=441,max_steps=6)
        assert result['failure'] is None,result['failure']
        assert result['frames']==1 and not result['software_contract_only']
        assert result['target_provider']['kind']=='MUJOCO_CAD'
        assert result['independent_evaluation']['finite_step_count']==1
        assert not result['eligible_as_simulation_expert'] and not result['full_target_simulation_success']
        rows=json.loads((tmp_path/'actual-target'/'target-trace.json').read_text());row=rows[0]
        assert len(row['actual_dispatched_action9'])==9 and row['actual_dispatched_action9'][-3:]==[0.,0.,0.]
        assert row['dispatch']['dispatch_stamp']['monotonic_ns']>row['dispatch']['pre_action_stamp']['monotonic_ns']
        assert row['renderer_receipt']['stamp']['monotonic_ns']<=row['dispatch']['pre_action_stamp']['monotonic_ns']
        assert row['plan']['collision']['sampled_segment_checked'] and not row['plan']['collision']['continuous_segment_checked']
        assert len(row['dispatch']['native_integration_state'])>9
        assert len(result['independent_evaluation']['substep_contact_trace'])>1
        assert not result['physical_success_validated'] and result['source_method']=='SCRIPTED_SIM'
    finally:env.close()


def test_actual_mobile_target_provider_preserves9D_and_drives_finite_wheels_only_in_SIM(tmp_path):
    from explorer_sim.mobile_manipulation_env import MobileManipulationEnv
    from legacy.simulator.explorer_model import resolve_mesh_dir
    mesh=resolve_mesh_dir();env=MobileManipulationEnv(mesh,horizon=30)
    try:
        provider=MuJoCoTargetProvider(env,target_urdf_sha256=sha(TARGET.read_bytes()),mesh_files=mesh_inventory(mesh),phase_evidence_map={'navigate':['navigate_to_source']})
        provider.reset(seed=991);initial=provider.observe();before=env.base_pose().copy()
        rows=[]
        for _ in range(16):
            last=provider.advance(initial['preceding_command9'][:6]+[.1,0.,0.],phase_id='navigate');rows.append(last)
        after=env.base_pose();image=provider.observe()
        assert np.linalg.norm(after[:2]-before[:2])>.01
        assert len(last['dispatched_action9'])==9 and last['dispatched_action9'][6]>.05
        assert provider.provider_identity['base_motion_backend']=='actual_finite_wheel_dynamics'
        assert not np.array_equal(image['target_rgb'],initial['target_rgb'])
        assert not provider.report()['end_to_end_success']
        assert last['finite_dynamics_step_completed'] and not last['physical_io']
        from PIL import Image
        Image.fromarray(initial['target_rgb']).save(tmp_path/'before.png');Image.fromarray(image['target_rgb']).save(tmp_path/'after.png')
        (tmp_path/'wheel-trace.json').write_bytes(canonical(dict(schema='explorer_transfer_target_wheel_qualification_v1',provider_identity=provider.provider_identity,
            initial_base_pose=before.tolist(),final_base_pose=after.tolist(),base_displacement_m=float(np.linalg.norm(after[:2]-before[:2])),
            dispatches=rows,independent_report=provider.report(),learned_policy_executed=False,physical_io=False)))
    finally:env.close()


def test_late_independent_outcome_does_not_relabel_a_missed_earlier_phase():
    # Pure phase protocol regression. This fake is never used as physics proof.
    from types import SimpleNamespace
    from explorer_shared.transfer_mapping04 import TransferBlocked
    import pytest
    flag=[False]
    provider=MuJoCoTargetProvider.__new__(MuJoCoTargetProvider)
    data=SimpleNamespace(time=0.,qpos=np.zeros(1),qvel=np.zeros(1))
    env=SimpleNamespace(data=data,command=np.zeros(9),steps=0,substep_trace=[])
    def step(command):env.steps+=1;data.time+=.05;env.command=np.asarray(command)
    env.step_command=step;env.integration_state=lambda:np.r_[data.time,env.command]
    env.evaluator=SimpleNamespace(result=lambda:dict(grasped=flag[0],success=False))
    provider.env=env;provider.sequence=0;provider.boot='MOCK-only';provider.phase_results={}
    provider.phase_evidence_map={'early_grasp':['grasped'],'later_grasp':['grasped']}
    provider._facts=lambda:(dict(grasped=flag[0],success=False),None);provider.recheck=lambda:None
    provider.advance([0]*9,'early_grasp');flag[0]=True
    provider.advance([0]*9,'later_grasp');report=provider.report()
    assert not report['per_phase']['early_grasp']['success']
    assert report['per_phase']['later_grasp']['success'] and not report['end_to_end_success']
    before=env.steps
    with pytest.raises(TransferBlocked,match='PHASE_CRITERIA_NOT_DECLARED'):provider.advance([0]*9,'unknown')
    assert env.steps==before


def test_virtual_dispatch_prefix_matches_checked_segment_without_changing_original_rates():
    from types import SimpleNamespace
    provider=MuJoCoTargetProvider.__new__(MuJoCoTargetProvider)
    current=np.array([90,90,90,90,90,30,0,0,0.],float);velocity=np.array([60,70,80,90,90,72.])
    provider.env=SimpleNamespace(command=current.copy(),velocity=velocity.copy(),period=.05)
    requested=current+np.array([40,1,-10,15,20,140,.05,0,0])
    actual,receipt=provider.prepare_dispatch(requested);actual=np.asarray(actual)
    difference=requested[:6]-current[:6]
    assert np.allclose((actual[:6]-current[:6])/difference,receipt['fraction'])
    independently_limited=current[:6]+np.clip(difference,-velocity*.05,velocity*.05)
    assert not np.allclose((independently_limited-current[:6])/difference,receipt['fraction'])
    assert np.allclose(current[:6]+np.clip(actual[:6]-current[:6],-velocity*.05,velocity*.05),actual[:6])
    assert np.array_equal(provider.env.velocity,velocity) and provider.env.period==.05
    assert actual[-3:].tolist()==requested[-3:].tolist() and not receipt['controller_settings_modified']


def test_past_latched_goal_success_cannot_hide_object_lost_before_full_trial_end():
    # Pure protocol test, not physics evidence. A real evaluator may latch an
    # earlier 0.5s success; transfer must also verify terminal goal occupancy.
    from types import SimpleNamespace
    provider=MuJoCoTargetProvider.__new__(MuJoCoTargetProvider)
    provider.env=SimpleNamespace(substep_trace=[],steps=10,data=SimpleNamespace(time=.5))
    provider.phase_evidence_map={'settle':['success','settled_in_goal'],'retreat':['withdrawn']};provider.phase_results={}
    provider.recheck=lambda:None;facts={'success':True,'settled_in_goal':False,'withdrawn':True}
    provider._facts=lambda:(facts,None)
    rejected=provider.report();assert rejected['latched_historical_success'] and not rejected['end_to_end_success']
    facts['settled_in_goal']=True;assert provider.report()['end_to_end_success']
    facts['withdrawn']=False;assert not provider.report()['end_to_end_success']
