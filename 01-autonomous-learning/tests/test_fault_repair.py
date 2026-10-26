"""Identifiability, informative probes and actual no-fault executor parity."""
import copy
import os
from pathlib import Path

import mujoco
import numpy as np
import pytest

from explorer_sim.fault_repair import FaultSpec, FaultedReach, choose_probe, diagnose, execute_trial, run_fault_repair, settings
from explorer_sim.pilot import HOME, ReachEnv


def measurements(*, delay=0, bias=(0.,0.,0.), frame_bias=(0.,0.,0.), drift=(0.,0.,0.), feedback=True):
    clock = np.arange(90)*.08
    nominal_joint = np.column_stack([90+10*np.sin(clock*frequency) for frequency in (2.1,2.7,3.2)])
    observed = np.vstack((np.repeat(nominal_joint[:1],delay,axis=0), nominal_joint[:len(clock)-delay])) if delay else nominal_joint.copy()
    observed += bias
    # A synthetic encoder-FK stream for diagnosis invariants, not a CAD test.
    fk = observed*.001
    measured_pose = fk+np.asarray(frame_bias)+clock[:,None]*drift
    expected = dict(joint_sensor_deg=nominal_joint, pose_sensor_xyz_m=nominal_joint*.001,
        fk_from_joint_sensor_xyz_m=nominal_joint*.001,elapsed_sim_s=clock,requested_command3=nominal_joint)
    actual = dict(joint_sensor_deg=observed if feedback else np.full_like(observed,np.nan),
        pose_sensor_xyz_m=measured_pose, fk_from_joint_sensor_xyz_m=fk if feedback else np.full_like(fk,np.nan), elapsed_sim_s=clock,requested_command3=nominal_joint)
    return actual, expected


def test_diagnosis_estimates_joint_bias_and_delay_without_hidden_injection_fields():
    actual, nominal = measurements(delay=3,bias=(5.,-4.,2.))
    result = diagnose(actual,nominal)
    assert result['actuator_delay_ticks'] == 3
    np.testing.assert_allclose(result['actuator_bias_deg'],[5,-4,2],atol=1e-8)
    assert {'actuator_bias','actuator_delay'}.issubset(result['causes'])
    poisoned = dict(actual, injected_fault='wrong-secret-label', audit_only_effective_actuator_target3=np.zeros_like(actual['joint_sensor_deg']))
    assert diagnose(poisoned,nominal) == result
    assert not result['hidden_injection_fields_consumed']


def test_frame_bias_and_drift_are_separated_using_independent_encoder_fk():
    actual, nominal = measurements(frame_bias=(.02,-.01,.015),drift=(.002,0,0))
    result = diagnose(actual,nominal)
    assert 'actuator_bias' not in result['causes']
    assert {'camera_frame_bias','sensor_drift'}.issubset(result['causes'])
    np.testing.assert_allclose(result['pose_bias_m'],[.02,-.01,.015],atol=1e-8)
    np.testing.assert_allclose(result['pose_drift_m_s'],[.002,0,0],atol=1e-8)


def test_confounding_sensor_and_actuator_error_abstains_without_joint_feedback():
    actual, nominal = measurements(frame_bias=(.03,0,0),feedback=False)
    result = diagnose(actual,nominal)
    assert result['status'] == 'AMBIGUOUS'
    assert not result['repair_authorized']
    assert len(result['competing_causes']) >= 2
    assert result['actuator_bias_deg'] == [0,0,0]
    assert result['pose_bias_m'] == [0,0,0]


def test_sensor_dropout_is_not_filled_with_future_or_nominal_data():
    actual, nominal = measurements()
    actual['pose_sensor_xyz_m'][::3] = np.nan
    result = diagnose(actual,nominal)
    assert result['measurement_coverage']['pose_dropped'] == 30
    assert 'sensor_dropout' in result['causes']
    assert np.isnan(actual['pose_sensor_xyz_m'][::3]).all()


def test_informative_probe_outscores_a_static_hold_for_delay_identification():
    selected, commands = choose_probe()
    assert selected['selected'] == 'bidirectional_joint_pulses'
    assert selected['scores'][selected['selected']] > selected['scores']['stationary_hold']
    assert commands.shape == (64,3) and np.ptp(commands,axis=0).min() > 0


def test_bad_measurement_chronology_and_unbounded_configs_are_rejected():
    actual, nominal = measurements()
    actual['elapsed_sim_s'][3] = -1
    with pytest.raises(ValueError,match='chronology'):diagnose(actual,nominal)
    for config in ({'evaluation_episodes':True},{'scenarios':['unknown']},{'horizon':1},{'device':'cuda'}):
        with pytest.raises(ValueError):settings(config)
    with pytest.raises(ValueError):FaultSpec(actuator_delay_ticks=-1)


@pytest.fixture
def meshes():
    path = os.environ.get('EXPLORER_SIM_MESH_DIR')
    if not path:
        pytest.skip('Checksum-qualified CAD assets explicitly required')
    return path


