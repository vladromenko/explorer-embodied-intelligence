"""Recheck actual physical artifacts before any diagnostic registration.

No optimizer, teacher, physics actions, service update or production registry.
"""
import json
from pathlib import Path
import numpy as np

from .learning import sha256, write_json
from .environment import ExplorerEnv
from .room_policy import issued_command
from .recovery_contracts import SCHEMA, StationaryExecutor, validate_teacher_rows


def post_stop_gate(result,hold_rows):
    window=result.get('post_stop_hold',{})
    times=[row.get('simulation_time',float('nan')) for row in hold_rows]
    continuous=bool(len(times)>=500 and np.isfinite(times).all()
        and np.allclose(np.diff(times),.002,atol=1e-8,rtol=0))
    return bool(result.get('success') is True and result.get('teacher_truth') is False
        and result.get('student_trained') is True and not result.get('collision_evidence')
        and result.get('contract_schema')==SCHEMA
        and window.get('success') is True and window.get('all_samples_valid') is True
        and window.get('elapsed_s',0)>=1.-1e-8 and window.get('substep_samples',0)>=500
        and len(hold_rows)==window['substep_samples']
        and continuous
        and all(row.get('valid') and row.get('command_unchanged')
            and row.get('pad_contacts')==[True,True] and row.get('height_gain_m',0)>.045
            and row.get('distance_to_jaw_m',1)<.12
            and row.get('base_translation_m',1)<=.025 for row in hold_rows))


def verify_episode(folder):
    folder=Path(folder)
    result=json.loads((folder/'result.json').read_text())
    for name,digest in result['artifact_sha256'].items():
        path=(folder/name).resolve()
        if not path.is_relative_to(folder.resolve()) or sha256(path)!=digest:
            raise ValueError('Changed or escaping episode artifact: '+name)
    rows=[json.loads(line) for line in (folder/'samples.jsonl').read_text().splitlines()]
    validate_teacher_rows(rows,ExplorerEnv.validate_command,require_expert=False)
    guard=StationaryExecutor(ExplorerEnv.validate_command,issued_command)
    for row in rows:
        if row['training_target_valid'] is not False:
            raise ValueError('Teacher-labelled row found inside autonomous student evaluation')
        if sha256(folder/row['image'])!=row['image_sha256']:
            raise ValueError('Student input RGB changed')
        plan=guard.prepare(row['observation_state'],row['raw_action'])
        if not np.allclose(plan.issued,row['applied_action'],atol=1e-6,rtol=0):
            raise ValueError('Stored applied command differs from shared executor')
        if not np.allclose(plan.residual_before,row['residual_before'],atol=1e-6,rtol=0):
            raise ValueError('Residual lineage differs')
        guard.commit(plan,row['applied_action'])
    hold=[json.loads(line) for line in (folder/'post-stop-hold.jsonl').read_text().splitlines()]
    return dict(path=str(folder),success=post_stop_gate(result,hold),seed=result['seed'],
        steps=len(rows),raw_contract_passed=True,reason=result.get('reason'),
        result_sha256=sha256(folder/'result.json'),simulation_only=True)


def compare(root):
    root=Path(root);candidate=root/'loss-mse-uniform'
    expected=json.loads((root/'BUDGET_AND_PROTOCOL.json').read_text())['success_followup']['seeds']
    training=[verify_episode(candidate/'rollout-2026102001'),verify_episode(candidate/'rollout-repeat-2026102001')]
    independent=[verify_episode(candidate/f'rollout-{seed}') for seed in expected]
    control=verify_episode(root/'control-l1-uniform/rollout-2026102001')
    checkpoint_sha=sha256(candidate/'bc/checkpoint.pt')
    for seed in [2026102001]+expected:
        p=json.loads((candidate/f'rollout-protocol-{seed}.json').read_text())
        if p['source_checkpoint_sha256']!=checkpoint_sha:
            raise ValueError('Evaluation used another checkpoint')
    all_pass=all(row['success'] for row in training+independent)
    decision=dict(state='diagnostic_candidate_only' if all_pass else 'registration_refused',
        registration_allowed=all_pass,production_registration=False,robot_ready=False,room_ready=False,
        checkpoint_sha256=checkpoint_sha,training_repeats=training,independent=independent,control=control,
        old_learning_resumed=False,teacher_fallback=False,
        reason='All declared local gates passed' if all_pass else 'At least one reserved physical scene failed',
        full_collect_correct_retrain_loop_qualified=False,
        implemented_slice='Validate physical evidence -> Compare fixed checkpoints -> gate Register',
        missing='Automatic failure-specific teacher collection and retraining with a new untouched test protocol')
    write_json(root/'comparison-and-registration.json',decision)
    return decision


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('--root',type=Path,required=True)
    print(json.dumps(compare(parser.parse_args().root),indent=2))
