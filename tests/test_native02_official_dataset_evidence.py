"""Current official Dataset/native106 proof is distinct from historical PROFILE4."""
import hashlib
import json
from pathlib import Path

ROOT=Path(__file__).parents[1];PROOFS=ROOT/'shared/training'


def read(name):return json.loads((PROOFS/name).read_bytes())
def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def test_current_official_Dataset_source_is_exactly_bound_to_actual_direct_and_queue_proofs():
    direct=read('NATIVE02_LEROBOT_CPU_20261027_QUALIFICATION.json');queue=read('NATIVE02_QUEUED_CPU_R2_20261027_QUALIFICATION.json')
    assert direct['results']['official_lerobot_roundtrip_passed'] and direct['results']['segment_chunk_padding_passed']
    assert direct['results']['actual_optimizer_updates_total']==16 and direct['results']['new_process_additional_steps']==4
    assert direct['results']['uninterrupted8_weights_and_losses_identical'] and direct['results']['shadow_dispatch_performed'] is False
    # Unchanged computational leaf bytes are covered by both independent paths.
    for name,expected in direct['qualified_source_files_sha256'].items():
        if name.startswith('shared/explorer_shared/learning/'):
            assert sha(ROOT/name)==expected
            if name in queue['qualified_materialized_archive_sources_sha256']:
                assert queue['qualified_materialized_archive_sources_sha256'][name]==expected
    assert len(queue['qualified_source_files_sha256'])==19
    for name,expected in queue['qualified_source_files_sha256'].items():assert sha(ROOT/name)==expected
    for name,expected in queue['qualified_materialized_archive_sources_sha256'].items():assert sha(ROOT/name)==expected
    results=queue['results'];assert results['actual_popen_bootstrap_job_runner'] and results['authenticated_ASGI_queue_and_models']
    assert results['actual_optimizer_updates']==4 and results['save_reload_passed'] and results['weights_changed']
    assert results['feature_dimension']==106 and results['action_dimension']==9
    assert results['official_dataset']['target_dimension']==9 and results['official_dataset']['original_train_demo_count']==3
    assert results['official_dataset']['train_segment_episode_count']==6 and not results['official_dataset']['sealed_payloads_exported']
    assert results['queued_DEV_optimizer_updates']==0 and results['queued_DEV_samples']==10 and results['eval_weights_unchanged']
    assert results['eval_execution_source_commit']==queue['qualification_source_commit']==queue['frozen_source']['source_commit']
    assert results['eval_source_network_requests']==0 and results['eval_dispatch_performed'] is False
    assert results['all_workers_exited_and_reservations_released'] and results['source_19pins_rechecked'] and results['git_clean_after_compute']
    train,evaluation=queue['runtime_admissions']['train'],queue['runtime_admissions']['evaluation']
    assert train['resources']['cpu_cores']==evaluation['resources']['cpu_cores']==2
    assert train['resources']['memory_bytes']==4*1024**3 and evaluation['resources']['memory_bytes']==2*1024**3
    assert train['resources']['gpu_slots']==evaluation['resources']['gpu_slots']==0 and not evaluation['preemptible']
    assert train['bootstrap_admitted'] and evaluation['bootstrap_admitted']
    assert train['worker']['pid']!=evaluation['worker']['pid'] and train['worker']['boot_id']==evaluation['worker']['boot_id']
    assert train['worker']['start_ticks']<evaluation['worker']['start_ticks'] and train['launch_nonce']!=evaluation['launch_nonce']
    for proof in (direct,queue):
        assert proof['scope']['all_inputs_are_software_fixtures'] and proof['scope']['real_demonstrations']==0
        assert proof['scope']['production_state_used'] is False and proof['scope']['physical_control_requests_sent']==0
        assert all(value=='0' for value in proof['last_memory_events'].values()) and proof['max_observed_swap_bytes']==0
        assert proof['max_observed_kernel_cgroup_memory_peak_bytes']<=4*1024**3
    assert not queue['scope']['GPU_qualified'] and not queue['scope']['independent_full_task_success'] and not queue['scope']['public_production_queue_tested']


def test_archived_fused106_receipt_and_original_source_tree_bytes_are_preserved():
    queue=read('NATIVE02_QUEUED_CPU_R2_20261027_QUALIFICATION.json')
    original=queue['previous_original_proof'];assert sha(PROOFS/original['path'])==original['sha256']
    assert sha(PROOFS/'ROSMASTER_ACT106_20261026_PROFILE4_CPU_QUALIFICATION.json')==queue['historical_fused106_receipt_sha256']
    assert sha(PROOFS/'NATIVE6_QUEUE_PROFILE4_20261026_SOURCE_TREE.json')==queue['historical_profile4_source_tree_sha256']
