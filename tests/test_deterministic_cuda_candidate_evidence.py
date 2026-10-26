"""Positive fixture evidence does not qualify the production execution profile."""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).parents[1]


def test_deterministic_cuda_fixture_has_actual_exactness_with_explicit_settings_only():
    index = json.loads((ROOT / '_registry/evidence/act9-cuda-deterministic-fixture-20261026.json').read_text())
    for name, expected in index['archives'].items():
        assert Path(name).name == name
        assert hashlib.sha256((ROOT / 'shared/training' / name).read_bytes()).hexdigest() == expected
    result = json.loads((ROOT / 'shared/training/ACT9_CUDA_DETERMINISTIC_CANDIDATE_20261026_QUALIFICATION.json').read_text())
    assert result['model_optimizer_losses_all_RNG_sampler_exactly_equal'] and result['old_process_gone']
    assert result['reference']['final_weights_sha256'] == result['resumed']['final_weights_sha256']
    assert result['resumed']['deterministic_algorithms'] and result['resumed']['cudnn_deterministic']
    assert result['resumed']['cublas_workspace_config'] == ':4096:8'
    assert result['resumed']['training_resume']['resumed_from_step'] == 4
    assert result['resumed']['training_resume']['optimizer_steps_executed_this_process'] == 4
    assert index['passed'] and not index['production_profile_qualified']
    assert not index['production_math_or_runtime_changed'] and not index['physical_io']
