"""Inspect archived actual checkpoint math proofs without allocating a model."""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).parents[1]


def receipt(name):
    return json.loads((ROOT / 'shared/training' / name).read_text())


def test_process_pause_proves_new_process_exactness_for_both_optimizer_loops():
    proof = receipt('OPTIMIZER_PROCESS_PAUSE_20261026_QUALIFICATION.json')
    assert proof['runtime_observation']['pytest_exit_code'] == 0
    assert {row['receipt']['backend'] for row in proof['receipts']} == {'act', 'diffusion'}
    for row in proof['receipts']:
        value = row['receipt']
        assert value['old_process_gone'] and value['resident_bytes_before_pause'] > 0
        assert value['paused_pid'] != value['resumed_pid']
        assert value['weights_optimizer_losses_exactly_equal']
        assert value['resumed_from_step'] == 4 and value['completed_steps'] == 8
    assert not proof['task_success_claimed'] and not proof['physical_io']


def test_actual_cpu_106_actor_is_not_promoted_to_cuda_or_physical_success():
    proof = receipt('ROSMASTER_ACT106_20261026_QUEUE_CPU_QUALIFICATION.json')
    assert proof['device'] == 'cpu' and proof['feature_dimension'] == 106
    assert proof['action_dimension'] == 9 and proof['optimizer_steps'] == 8
    assert proof['reload_max_abs_error'] == 0 and proof['independent_development_samples'] == 10
    assert not proof['sealed_test_used'] and not proof['learned_policy_rollout_executed']
    assert proof['whole_task_success'] == 'NOT_EVALUATED'
    assert not proof['gpu_qualified'] and not proof['physical_success']
    for name, expected in proof['runtime_observation']['source_sha256'].items():
        assert hashlib.sha256((ROOT / name).read_bytes()).hexdigest() == expected
