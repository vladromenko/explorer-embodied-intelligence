"""Exact current source03 CPU queue evidence; no model or hardware is constructed."""
import hashlib
import json
from pathlib import Path

ROOT=Path(__file__).parents[1]


def test_actual_native03_queue_proof_keeps_source_project_and_all_current_source_pins():
    r=json.loads((ROOT/'03-lerobot-teaching/docs/NATIVE03_QUEUE_CPU_R3_20261027.json').read_bytes())
    assert r['schema']=='explorer_native03_queued_cpu_qualification_v1'
    assert len(r['qualified_source_files_sha256'])==21
    for group in ('qualified_source_files_sha256','qualified_materialized_archive_sources_sha256','qualified_additional_source_files_sha256'):
        for name,checksum in r[group].items():assert hashlib.sha256((ROOT/name).read_bytes()).hexdigest()==checksum
    assert set(r['qualified_additional_source_files_sha256'])=={'00-control-center/src/explorer/mobile_dataset_evidence.py'}
    old=r['previous_original_proof'];assert hashlib.sha256((ROOT/old['path']).read_bytes()).hexdigest()==old['sha256']
    result=r['result'];assert result['actual_train_and_eval_project_id']=='03' and result['actual_optimizer_updates']==4
    assert result['save_reload_passed'] and result['reload_max_abs_error']==0 and result['different_processes']
    assert result['queued_DEV_optimizer_updates']==0 and result['queued_DEV_samples']==10 and result['weights_unchanged_in_DEV']
    assert r['actual_source_binding']['source_project_ids']==['03'] and r['source_native_imports']==5
    assert r['train_result']['feature_dimension']==106 and r['train_result']['output_action_dimension']==9
    export=r['train_result']['official_dataset'];assert export['original_train_demo_count']==3 and export['train_segment_episode_count']==6 and not export['sealed_payloads_exported']
    assert len({row['worker']['pid'] for row in r['launches']})==2
    evaluated=r['evaluation_job'];assert evaluated['status']=='completed' and evaluated['project_id']=='03'
    assert evaluated['result']['source_network_requests_sent']==evaluated['result']['physical_control_requests_sent']==0
    assert not evaluated['result']['dispatch_performed'] and evaluated['result']['execution_source_commit']==r['source_commit']
    assert r['max_observed_kernel_cgroup_memory_peak_bytes']<=r['resource_limits']['memory_max'] and r['max_observed_swap_bytes']==0
    assert all(value=='0' for value in r['last_memory_events'].values())
    scope=r['scope'];assert scope['all_inputs_are_software_fixtures'] and scope['real_demonstrations']==scope['physical_control_requests_sent']==0
    assert not scope['production_state_used'] and not scope['new_physical_path_deployed'] and scope['real_leader_continuity']=='OPEN'
