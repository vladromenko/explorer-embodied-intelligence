"""Finite CAD mobile control, complete-effect gates, native replay and corruption."""
import json
import os
from pathlib import Path
import xml.etree.ElementTree as ET

import numpy as np
import pytest

from explorer_sim.mobile_manipulation_env import MobileManipulationEnv, WholeMobileEvaluator
from explorer_sim.mobile_manipulation import parameters, record_mobile_rollout, replay_mobile_trajectory
from explorer_sim.pilot import sha
from legacy.simulator.explorer_model import wheel_targets


@pytest.fixture
def env():
    folder=os.environ.get('EXPLORER_SIM_MESH_DIR')
    if not folder:pytest.skip('Actual checksum-verified CAD mesh assets required')
    value=MobileManipulationEnv(folder,horizon=160)
    try:yield value
    finally:value.close()


class ObjectGateFixture:
    success=True
    lifted=True
    last={'pad_force_N':[1.,1.]}
    def result(self):return {'success':True,'software_fixture_only':True}


def test_object_placement_alone_is_not_complete_mobile_success():
    scorer=WholeMobileEvaluator(ObjectGateFixture(),[0.,0.,0.],[1.,0.,.01])
    scorer.observe([0.,0.,0.],0.,1.,0.)
    outcome=scorer.result()
    assert not outcome['success']
    assert not outcome['conditions']['outbound_door_crossed']
    assert not outcome['conditions']['navigate_to_source']
    assert not outcome['conditions']['base_carried_held_object_half_metre']


def test_full_mobile_gate_requires_both_directions_held_transport_and_no_collision():
    scorer=WholeMobileEvaluator(ObjectGateFixture(),[0.,0.,0.],[1.,0.,.01])
    for point in ([0.,0.,0.],[.66,0.,0.],[.80,0.,0.],[.30,0.,0.],[-.1,.24,0.]):
        scorer.observe(point,0.,1.,0.)
    assert scorer.result()['success']
    scorer.observe([-.1,.24,0.],0.,1.,2.)
    assert not scorer.result()['success']
    assert scorer.result()['kitchen_collision_substeps']==1


@pytest.mark.parametrize('value',[{'horizon':True},{'evaluation_episodes':0},{'frame_stride':0},{'device':'cuda'},{'carry_profile':'magic_grasp'}])
def test_invalid_mobile_protocol_parameters_are_rejected(value):
    with pytest.raises(ValueError):parameters(value)


def test_real_model_keeps_free_base_candy_rollers_and_finite_actuators(env):
    xml=ET.fromstring(env.xml)
    assert xml.find(".//body[@name='base_link']/freejoint[@name='base_free']") is not None
    assert xml.find(".//body[@name='candy']/freejoint[@name='candy_free']") is not None
    assert xml.findall('./equality/weld')==[]
    assert env.model.nmesh==20 and env.model.nu==10
    assert sum('_roller' in env.model.joint(i).name for i in range(env.model.njnt))==48
    assert len(env.kitchen_geom_ids)==7
    assert env.model.geom('candy_contact').size.tolist()==pytest.approx([.020,.012,.010])
    assert env.model.geom('candy_wrapper_-1').type[0]==mujoco_ellipsoid_type()
    assert np.all(np.isfinite(env.model.actuator_forcerange))
    assert np.all(env.model.actuator_forcerange[env.wheel_ids,1]==.25)


def mujoco_ellipsoid_type():
    import mujoco
    return int(mujoco.mjtGeom.mjGEOM_ELLIPSOID)


def test_wheel_commands_move_actual_base_by_contact_without_claiming_full_task(env):
    initial=env.base_pose().copy();native=env.integration_state()
    command=np.r_[env.command[:6],.1,0.,0.]
    env.step_command(command)
    assert env.command[6]==pytest.approx(.025)
    np.testing.assert_allclose(env.data.ctrl[env.wheel_ids],wheel_targets(*env.command[6:]))
    for _ in range(59):env.step_command(command)
    displacement=env.base_pose()-initial
    assert .20<displacement[0]<.35 and abs(displacement[1])<.02
    assert np.max(abs(env.data.qvel[env.wheel_dofs]))>1.
    assert not np.array_equal(native,env.integration_state())
    assert not env.mobile_evaluator.result()['success']
    assert env.mobile_evaluator.result()['kitchen_collision_substeps']==0
    with pytest.raises(ValueError):env.step_command(np.r_[env.command[:6],.21,0.,0.])


def test_native_mobile_replay_reexecutes_wheel_requests_and_detects_changed_action(env,tmp_path,monkeypatch):
    import explorer_sim.mobile_manipulation as module
    def finite_fixture(current,profile):
        for _ in range(40):yield np.r_[current.command[:6],.10,0.,0.]
        for _ in range(20):yield np.r_[current.command[:6],0.,0.,0.]
    monkeypatch.setattr(module,'scripted_mobile_actions',finite_fixture)
    folder=tmp_path/'finite_navigation_fixture_not_full_task'
    result=record_mobile_rollout(env,folder,641099,frame_stride=20)
    replay=replay_mobile_trajectory(env,folder)
    assert result['n_actions']==replay['actions_reexecuted']==60
    assert replay['maximum_native_state_error']==0.0
    assert not result['evaluator']['success']
    with np.load(folder/'trajectory.npz',allow_pickle=False) as source:
        data={key:source[key].copy() for key in source.files}
    assert len(data['mobile_contact_substeps'])==1500
    assert len(data['object_contact_substeps'])==1500
    assert data['state_command9'].shape==(60,9)
    assert len(data['native_integration_state'])==61
    np.testing.assert_allclose(data['state_command9'][1:],data['dispatched_endpoint9'][:-1])
    assert data['rgb_action_indices'].tolist()==[0,20,40]
    data['raw_requested_endpoint9'][0,6]=-.1
    np.savez_compressed(folder/'trajectory.npz',**data)
    with pytest.raises(ValueError,match='SHA'):replay_mobile_trajectory(env,folder)
    # Explicitly author a changed fixture request, keeping old native target states.
    result['trajectory_sha256']=sha(folder/'trajectory.npz');(folder/'result.json').write_text(json.dumps(result))
    assert replay_mobile_trajectory(env,folder)['maximum_native_state_error']>1e-4


def test_regrasp_does_not_credit_unheld_chassis_travel():
    object_gate=ObjectGateFixture()
    scorer=WholeMobileEvaluator(object_gate,[.8,0.,0.],[1.,0.,.01])
    scorer.observe([.8,0.,0.],0.,1.,0.)
    scorer.observe([.65,0.,0.],0.,1.,0.)
    object_gate.last={'pad_force_N':[0.,0.]}
    scorer.observe([.1,0.,0.],0.,1.,0.)
    object_gate.last={'pad_force_N':[1.,1.]}
    scorer.observe([.1,0.,0.],0.,1.,0.)
    assert scorer.result()['carried_base_distance_m']==pytest.approx(.15)
    assert not scorer.result()['conditions']['base_carried_held_object_half_metre']
