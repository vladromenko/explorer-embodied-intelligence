"""Full-task criteria, actual flex contact, bounded requests and native replay."""
import json
import os
from pathlib import Path
import sys
import xml.etree.ElementTree as ET

import mujoco
import numpy as np
import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from explorer_sim.manipulation_env import FullTaskEvaluator, ManipulationEnv, TASKS, STATE_SPEC
from explorer_sim.manipulation import parameters, record_rollout, replay_trajectory, run_manipulation, raw_policy_action


def points(x=0.,y=0.,z=.01):
    return np.array([[x+dx,y+dy,z+dz] for dx in (-.01,.01) for dy in (-.01,.01) for dz in (0,.01)])


def tick(evaluator,positions,forces=(1,1),support=0.,opened=False,count=60):
    for _ in range(count):
        evaluator.observe(positions,np.zeros(len(positions)),forces,support,opened)


def test_first_grasp_or_lift_is_never_full_success():
    evaluator = FullTaskEvaluator(points(),[.2,0,0],[.07,.06],.05,.002)
    tick(evaluator,points())
    assert evaluator.grasped and not evaluator.success
    tick(evaluator,points(z=.10),count=120)
    assert evaluator.lifted and not evaluator.success
    tick(evaluator,points(.2,0,.01),forces=(0,0),support=1,opened=True,count=300)
    assert not evaluator.success  # No carried hold above target rim.


@pytest.mark.parametrize('defect',['one_vertex_outside','finger_contact','unsupported','not_open','still_moving'])
def test_release_gate_rejects_incomplete_placement(defect):
    evaluator = FullTaskEvaluator(points(),[.2,0,0],[.07,.06],.05,.002)
    tick(evaluator,points());tick(evaluator,points(z=.10),count=120)
    tick(evaluator,points(.2,0,.10),count=10)
    positions=points(.2,0,.01); forces=[0,0];support=1;opened=True;speeds=np.zeros(8)
    if defect=='one_vertex_outside':positions[0,0]=.3
    if defect=='finger_contact':forces=[.01,0]
    if defect=='unsupported':support=0
    if defect=='not_open':opened=False
    if defect=='still_moving':speeds[0]=.5
    for _ in range(300):evaluator.observe(positions,speeds,forces,support,opened)
    assert evaluator.carried and not evaluator.success


def test_complete_sequence_requires_actual_release_and_settling():
    evaluator=FullTaskEvaluator(points(),[.2,0,0],[.07,.06],.05,.002)
    tick(evaluator,points());tick(evaluator,points(z=.10),count=120)
    tick(evaluator,points(.2,0,.10),count=10)
    tick(evaluator,points(.2,0,.01),forces=(0,0),support=1,opened=True,count=249)
    assert not evaluator.success
    tick(evaluator,points(.2,0,.01),forces=(0,0),support=1,opened=True,count=2)
    assert evaluator.success


def test_invalid_training_parameters_do_not_silently_coerce():
    for config in [{'task':'cookie_delivery'},{'device':'cuda'},{'teacher_steps':True},{'horizon':1}]:
        with pytest.raises(ValueError):parameters(config)


@pytest.fixture
def cad_meshes():
    folder=os.environ.get('EXPLORER_SIM_MESH_DIR')
    if not folder:pytest.skip('Actual checksum-verified CAD assets required')
    return folder


@pytest.mark.parametrize('task',TASKS)
def test_actual_model_contact_topology_and_raw_request_saturation(cad_meshes,task):
    env=ManipulationEnv(cad_meshes,task,horizon=32)
    try:
        assert env.model.nmesh==20 and env.model.nu==10
        root=ET.fromstring(env.xml)
        assert len(root.findall('./equality/weld'))==1
        assert root.find('./equality/weld').get('body1')=='base_link'
        assert root.find('.//flexcomp/pin') is None
        if task==TASKS[1]:
            assert env.model.nflex==1 and env.model.nflexvert==57 and env.model.nflexelem==104
            assert env.model.nq==241 and len(env.points())==57
        else:
            assert env.model.nflex==0 and env.model.nq==77
            assert env.model.body('candy').mass[0]==pytest.approx(.0126)
        before=env.command.copy();native=env.integration_state()
        env.record_substeps=True
        env.step(np.full(6,4.))
        assert env.raw_action.tolist()==[4.]*6
        np.testing.assert_allclose(env.requested,before+4*env.velocity*env.period)
        assert np.all(abs(env.command-before)<=env.velocity*env.period+1e-9)
        assert len(env.substep_trace)==25 and env.evaluator.substeps==25
        assert not np.array_equal(native,env.integration_state())
        assert env.observation().shape==(36,)
        with pytest.raises(ValueError):env.step([np.nan]*6)
    finally:env.close()


def test_actual_rigid_rotation_is_included_in_settling_velocity(cad_meshes):
    env=ManipulationEnv(cad_meshes,TASKS[0])
    try:
        adr=env.model.joint('candy_free').dofadr[0]
        env.data.qvel[adr+3:adr+6]=[0,0,10]
        mujoco.mj_forward(env.model,env.data)
        assert env.speeds().max()>.1
    finally:env.close()


def test_raw_native_replay_reexecutes_actions_and_exposes_tampering(cad_meshes,tmp_path):
    env=ManipulationEnv(cad_meshes,TASKS[0],horizon=32)
    class Policy:
        def predict(self,observation,deterministic=True):return np.array([.1,-.1,0,.1,0,.3]),None
    try:
        record_rollout(env,tmp_path/'episode',321051,Policy())
        result=replay_trajectory(env,tmp_path/'episode')
        assert result['actions_reexecuted']==32 and result['max_abs_native_state_error']==0
        data=dict(np.load(tmp_path/'episode/trajectory.npz'))
        assert len(data['native_integration_state'])==33
        assert len(data['substep_contact_trace'])==800
        assert data['rgb'].shape==(32,240,320,3)
        data['requested_endpoint6'][0,0]+=1.
        np.savez_compressed(tmp_path/'episode/trajectory.npz',**data)
        assert replay_trajectory(env,tmp_path/'episode')['max_abs_native_state_error']>1e-4
    finally:env.close()


def test_sb3_raw_network_output_survives_its_internal_predict_clipping(cad_meshes):
    from stable_baselines3 import PPO
    import torch
    env=ManipulationEnv(cad_meshes,TASKS[0],horizon=32)
    try:
        policy=PPO('MlpPolicy',env,n_steps=8,batch_size=8,n_epochs=1,device='cpu',seed=19)
        with torch.no_grad():
            policy.policy.action_net.weight.zero_()
            policy.policy.action_net.bias.fill_(4.)
        observation=env.observation()
        np.testing.assert_allclose(policy.predict(observation,deterministic=True)[0],1.)
        np.testing.assert_allclose(raw_policy_action(policy,observation),4.)
    finally:env.close()


def test_bad_job_identity_cannot_write_outside_job_directory(tmp_path):
    class Store:
        root=tmp_path
    with pytest.raises(ValueError,match='job ID'):
        run_manipulation(Store(),{'id':'../outside','config':{}})
    assert not (tmp_path/'outside').exists()
