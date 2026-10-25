"""Finite, isolated stationary cloth-grasp gate. Never resumes the old queue.

Truth is confined to the expert and independent evaluator. This is a diagnostic
gate, not a room-cleanup qualification or a hardware executable.
"""
import argparse
import json
import math
from pathlib import Path
import subprocess
import time

import mujoco
import numpy as np
from PIL import Image

from .environment import ExplorerEnv, PERIOD
from .learning import sha256, write_json
from .room_validation import CollisionAudit
from src.mobile_policy_contract import ACTION_ORDER, UNITS
from .room_policy import issued_command
from .recovery_contracts import SCHEMA, StationaryExecutor, HoldWindow, evidence_value


PROTOCOL = dict(schema=SCHEMA,
    task='lift deformable sock and hold for one second with stationary base',
    training_seeds=[2026102001, 2026102002], development_seeds=[2026102011, 2026102012],
    positions_m=[[.20, 0., .015], [.21, .008, .015], [.205, -.008, .015], [.215, .004, .015]],
    admission_hypothesis='all four grasp waypoints reachable with stationary base at 20-22cm; 29cm rejected by unchanged IK',
    max_steps=600, wall_budget_s=180, minimum_hold_s=1., maximum_base_translation_m=.025,
    forbidden_contacts=0, contact_penetration_m=.001, repeat_failed_hypothesis=False,
    old_learning_resume=False, robot_io=False, physics_changes=False,
    teacher_truth=True, student_inputs=['command_estimate9', 'wrist_rgb'],
    action_order=ACTION_ORDER, units=UNITS, period_s=PERIOD,
    execution='shared_raw9_guard_then_room_issued_command_persistent_residual_then_applied_step',
    evaluation='independent_post_stop_hold_every_physics_substep',
    declared_scope='local grasp prerequisite only; no room or deployment acceptance')



def current_grasp_probe(env):
    """Read the existing contact/height criteria at the CURRENT physics substep.

    Independent evaluator only. Thresholds match ExplorerEnv._evaluate_contact;
    this function does not update its accumulated counters or feed the actor.
    """
    pads = [False, False]
    force = np.zeros(6)
    for index, contact in enumerate(env.data.contact[:env.data.ncon]):
        mujoco.mj_contactForce(env.model, env.data, index, force)
        object_contact = (any(int(i) >= 0 for i in contact.flex) or
            (env.rigid_id >= 0 and env.model.geom('task_object_contact').id in contact.geom))
        if object_contact:
            for side, pad in enumerate(env.pad_ids):
                if pad in contact.geom and contact.dist <= .001 and force[0] > .0001:
                    pads[side] = True
    points = env._sock_points()
    height = float(points[:, 2].mean() - env.start_height) if len(points) else 0.
    distance = float(np.linalg.norm(points.mean(axis=0) - env.jaw_center())) if len(points) else float('inf')
    return dict(valid=bool(all(pads) and height > .045 and distance < .12),
                pad_contacts=pads, height_gain_m=height, distance_to_jaw_m=distance)


