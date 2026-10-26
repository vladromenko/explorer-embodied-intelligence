"""A failed actual CUDA comparison stays failed; no optional model allocation."""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).parents[1]


def test_actual_cuda_resume_negative_is_preserved_without_tolerance_promotion():
    report = json.loads((ROOT / '_registry/evidence/act9-cuda-resume-negative-20261026.json').read_text())
    assert not report['passed'] and not report['CUDA_bit_exact_resume_qualified']
    assert report['actual_test_exit_code'] == 1 and report['pause_exit_restart_completed']
    assert report['all_RNG_and_sampler_equal'] and report['initial_model_weights_equal']
    assert report['independent_prefix_already_different_before_pause']
    assert report['prefix4_max_model_abs_difference'] > 0 and report['final8_max_model_abs_difference'] > 0
    assert not report['root_math_code_changed'] and not report['task_success_claimed']
    for name, expected in report['archives'].items():
        assert Path(name).name == name
        assert hashlib.sha256((ROOT / 'shared/training' / name).read_bytes()).hexdigest() == expected