def test_fault_free_wrapper_matches_real_reach_executor_native_state(meshes):
    plant = FaultedReach(meshes)
    original = ReachEnv(meshes)
    try:
        plant.reset(441);original.reset(seed=441)
        for value in ([91,88,93],[94,86,96],[90,91,88],[87,92,86]):
            request = HOME.copy();request[:3] = value
            plant.step(request);original.step_command(request)
            np.testing.assert_array_equal(plant.env.data.qpos,original.data.qpos)
            np.testing.assert_array_equal(plant.env.data.qvel,original.data.qvel)
            np.testing.assert_array_equal(plant.env.data.ctrl,original.data.ctrl)
    finally:
        plant.close();original.close()


def test_ambiguous_diagnosis_cannot_apply_a_simulation_repair(meshes):
    plant = FaultedReach(meshes)
    actual, nominal = measurements(feedback=False,frame_bias=(.02,0,0))
    diagnosis = diagnose(actual,nominal)
    fault = FaultSpec(pose_bias_m=(.02,0,0),joint_feedback_available=False)
    try:
        before = execute_trial(plant,941,fault,diagnosis,repair=False,horizon=24)
        after = execute_trial(plant,941,fault,diagnosis,repair=True,horizon=24)
        assert not after['repair_applied']
        assert before['distance_m'] == after['distance_m']
        assert before['rows'] == after['rows']
    finally:plant.close()


def test_fault_job_identity_cannot_escape_artifact_storage(tmp_path):
    class Store:
        root = tmp_path
    with pytest.raises(ValueError,match='identity'):
        run_fault_repair(Store(),{'id':'../escape','config':{}})
    assert not (tmp_path.parent/'escape').exists()


def test_transient_reach_that_leaves_goal_does_not_pass_final_task_predicate():
    from types import SimpleNamespace
    class Plant:
        def __init__(self):
            self.env = SimpleNamespace(period=.08,goal=np.zeros(3),distance=lambda:.03,
                data=SimpleNamespace(qpos=np.zeros(3),warning=[]))
        def reset(self,seed,fault):
            self.steps,self.hold,self.success=0,0,False
        def target_measurement(self):return np.zeros(3)
        def geometry(self,command):return np.zeros(3)
        def step(self,requested):
            self.steps += 1
            self.hold = 3 if self.steps == 10 else 0
            self.success |= self.hold >= 3
            return dict(elapsed_sim_s=self.steps*.08)
    result = execute_trial(Plant(),31,FaultSpec(),dict(repair_authorized=False),repair=False,horizon=24)
    assert result['first_success_s'] is not None
    assert not result['success'] and result['final_hold_steps'] == 0


def test_completed_job_identity_binds_cad_assets_and_engine_version(tmp_path,monkeypatch):
    import json
    import explorer_sim.fault_repair as module
    from types import SimpleNamespace
    assets = tmp_path/'assets';assets.mkdir()
    mesh = assets/'software-fixture.STL';mesh.write_bytes(b'explicit-software-cache-fixture')
    monkeypatch.setattr(module,'resolve_mesh_dir',lambda *_:assets)
    job = dict(id='completed-fixture',config={})
    folder = tmp_path/'jobs'/job['id'];folder.mkdir(parents=True)
    original = module.execution_identity(module.settings({}),assets)
    saved = dict(request_signature=original['request_signature'],artifact_sha256={})
    (folder/'fault-repair-result.json').write_text(json.dumps(saved))
    store = SimpleNamespace(root=tmp_path)
    assert module.run_fault_repair(store,job) == saved
    mesh.write_bytes(b'changed-CAD-must-not-reuse-completed-result')
    with pytest.raises(ValueError,match='source or settings differ'):
        module.run_fault_repair(store,job)
    mesh.write_bytes(b'explicit-software-cache-fixture')
    version = module.importlib.metadata.version
    monkeypatch.setattr(module.importlib.metadata,'version',lambda name:'different-MuJoCo-engine' if name=='mujoco' else version(name))
    with pytest.raises(ValueError,match='source or settings differ'):
        module.run_fault_repair(store,job)


def test_actual_entry_keeps_job_identity_and_checks_completed_raw_artifacts(meshes,tmp_path):
    from types import SimpleNamespace
    calls = []
    def update(job_id,**body):
        assert job_id == 'actual-finite-run'
        calls.append(body)
    store = SimpleNamespace(root=tmp_path,update_job=update)
    job = dict(id='actual-finite-run',config=dict(scenarios=['nominal'],evaluation_episodes=1,horizon=24,mesh_dir=meshes))
    result = run_fault_repair(store,job)
    assert calls and result['versions']['mujoco']
    assert result['cad_mesh_sha256'] and result['cases'][0]['diagnosis']['causes'] == ['nominal_within_probe_resolution']
    assert run_fault_repair(store,job) == result
    artifact = Path(result['actual_attempt'])/'nominal/probe-measurements.npz'
    artifact.write_bytes(b'changed-raw-measurements')
    with pytest.raises(ValueError,match='artifact checksum'):
        run_fault_repair(store,job)