def collect_teacher(output, seed, position, *, max_steps=600, wall_budget_s=180, policy=None,
                    prefix_policy=None, prefix_steps=0, reference_prefix=None):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    (output / 'frames').mkdir()
    env = ExplorerEnv(render=True)
    rows = []; states = []; start = time.monotonic(); phase = 'initial'; result = {}
    audit = None
    executor = StationaryExecutor(env.validate_command, issued_command)
    hold_window = None
    hold_rows = []
    hold_reference = None
    expert_targets=policy is None
    active_actor=policy
    prefix_reference=[];prefix_states=None;prefix_checks=0
    try:
        if getattr(policy, 'returns_issued_commands', False):
            raise ValueError('Pilot requires RAW policy outputs; do not preprocess twice')
        env.reset(seed=seed, options=dict(sock_position=position, bin_position=[.60, .30, 0.]))
        if env.model.nflex < 1 or env.rigid_id >= 0:
            raise ValueError('Pilot requires the real deformable sock model')
        base = np.asarray(env.base_pose())
        audit = CollisionAudit(env)
        substep_count = 0

        def observe():
            nonlocal substep_count
            substep_count += 1
            audit.sample()
            if audit.contacts:
                raise RuntimeError('forbidden_contact')
            if hold_window is not None:
                probe = current_grasp_probe(env)
                base_shift = float(np.linalg.norm(np.asarray(env.base_pose())[:2] - base[:2]))
                fixed_command = bool(np.allclose(env.command, hold_reference, atol=1e-9, rtol=0))
                valid = probe['valid'] and base_shift <= .025 and fixed_command
                hold_window.observe(float(env.data.time), valid)
                hold_rows.append(dict(simulation_time=float(env.data.time),
                    base_translation_m=base_shift, command_unchanged=fixed_command, **probe))

        env._outcome_observer = observe
        specification = mujoco.mjtState.mjSTATE_INTEGRATION

        def tick(request, observation=None):
            if len(rows) >= max_steps or time.monotonic() - start > wall_budget_s:
                raise RuntimeError('finite_pilot_budget_exhausted')
            command = env.command.copy()
            raw = np.asarray(request, float)
            if observation is not None:
                if not np.allclose(observation['state'], command, atol=1e-5, rtol=0):
                    raise ValueError('Policy observation is not the current command estimate')
                image = np.asarray(observation['wrist']).copy()
            else:
                image = env.render_rgb('wrist')
            try:
                plan = executor.prepare(command, raw)
            except ValueError as error:
                state = np.empty(mujoco.mj_stateSize(env.model, specification))
                mujoco.mj_getState(env.model, env.data, state, specification)
                np.savez_compressed(output/'refusal-state.npz', integration_state=state)
                Image.fromarray(image).save(output/'refusal.png')
                write_json(output/'refusal.json',evidence_value(dict(step=len(rows),state=command.tolist(),
                    raw_action=raw.tolist(),reason=str(error),applied=False,
                    simulation_time=float(env.data.time),
                    residual=executor.residual.tolist(),
                    policy_exposure=getattr(policy,'last_exposure',{}))))
                raise
            issued = plan.issued.copy()
            image_path = 'frames/' + f'{len(rows):06d}.png'
            Image.fromarray(image).save(output / image_path)
            state = np.empty(mujoco.mj_stateSize(env.model, specification))
            mujoco.mj_getState(env.model, env.data, state, specification)
            states.append(state)
            row = dict(step=len(rows), phase=phase, simulation_time=float(env.data.time),
                observation_state=command.tolist(), raw_action=raw.tolist(), issued_action=issued.tolist(),
                image=image_path, image_sha256=sha256(output / image_path),
                training_target_valid=expert_targets, joint_state_source='command_estimate',
                residual_before=plan.residual_before.tolist(), residual_after=plan.residual_after.tolist(),
                contract_schema=SCHEMA)
            if active_actor is not None:
                row['policy_exposure'] = getattr(active_actor, 'last_exposure', {})
            if phase=='student_prefix':
                expected=prefix_reference[len(rows)]
                if (row['image_sha256']!=expected['image_sha256'] or
                        not np.allclose(command,expected['observation_state'],atol=1e-7,rtol=0) or
                        not np.allclose(raw,expected['raw_action'],atol=1e-6,rtol=0) or
                        not np.allclose(state,prefix_states[len(rows)],atol=1e-8,rtol=0)):
                    raise ValueError('Live student prefix differs from saved RGB/state/raw/physics')
            rows.append(row)
            try:
                env.step(issued, applied=True)
                if env.last_error:
                    raise RuntimeError(env.last_error)
                if np.linalg.norm(np.asarray(env.base_pose())[:2] - base[:2]) > .025:
                    raise RuntimeError('stationary_base_envelope_exceeded')
                executor.commit(plan, env.command)
            finally:
                row.update(applied_action=env.command.tolist(), post_simulation_time=float(env.data.time))

        def move(goal, seconds=2.):
            first = env.command[:6].copy()
            # <=1.8 degrees raw/decision, below the same two-degree runtime cap.
            count = max(math.ceil(seconds / PERIOD), math.ceil(np.max(abs(goal-first)) / 1.8))
            for index in range(count):
                tick(np.r_[first + (goal-first)*(index+1)/count, 0., 0., 0.])
            for _ in range(12):
                tick(np.r_[goal, 0., 0., 0.])

        if prefix_policy is not None:
            if policy is not None or not 1<=prefix_steps<=400 or reference_prefix is None:
                raise ValueError('Invalid finite expert-correction prefix')
            reference=Path(reference_prefix)
            prefix_reference=[json.loads(line) for line in (reference/'samples.jsonl').read_text().splitlines()]
            prefix_states=np.load(reference/'evaluator_states.npz')['integration_state']
            if len(prefix_reference)<prefix_steps:
                raise ValueError('Recorded prefix is shorter than correction request')
            prefix_policy.reset();expert_targets=False;active_actor=prefix_policy;phase='student_prefix'
            for _ in range(prefix_steps):
                obs=env._observation()
                tick(prefix_policy(obs['state'],obs['wrist']),observation=obs)
                prefix_checks+=1
            result['student_prefix']=dict(steps=prefix_steps,verified_steps=prefix_checks,
                reference=str(reference),student_targets_masked=True,
                exact_rgb=True,command_raw_atol=1e-6,physics_state_atol=1e-8,
                live_execution=True,full_restore_claimed=False)
            expert_targets=True;active_actor=None
        if policy is None:
            # Truth belongs only to this explicitly privileged teacher.
            center = env._sock_points().mean(axis=0)
            jaw=env.jaw_center()
            direct_lower=(prefix_policy is not None and np.linalg.norm(jaw[:2]-center[:2])<.03
                and .025<=jaw[2]<=.10 and env.command[5]<=35.)
            result['expert_recovery_start']='direct_lower' if direct_lower else 'approach'
            if not direct_lower:
                phase = 'approach'
                approach = env.solve_ik([*center[:2], .07], env.command[:5], gripper=30.)
                move(approach)
            phase = 'lower'
            lowered = env.solve_ik([*center[:2], .025], env.command[:5], gripper=30.)
            move(lowered)
            phase = 'close'
            closed = env.solve_ik([*center[:2], .017], env.command[:5], gripper=170.)
            move(closed, 4.)
            phase = 'lift'
            lifted = env.solve_ik([*center[:2], .10], env.command[:5], gripper=170.)
            move(lifted)
            phase = 'hold'
            for _ in range(30):
                tick(np.r_[lifted, 0., 0., 0.])
        else:
            phase = 'student'
            if hasattr(policy, 'reset'):
                policy.reset()
            # Fixed horizon: no hidden teacher/task predicate controls the actor.
            for _ in range(min(max_steps, 360)):
                obs = env._observation()
                tick(policy(obs['state'], obs['wrist']), observation=obs)
        # NEW evaluation window: the actor is never queried during this second.
        stopped_phase = phase
        phase = 'post_stop_evaluation'
        hold_reference = env.command.copy()
        first_probe = current_grasp_probe(env)
        hold_window = HoldWindow(float(env.data.time), PROTOCOL['minimum_hold_s'],
            float(env.model.opt.timestep), first_probe['valid'])
        result['post_stop_initial_probe'] = first_probe
        hold_started = float(env.data.time)
        while float(env.data.time) - hold_started < PROTOCOL['minimum_hold_s'] - 1e-9:
            if time.monotonic() - start > wall_budget_s:
                raise RuntimeError('finite_pilot_budget_exhausted_during_post_stop_hold')
            env.step(hold_reference.copy(), applied=True)
            if env.last_error:
                raise RuntimeError(env.last_error)
        result['post_stop_hold'] = hold_window.summary()
        result.update(success=bool(result['post_stop_hold']['success'] and not audit.contacts),
                      stopped_phase=stopped_phase)
        if not result['success']:
            result['reason']='post_stop_hold_not_satisfied'
    except Exception as error:
        result.update(success=False, reason=str(error), stopped_phase=phase)
    finally:
        # Preserve raw and physical state even when the observer aborts mid-step.
        with (output / 'samples.jsonl').open('w') as stream:
            for row in rows:
                stream.write(json.dumps(evidence_value(row), allow_nan=False) + '\n')
        with (output / 'post-stop-hold.jsonl').open('w') as stream:
            for row in hold_rows:
                stream.write(json.dumps(evidence_value(row), allow_nan=False) + '\n')
        if hold_window is not None:
            result['post_stop_hold'] = hold_window.summary()
        runtime = {name: getattr(env, name, None) for name in (
            'command', 'pending_command', 'arm_velocity', 'arm_acceleration', 'steps',
            'phase', 'lift_hold_s', 'placement_hold_s', 'ever_lifted', 'ever_grasped',
            'contacts', 'task_success', 'last_error', 'start_height')}
        runtime.update(executor_residual=executor.residual, executor_generation=executor.generation,
            full_replay_verified=False, note='Host snapshot, not a tested complete restoration codec')
        write_json(output / 'final-runtime-state.json', evidence_value(runtime))
        if states:
            np.savez_compressed(output / 'evaluator_states.npz', integration_state=np.asarray(states))
        final_state = np.empty(mujoco.mj_stateSize(env.model, mujoco.mjtState.mjSTATE_INTEGRATION))
        mujoco.mj_getState(env.model, env.data, final_state, mujoco.mjtState.mjSTATE_INTEGRATION)
        np.savez_compressed(output/'final-evaluator-state.npz', integration_state=final_state)
        if policy is not None:
            Image.fromarray(env.render_rgb('wrist')).save(output/'final-wrist.png')
            write_json(output/'last-policy-exposure.json',evidence_value(getattr(policy,'last_exposure',{})))
        Image.fromarray(env.render_rgb('overview')).save(output / 'final-overview.png')
        result.update(seed=seed, position_m=position, steps=len(rows),
            wall_time_s=time.monotonic()-start, physics_state=env.state(),
            collision_evidence=audit.contacts if audit is not None else [],
            simulation_only=True, physical_ready=False, teacher_truth=policy is None,
            contract_schema=SCHEMA, collision_substep_samples=substep_count if audit is not None else 0,
            student_trained=policy is not None and getattr(policy, 'trained', False), artifact_sha256={
                name:sha256(output/name) for name in ('samples.jsonl', 'final-overview.png', 'evaluator_states.npz',
                    'final-evaluator-state.npz','final-wrist.png','last-policy-exposure.json',
                    'final-runtime-state.json','post-stop-hold.jsonl')
                if (output/name).is_file()})
        write_json(output / 'result.json', evidence_value(result))
        env.close()
    return result


