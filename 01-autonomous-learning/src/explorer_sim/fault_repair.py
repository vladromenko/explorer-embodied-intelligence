"""ENGINEERING_GLUE / EXPLORER_CAD_MUJOCO: controlled fault diagnosis pilot.

Real CAD MuJoCo dynamics, measured simulation sensors and nominal interventions.
Diagnosis consumes only probe measurements; fault declarations are audit-only.
Finite reach repairs are engineering development evidence, not H4 or hardware.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import fcntl
import hashlib
import importlib.metadata
import json
from pathlib import Path
import time
import uuid

import mujoco
import numpy as np

from legacy.simulator.explorer_model import joint_to_servo, resolve_mesh_dir, servo_to_joint
from .pilot import HOME, ReachEnv, sha, utc, write_json


@dataclass(frozen=True)
class FaultSpec:
    actuator_bias_deg: tuple = (0., 0., 0.)
    actuator_delay_ticks: int = 0
    pose_bias_m: tuple = (0., 0., 0.)
    pose_drift_m_s: tuple = (0., 0., 0.)
    pose_noise_std_m: float = 0.
    pose_dropout_probability: float = 0.
    joint_feedback_available: bool = True

    def __post_init__(self):
        for value in (self.actuator_bias_deg, self.pose_bias_m, self.pose_drift_m_s):
            array = np.asarray(value, float)
            if array.shape != (3,) or not np.isfinite(array).all():
                raise ValueError('Fault vectors require three finite active-axis values')
        if type(self.actuator_delay_ticks) is not int or not 0 <= self.actuator_delay_ticks <= 6:
            raise ValueError('Fault delay must be 0..6 measured control ticks')
        if not np.isfinite([self.pose_noise_std_m, self.pose_dropout_probability]).all() or not 0 <= self.pose_noise_std_m <= .05 or not 0 <= self.pose_dropout_probability <= 1:
            raise ValueError('Finite bounded pose sensor noise/dropout required')
        if type(self.joint_feedback_available) is not bool or max(abs(np.asarray(self.actuator_bias_deg))) > 12:
            raise ValueError('Explicit feedback availability and bounded simulated bias required')


SCENARIOS = {
    'nominal': FaultSpec(),
    'actuator_bias': FaultSpec(actuator_bias_deg=(7., -5., 4.)),
    'actuator_delay': FaultSpec(actuator_delay_ticks=3),
    'camera_frame_bias': FaultSpec(pose_bias_m=(.025, -.018, .012)),
    'sensor_noise_dropout': FaultSpec(pose_noise_std_m=.006, pose_dropout_probability=.25),
    'sensor_drift': FaultSpec(pose_drift_m_s=(.002, -.001, .001)),
    'confounded_without_joint_feedback': FaultSpec(pose_bias_m=(.025, -.018, .012), joint_feedback_available=False),
}


class FaultedReach:
    """Fault injection exists only here; no device interfaces or model edits."""
    def __init__(self, mesh_dir, horizon=60):
        self.env = ReachEnv(mesh_dir, horizon=horizon)
        self.fault = FaultSpec()

    def reset(self, seed, fault=None):
        self.fault = fault or FaultSpec()
        self.env.reset(seed=seed)
        self.command = self.env.command.copy()
        self.queue = [self.command.copy()] * (self.fault.actuator_delay_ticks + 1)
        self.sensor_rng = np.random.default_rng(seed + 600007)
        self.steps, self.success, self.hold = 0, False, 0
        return self.measurement()

    def geometry(self, command):
        self.env._pose(self.env.reference, command)
        return self.env.reference.site_xpos[self.env.site_id].copy()

    def _pose_sensor(self, truth):
        if self.sensor_rng.random() < self.fault.pose_dropout_probability:
            return np.full(3, np.nan)
        return (np.asarray(truth) + np.asarray(self.fault.pose_bias_m)
            + np.asarray(self.fault.pose_drift_m_s) * self.steps * self.env.period
            + self.sensor_rng.normal(0., self.fault.pose_noise_std_m, 3))

    def measurement(self):
        joints = joint_to_servo(self.env.data.qpos[self.env.qids])
        if self.fault.joint_feedback_available:
            # FK uses only the measured encoder vector and the registered CAD
            # chain; it never receives the hidden position sensor correction.
            mujoco.mj_resetData(self.env.model, self.env.reference)
            self.env.reference.qpos[self.env.qids] = self.env.data.qpos[self.env.qids]
            mujoco.mj_forward(self.env.model, self.env.reference)
            fk = self.env.reference.site_xpos[self.env.site_id].copy()
            encoder = joints[:3].copy()
        else:
            encoder, fk = np.full(3, np.nan), np.full(3, np.nan)
        tool = self.env.data.site_xpos[self.env.site_id].copy()
        return dict(joint_sensor_deg=encoder, pose_sensor_xyz_m=self._pose_sensor(tool),
            fk_from_joint_sensor_xyz_m=fk, elapsed_sim_s=self.steps*self.env.period,
            requested_command3=self.command[:3].copy())

    def step(self, requested):
        raw = np.asarray(requested, float)
        if raw.shape != (9,) or not np.isfinite(raw).all() or np.any(raw[6:] != 0) or not np.array_equal(raw[3:6], HOME[3:6]):
            raise ValueError('This pilot accepts only finite three-joint arm reach requests')
        target = HOME.copy()
        bounded = np.clip(raw[:3], 35, 145)
        target[:3] = self.command[:3] + np.clip(bounded-self.command[:3], -2.5, 2.5)
        self.env.last_action = (target[:3]-self.command[:3])/2.5
        self.command = target
        self.queue.append(target.copy())
        delayed = self.queue[-1-self.fault.actuator_delay_ticks].copy()
        effective = delayed.copy()
        effective[:3] = np.clip(delayed[:3]+self.fault.actuator_bias_deg, 35, 145)
        self.env.command = self.command.copy()
        self.env.data.ctrl[self.env.servo_ids] = servo_to_joint(effective[:6])
        for _ in range(round(self.env.period/self.env.model.opt.timestep)):
            mujoco.mj_step(self.env.model, self.env.data)
        self.steps += 1;self.env.steps = self.steps
        distance = self.env.distance()
        self.hold = self.hold+1 if distance < .025 else 0
        self.success |= self.hold >= 3
        measured = self.measurement()
        measured['audit_only_effective_actuator_target3'] = effective[:3].copy()
        measured['audit_only_raw_requested3'] = raw[:3].copy()
        measured['audit_only_true_tool_xyz_m'] = self.env.data.site_xpos[self.env.site_id].copy()
        return measured

    def target_measurement(self):
        # A target localization from the same biased/noisy camera frame. The
        # independent evaluator retains its world goal outside the controller.
        return self._pose_sensor(self.env.goal)

    def close(self):
        self.env.close()


def choose_probe(joint_feedback_available=True):
    """Select an excitation by predicted disagreement of delay hypotheses."""
    length = 64
    pulse = np.zeros((length, 3))
    for index in range(length):
        pulse[index] = [10 if (index//8)%2 else -10,
                        8 if (index//11)%2 else -8,
                        6 if (index//13)%2 else -6]
    sinusoid = np.column_stack([8*np.sin(np.arange(length)*frequency) for frequency in (.12, .15, .18)])
    candidates = dict(stationary_hold=np.zeros_like(pulse), bidirectional_joint_pulses=pulse, slow_sine=sinusoid)
    scores = {}
    for name, commands in candidates.items():
        predictions = np.stack([np.vstack((np.repeat(commands[:1], delay, axis=0), commands[:length-delay])) if delay else commands for delay in (0, 1, 3)])
        scores[name] = float(np.var(predictions, axis=0).mean())
    selected = max(scores, key=scores.get)
    return dict(selected=selected, scores=scores, criterion='nominal_expected_disagreement_between_0_1_3_tick_delay_models',
        observability='joint_encoders_and_pose_sensor' if joint_feedback_available else 'pose_only_actuator_frame_confounding_expected'), candidates[selected]


def collect_probe(plant, seed, fault, commands):
    plant.reset(seed, fault)
    rows = []
    for _ in range(12):
        rows.append(plant.step(HOME.copy()))
    for value in commands:
        request = HOME.copy();request[:3] += value
        rows.append(plant.step(request))
    return {name:np.asarray([row[name] for row in rows]) for name in rows[0]}


def diagnose(measurements, nominal, *, max_delay_ticks=5):
    """Estimate from observations only. This function cannot inspect FaultSpec."""
    observed = np.asarray(measurements['joint_sensor_deg'], float)
    expected = np.asarray(nominal['joint_sensor_deg'], float)
    pose = np.asarray(measurements['pose_sensor_xyz_m'], float)
    fk = np.asarray(measurements['fk_from_joint_sensor_xyz_m'], float)
    clock = np.asarray(measurements['elapsed_sim_s'], float)
    if observed.shape != expected.shape or observed.ndim != 2 or observed.shape[1] != 3 or pose.shape != observed.shape or fk.shape != observed.shape or clock.shape != (len(observed),) or len(observed) < 20:
        raise ValueError('Synchronized named probe measurements and nominal predictions required')
    if not np.isfinite(clock).all() or np.any(np.diff(clock) <= 0) or type(max_delay_ticks) is not int or not 0 <= max_delay_ticks <= 6:
        raise ValueError('Strict source simulation chronology and bounded candidate delays required')
    joint_valid = np.isfinite(observed).all(axis=1) & np.isfinite(expected).all(axis=1)
    pose_valid = np.isfinite(pose).all(axis=1)
    coverage = dict(joint_samples=int(joint_valid.sum()), pose_samples=int(pose_valid.sum()),
        pose_dropped=int((~pose_valid).sum()), n=len(observed))
    result = dict(status='DIAGNOSED', causes=[], measurement_coverage=coverage,
        actuator_bias_deg=[0.,0.,0.], actuator_delay_ticks=0, pose_bias_m=[0.,0.,0.], pose_drift_m_s=[0.,0.,0.],
        pose_noise_std_m=None, hypotheses={}, hidden_injection_fields_consumed=False,
        diagnostic_scope='model_relative_simulation_probe_not_physical_identification')
    if joint_valid.sum() < 16:
        difference = pose-np.asarray(nominal['pose_sensor_xyz_m'], float)
        residual = float(np.sqrt(np.nanmean(difference**2))) if pose_valid.any() else None
        result.update(status='AMBIGUOUS', competing_causes=['actuator_model_mismatch', 'camera_frame_miscalibration', 'joint_feedback_unavailable'],
            reason='Without an independent joint or frame reference, camera and actuator errors are not uniquely attributable',
            hypotheses=dict(pose_vs_nominal_rmse_m=residual), repair_authorized=False)
        return result
    begin = max_delay_ticks+12
    models = []
    for delay in range(max_delay_ticks+1):
        indices = np.arange(begin, len(observed))
        valid = joint_valid[indices] & joint_valid[indices-delay]
        differences = observed[indices[valid]]-expected[indices[valid]-delay]
        if len(differences) < 10:
            raise ValueError('Not enough complete informative joint samples')
        bias = np.median(differences, axis=0)
        models.append(dict(delay_ticks=delay, bias_deg=bias.tolist(),
            residual_rmse_deg=float(np.sqrt(np.mean((differences-bias)**2))),
            no_bias_rmse_deg=float(np.sqrt(np.mean(differences**2)))))
    best = min(models, key=lambda item:item['residual_rmse_deg'])
    nominal_fit = models[0]
    result['hypotheses']['joint_bias_and_delay_candidates'] = models
    result['hypotheses']['nominal_rmse_deg'] = nominal_fit['no_bias_rmse_deg']
    # A candidate must explain the data materially better than no delay.
    if best['delay_ticks'] and best['residual_rmse_deg'] < .65*nominal_fit['residual_rmse_deg']:
        result['actuator_delay_ticks'] = best['delay_ticks'];result['causes'].append('actuator_delay')
    else:
        best = nominal_fit
    if max(abs(np.asarray(best['bias_deg']))) > .8 and best['residual_rmse_deg'] < .65*best['no_bias_rmse_deg']:
        result['actuator_bias_deg'] = best['bias_deg'];result['causes'].append('actuator_bias')
    pair_valid = pose_valid & np.isfinite(fk).all(axis=1)
    if pair_valid.sum() >= 12:
        residuals = pose[pair_valid]-fk[pair_valid]
        design = np.column_stack((np.ones(pair_valid.sum()), clock[pair_valid]))
        fitted = np.linalg.lstsq(design, residuals, rcond=None)[0]
        errors = residuals-design@fitted
        noise = float(np.sqrt(np.mean(errors**2)))
        result['pose_noise_std_m'] = noise
        result['hypotheses']['camera_minus_encoder_FK_linear_fit_rmse_m'] = noise
        if np.linalg.norm(fitted[0]) > .003:
            result['pose_bias_m'] = fitted[0].tolist();result['causes'].append('camera_frame_bias')
        if np.linalg.norm(fitted[1]) > .0007 and np.linalg.norm(fitted[1])*np.ptp(clock[pair_valid]) > 3*noise:
            result['pose_drift_m_s'] = fitted[1].tolist();result['causes'].append('sensor_drift')
        if noise > .002:
            result['causes'].append('sensor_noise')
    else:
        result['causes'].append('pose_sensor_insufficient')
    if coverage['pose_dropped']:
        result['causes'].append('sensor_dropout')
    result['repair_authorized'] = 'pose_sensor_insufficient' not in result['causes']
    if not result['causes']:
        result['causes'] = ['nominal_within_probe_resolution']
    return result


def solve_target(plant, xyz):
    """Nominal task-space IK, using the observed target and no fault labels."""
    target = np.asarray(xyz, float)
    if target.shape != (3,) or not np.isfinite(target).all():
        raise ValueError('A finite observed target is required')
    command = HOME.copy()
    for _ in range(70):
        current = plant.geometry(command)
        error = target-current
        if np.linalg.norm(error) < .0005:
            break
        columns = []
        for axis in range(3):
            perturbed = command.copy();perturbed[axis] += .01
            columns.append((plant.geometry(perturbed)-current)/.01)
        jacobian = np.column_stack(columns)
        update = np.linalg.solve(jacobian.T@jacobian + 1e-8*np.eye(3), jacobian.T@error)
        command[:3] = np.clip(command[:3] + np.clip(update, -4, 4), 45, 135)
    return command


def _json_value(value):
    if isinstance(value, np.ndarray):
        return _json_value(value.tolist())
    if isinstance(value, list):
        return [_json_value(item) for item in value]
    if isinstance(value, (float, np.floating)) and not np.isfinite(value):
        return None
    return value


def execute_trial(plant, seed, fault, diagnosis, *, repair, horizon):
    plant.reset(seed, fault)
    samples, observations = [], []
    for _ in range(5):
        sample = plant.target_measurement()
        samples.append(dict(elapsed_sim_s=plant.steps*plant.env.period, target_sensor_xyz_m=_json_value(sample)))
        observations.append(sample)
        plant.step(HOME.copy())
    valid = np.isfinite(observations).all(axis=1)
    authorized = repair and diagnosis.get('repair_authorized') is True
    if not np.any(valid):
        return dict(seed=seed, success=False, outcome='unknown_no_target_measurement', distance_m=plant.env.distance(), observations=samples, rows=[])
    observed = np.asarray(observations)[valid]
    if authorized:
        acquired = np.asarray([sample['elapsed_sim_s'] for sample in samples])[valid]
        corrected = observed-np.asarray(diagnosis['pose_bias_m'])-acquired[:,None]*np.asarray(diagnosis['pose_drift_m_s'])
        perceived = np.median(corrected, axis=0)
    else:
        perceived = observed[0]
    nominal_goal = solve_target(plant, perceived)
    times = np.linspace(0, 1, horizon-5)
    smooth = times**3*(10-15*times+6*times**2)
    trajectory = [HOME+(nominal_goal-HOME)*value for value in smooth] + [nominal_goal.copy() for _ in range(5)]
    rows, first_success = [], None
    for index in range(horizon):
        advance = diagnosis['actuator_delay_ticks'] if authorized else 0
        requested = trajectory[min(index+advance, horizon-1)].copy()
        if authorized:
            requested[:3] -= np.asarray(diagnosis['actuator_bias_deg'])
        measurement = plant.step(requested)
        rows.append({name:_json_value(value) for name,value in measurement.items()})
        if plant.success and first_success is None:
            first_success = (index+1)*plant.env.period
    unstable = not np.isfinite(plant.env.data.qpos).all() or any(w.number for w in plant.env.data.warning)
    return dict(seed=seed, success=bool(plant.hold >= 3 and not unstable), distance_m=plant.env.distance(),
        success_predicate='final_3_consecutive_control_steps_within_0.025m', final_hold_steps=plant.hold,
        first_success_s=first_success, unstable=bool(unstable), goal_truth_evaluator_only_xyz_m=plant.env.goal.tolist(),
        observed_target_xyz_m=perceived.tolist(), nominal_ik_endpoint3=nominal_goal[:3].tolist(),
        repair_applied=authorized, observations=samples, rows=rows)


def settings(config):
    config = config or {}
    result = {}
    for name, default, low, high in (('seed',741,0,10**6),('evaluation_episodes',3,1,20),('horizon',48,24,100)):
        value = config.get(name, default)
        if type(value) is not int or not low <= value <= high:
            raise ValueError('Invalid fault-repair '+name)
        result[name] = value
    scenarios = config.get('scenarios', list(SCENARIOS))
    if not isinstance(scenarios, list) or not scenarios or any(not isinstance(item, str) or item not in SCENARIOS for item in scenarios) or len(set(scenarios)) != len(scenarios):
        raise ValueError('Choose distinct controlled simulation scenarios')
    if config.get('device', 'cpu') != 'cpu':
        raise ValueError('This finite diagnosis pilot uses CPU MuJoCo only')
    result['scenarios'] = scenarios
    return result


def _summary(episodes):
    return dict(n=len(episodes), successes=sum(item['success'] for item in episodes),
        mean_final_distance_m=float(np.mean([item['distance_m'] for item in episodes])),
        unknown_outcomes=sum(item.get('outcome', '').startswith('unknown') for item in episodes),
        mean_success_latency_s=float(np.mean([item['first_success_s'] for item in episodes if item.get('first_success_s') is not None])) if any(item.get('first_success_s') is not None for item in episodes) else None)


def execution_identity(options, mesh_dir):
    source_paths = [Path(__file__), Path(__file__).with_name('pilot.py'),
        Path(__file__).parents[1]/'legacy/simulator/explorer_model.py',
        Path(__file__).parents[1]/'legacy/simulator/model_config.json',
        Path(__file__).parents[1]/'legacy/config/explorer.urdf', Path(__file__).parents[1]/'legacy/config/explorer.srdf']
    context = dict(options=options, source_sha256={str(path):sha(path) for path in source_paths},
        cad_mesh_sha256={path.name:sha(path) for path in sorted(Path(mesh_dir).glob('*.STL'))},
        versions={name:importlib.metadata.version(name) for name in ('mujoco','numpy')})
    return dict(context, request_signature=hashlib.sha256(json.dumps(context,sort_keys=True).encode()).hexdigest())


def run_fault_repair(store, job, config=None):
    """Durable, offline simulation entry; no hidden actuator/controller writes."""
    options = settings(job.get('config'))
    identity = job['id']
    if not isinstance(identity, str) or Path(identity).name != identity or identity in ('.', '..'):
        raise ValueError('Invalid fault-repair job identity')
    folder = Path(store.root)/'jobs'/identity
    folder.mkdir(parents=True, exist_ok=True)
    mesh_dir = resolve_mesh_dir((job.get('config') or {}).get('mesh_dir'))
    execution = execution_identity(options, mesh_dir)
    signature, source_hashes = execution['request_signature'], execution['source_sha256']
    source_paths = [Path(name) for name in source_hashes]
    with (folder/'.fault-repair-owner.lock').open('a') as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise RuntimeError('Fault-repair attempt already active') from error
        if (folder/'fault-repair-result.json').is_file():
            saved = json.loads((folder/'fault-repair-result.json').read_text())
            if saved['request_signature'] != signature:
                raise ValueError('Completed fault-repair source or settings differ')
            for name, expected in saved['artifact_sha256'].items():
                artifact = (folder/name).resolve()
                if not artifact.is_relative_to(folder.resolve()) or sha(artifact) != expected:
                    raise ValueError('Completed fault-repair artifact checksum mismatch')
            return saved
        attempt = folder/'attempts'/uuid.uuid4().hex;attempt.mkdir(parents=True)
        plant = FaultedReach(mesh_dir, horizon=options['horizon'])
        started = time.monotonic()
        records = []
        try:
            (attempt/'model.xml').write_text(plant.env.xml)
            source_folder = attempt/'implementation_sources';source_folder.mkdir()
            for source in source_paths:
                (source_folder/source.name).write_bytes(source.read_bytes())
            for scenario_index, scenario_id in enumerate(options['scenarios']):
                fault = SCENARIOS[scenario_id]
                case_folder = attempt/scenario_id;case_folder.mkdir()
                availability = plant.reset(options['seed']+scenario_index, fault)
                selection, commands = choose_probe(bool(np.isfinite(availability['joint_sensor_deg']).all()))
                probe_seed = options['seed']+scenario_index
                nominal = collect_probe(plant, probe_seed, FaultSpec(), commands)
                measured = collect_probe(plant, probe_seed, fault, commands)
                # Construct an explicit diagnostic view: audit-only true tool
                # positions/effective actuator targets never cross this boundary.
                fields = ('joint_sensor_deg','pose_sensor_xyz_m','fk_from_joint_sensor_xyz_m','elapsed_sim_s','requested_command3')
                diagnostic_input = {key:measured[key] for key in fields}
                diagnosis = diagnose(diagnostic_input, {key:nominal[key] for key in fields})
                np.savez_compressed(case_folder/'probe-measurements.npz', **measured)
                np.savez_compressed(case_folder/'nominal-probe.npz', **nominal)
                write_json(case_folder/'diagnosis.json', diagnosis)
                seeds = list(range(options['seed']+10000, options['seed']+10000+options['evaluation_episodes']))
                baseline, repaired = [], []
                for seed in seeds:
                    baseline.append(execute_trial(plant, seed, fault, diagnosis, repair=False, horizon=options['horizon']))
                    repaired.append(execute_trial(plant, seed, fault, diagnosis, repair=True, horizon=options['horizon']))
                write_json(case_folder/'matched-development-trials.json',dict(baseline=baseline,targeted_repair=repaired))
                record = dict(scenario_id=scenario_id, injection_audit=json.loads(json.dumps(asdict(fault))), probe_selection=selection,
                    probe_seed=probe_seed, development_seeds=seeds, diagnosis=diagnosis,
                    unmodified=_summary(baseline), targeted_repair=_summary(repaired),
                    paired_final_distance_change_m=[after['distance_m']-before['distance_m'] for before,after in zip(baseline,repaired)],
                    trial_artifact=str(case_folder/'matched-development-trials.json'))
                records.append(record)
                store.update_job(identity,progress=(scenario_index+1)/len(options['scenarios'])*.98,result=dict(phase='controlled_simulation_diagnosis_and_matched_repair', scenarios_completed=len(records)))
            result = dict(schema='explorer_controlled_fault_repair_v1', request_signature=signature, created_wall_utc=utc(),
                options=options, actual_attempt=str(attempt), cases=records, elapsed_s=time.monotonic()-started,
                source_sha256=source_hashes,
                cad_mesh_sha256=execution['cad_mesh_sha256'],
                model_xml_sha256=sha(attempt/'model.xml'), versions=execution['versions'],
                method_ids=['EXPLORER_CAD_MUJOCO','ENGINEERING_GLUE'], physical_run=False,
                scope='controlled_faults_and_model_based_selective_compensation_in_CAD_fixed_base_reach',
                diagnostic_fault_labels_consumed=False, full_fine_tuning_baseline_executed=False,
                hypotheses_status='UNTESTED_HYPOTHESIS',
                limitations=['Development seeds reused across matched conditions; no sealed test or science claim',
                    'Cartesian pose-coordinate sensor perturbations, not RGB detection or full camera calibration',
                    'Explicit simulated joint encoders, not evidence of ROSMASTER measured joint feedback',
                    'Fixed-base reach only; friction/contact causes cannot be identified without object contact',
                    'No learned policy/fine-tuning comparison; H4 remains untested'])
            if execution_identity(options, mesh_dir) != execution:
                raise ValueError('Fault-repair source, CAD assets or engine changed during execution; attempt retained')
            result['artifact_sha256'] = {str(path.relative_to(folder)):sha(path) for path in attempt.rglob('*') if path.is_file()}
            write_json(folder/'fault-repair-result.json',result)
            return result
        finally:
            plant.close()
