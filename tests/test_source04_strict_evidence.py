"""Actual strict-source qualifications stay byte-bound; this test allocates no model."""
import hashlib
import json
from pathlib import Path

ROOT=Path(__file__).parents[1]
PROOFS=ROOT/'shared/training'


def read(name):return json.loads((PROOFS/name).read_text())


def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def test_actual_strict_native6_runs_preserve_prior_model_math_and_truthful_shadow_scope():
    comparison=read('SOURCE04_STRICT_20261026_CHARACTERIZATION.json')
    act=read('SO101_ACT6_20261026_STRICT_QUALIFICATION.json')
    shadow=read('SO101_ACT6_20261026_STRICT_SHADOW_INFERENCE.json')
    runtime=read('SO101_ACT6_20261026_STRICT_RUNTIME_OBSERVATION.json')
    active=read('DIFFUSION_NATIVE6_ACTIVE_BINDING.json');dp=read(active['qualification_result'])
    assert comparison['ACT_qualification_sha256']==sha(PROOFS/'SO101_ACT6_20261026_STRICT_QUALIFICATION.json')
    assert comparison['DP_qualification_sha256']==sha(PROOFS/active['qualification_result'])
    assert comparison['prior_ACT_qualification_sha256']==sha(ROOT/'research/audits/SO101_SOURCE04_ACT_QUALIFICATION_20261026.json')
    assert comparison['prior_DP_qualification_sha256']==sha(PROOFS/'DIFFUSION_NATIVE6_20261026_CURRENT_QUALIFICATION.json')
    assert act['checkpoint_sha256']['model.safetensors']==comparison['ACT_model_weights_sha256_unchanged_vs_prior_qualification']
    assert dp['checkpoint_sha256']['model.safetensors']==comparison['DP_model_weights_sha256_unchanged_vs_prior_qualification']
    assert act['optimizer_steps']==8 and act['official_saved_processors_executed'] and act['saved_processor_reload_max_abs_error']==0
    assert {'policy_preprocessor.json','policy_postprocessor.json'}<=set(act['checkpoint_sha256'])
    assert act['source_kind']==dp['source_kind']=='SIMULATION' and act['project_id']==dp['project_id']=='04'
    assert shadow['execution_kind']=='offline_original_observation_policy_inference' and shadow['lineage']['software_fixture_mode']
    assert shadow['official_saved_processors_executed'] and not shadow['hardware_commands_dispatched'] and not shadow['physical_execution_authorized']
    assert runtime['before']['runtime']==runtime['after']['runtime'] and runtime['after']['pytest_exit_code']==0
    for name,digest in runtime['before']['implementation_sha256'].items():assert sha(ROOT/name)==digest
    assert runtime['before']['implementation_sha256']==runtime['after']['implementation_sha256']
    assert not comparison['production_queue_integration_qualified'] and not comparison['task_success_claimed']
