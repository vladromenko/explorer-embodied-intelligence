"""Real current-source profiles are independent from legacy fixture evidence."""
import hashlib
import json
from pathlib import Path

from explorer.qualification_bindings import PIPELINES
from explorer_shared.learning.runtime_profiles import ACT9_CUDA_DETERMINISTIC_PROFILE,ACT9_RUNTIME_CONSTRAINTS

ROOT=Path(__file__).parents[1]
PROOFS=ROOT/'shared/training'


def read(name):return json.loads((PROOFS/name).read_text())
def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def test_actual_current_production_profile_has_first_start_and_exact_GPU_new_process_receipts():
    index=json.loads((ROOT/'_registry/evidence/act9-source04-current-20261027.json').read_text())
    for name,expected in index['archives'].items():assert Path(name).name==name and sha(PROOFS/name)==expected
    qualification=read('ACT9_SOURCE04_CUDA_20261027_QUALIFICATION.json')
    result=read('ACT9_SOURCE04_CUDA_20261027_RESULT.json')
    resume=read('ACT9_SOURCE04_CUDA_20261027_RESUME_QUALIFICATION.json')
    runtime=read('ACT9_SOURCE04_CUDA_20261027_RESUME_RUNTIME.json')
    assert runtime['pipeline_source_sha256']==qualification['pipeline_source_sha256']
    assert runtime['pytest_exit_code']==0 and not runtime['physical_io']
    prior=index['previous_original_proof'];assert sha(ROOT/prior['path'])==prior['sha256']
    for name,expected in prior['archives'].items():assert sha(PROOFS/name)==expected
    profile=qualification['qualified_tensor_profile']
    assert qualification['pipeline_graph_version']==4 and set(qualification['pipeline_source_sha256'])==set(PIPELINES['manipulation_student'])
    assert len(qualification['pipeline_source_sha256'])==16
    for name,expected in qualification['pipeline_source_sha256'].items():assert sha(ROOT/name)==expected
    assert profile['cuda_execution_profile']==ACT9_CUDA_DETERMINISTIC_PROFILE and profile['runtime_constraints']==ACT9_RUNTIME_CONSTRAINTS
    first=result['student']['runtime_profile_receipt']
    assert first['CUDA_initialized_before_configuration'] is False and first['environment_validated_before_configuration']
    assert first['CUDA_initialized_when_rechecked'] is True  # metadata probe happened after the private first receipt
    assert qualification['optimizer_steps']==8 and qualification['official_saved_processors_executed'] and qualification['reload_max_abs_error']==0
    assert resume['CUDA_process_resume_verified'] and not resume['deterministic_candidate_only']
    assert resume['model_optimizer_losses_all_RNG_sampler_exactly_equal'] and resume['old_process_gone']
    assert resume['reference']['final_weights_sha256']==resume['resumed']['final_weights_sha256']
    assert resume['resumed']['training_resume']['resumed_from_step']==4 and resume['resumed']['training_resume']['optimizer_steps_executed_this_process']==4
    assert index['production_profile_source_qualified'] and not index['production_binding_published']
    assert index['independent_development_successes']==0 and not index['physical_skill_qualified']


def test_historical_fused106_CPU_proof_remains_bound_to_its_frozen_PROFILE4_source():
    comparison=read('SOURCE04_PROFILE4_20261026_CHARACTERIZATION.json')
    assert comparison['weights_equal_prior_qualification'] and comparison['CPU_flags_unchanged']
    assert comparison['new_runtime_profile_helper_in_source'] and not comparison['task_success_claimed']
    fused=read('ROSMASTER_ACT106_20261026_PROFILE4_CPU_QUALIFICATION.json')
    assert fused['optimizer_steps']==8 and fused['device']=='cpu' and fused['feature_dimension']==106 and fused['action_dimension']==9
    assert fused['reload_max_abs_error']==0 and not fused['GPU_qualified'] and not fused['physical_io']
    # This immutable fused proof predates the official Dataset loader. Preserve
    # its exact source against its original archive, never relabel it current.
    index=json.loads((ROOT/'_registry/evidence/native6-queue-profile4-current-20261026.json').read_bytes())
    tree_name='NATIVE6_QUEUE_PROFILE4_20261026_SOURCE_TREE.json'
    assert sha(PROOFS/tree_name)==index['archives'][tree_name]
    tree=read(tree_name)
    for name,expected in fused['runtime']['source_sha256'].items():assert tree[name]==expected
