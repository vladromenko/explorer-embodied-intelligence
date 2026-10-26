"""Current checkpoint-aware source math qualification; no model allocation."""
import hashlib
import json
from pathlib import Path

ROOT=Path(__file__).parents[1]
PROOFS=ROOT/'shared/training'


def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def read(name):return json.loads((PROOFS/name).read_text())


def test_actual_current_queue_math_preserves_source04_weights_and_qualifies_only_software_fixtures():
    comparison=read('SOURCE04_QUEUE_20261026_CHARACTERIZATION.json');act=read('SO101_ACT6_20261026_QUEUE_QUALIFICATION.json')
    active=read('DIFFUSION_NATIVE6_ACTIVE_BINDING.json');dp=read(active['qualification_result'])
    assert comparison['ACT_qualification_sha256']==sha(PROOFS/'SO101_ACT6_20261026_QUEUE_QUALIFICATION.json')
    assert comparison['DP_qualification_sha256']==sha(PROOFS/active['qualification_result'])
    assert act['checkpoint_sha256']['model.safetensors']==read('SO101_ACT6_20261026_STRICT_QUALIFICATION.json')['checkpoint_sha256']['model.safetensors']==comparison['ACT_model_weights_sha256']
    assert dp['checkpoint_sha256']['model.safetensors']==read('DIFFUSION_NATIVE6_20261026_CURRENT_STRICT_QUALIFICATION.json')['checkpoint_sha256']['model.safetensors']==comparison['DP_model_weights_sha256']
    assert act['optimizer_steps']==dp['optimizer_steps']==8 and dp['training_resume']['resumed_from_step']==4
    assert act['saved_processor_reload_max_abs_error']==dp['reload_max_abs_error']==0
    assert act['source_kind']==dp['source_kind']=='SIMULATION' and not comparison['task_success_claimed']
    observation=read('SO101_ACT6_20261026_QUEUE_RUNTIME_OBSERVATION.json')
    assert observation['before']['runtime']==observation['after']['runtime'] and observation['after']['pytest_exit_code']==0
    assert 'shared/explorer_shared/learning/checkpoint_control.py' in observation['before']['implementation_sha256']
    for name,digest in observation['before']['implementation_sha256'].items():assert sha(ROOT/name)==digest
    assert not comparison['production_worker_integration_qualified']