def run(output):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    write_json(output / 'protocol.json', PROTOCOL)
    repo = Path(__file__).resolve().parents[1]
    sources = ['simulator/recovery_pilot.py', 'simulator/recovery_contracts.py',
        'simulator/room_policy.py', 'simulator/environment.py',
        'simulator/room_validation.py', 'src/mobile_policy_contract.py',
        'simulator/explorer_model.py', 'simulator/model_config.json', 'config/explorer.urdf',
        'config/explorer.srdf', 'config/arm-motion.json']
    write_json(output / 'source.json', dict(revision=subprocess.check_output(
        ['git', 'rev-parse', 'HEAD'], cwd=repo, text=True).strip(),
        files={name:sha256(repo/name) for name in sources if (repo/name).is_file()}))
    results = []
    for index, seed in enumerate(PROTOCOL['training_seeds']):
        value = collect_teacher(output / f'teacher-{seed}', seed, PROTOCOL['positions_m'][index])
        results.append(value)
        write_json(output / 'state.json', dict(state='teacher_gate', completed=len(results), results=results))
        if not value['success']:
            break
    # No optimizer may start from an invalid teacher contract or failed grasp.
    state = 'teacher_gate_passed' if all(r['success'] for r in results) else 'blocked_teacher'
    report = dict(state=state, teacher_successes=sum(r['success'] for r in results),
        teacher_attempts=len(results), results=results, training_updates=0,
        student_successes=None, next_action=('implement bounded comparative learner' if state == 'teacher_gate_passed'
            else 'repair the recorded teacher failure before training'),
        old_operator_pause_preserved=True, room_ready=False, physical_ready=False)
    write_json(output / 'state.json', report)
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(run(args.output), ensure_ascii=False))
