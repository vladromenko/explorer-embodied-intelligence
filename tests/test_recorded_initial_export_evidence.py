"""Actual frozen software evidence consistency; no private files/models/physics."""
import hashlib
import json
from pathlib import Path

ROOT=Path(__file__).parents[1]
AUDIT=ROOT/'research/audits'
PREFIX='recorded-initial-capture-export-a1-20261027-'


def path(name):return AUDIT/(PREFIX+name+'.json')
def read(name):return json.loads(path(name).read_bytes())
def sha(name):return hashlib.sha256(path(name).read_bytes()).hexdigest()


def test_new_real_CAPTURE_is_distinct_failed_development_fixture_not_historical_or_full_skill():
    capture,raw,fixture,trace,binding=[read(name) for name in ('capture','source-result','scripted-provider','source-trace','source-fixture-binding')]
    assert binding['summary_sha256']==sha('summary')
    assert binding['source_trace_sha256']==sha('source-trace')==raw['source_manifest_sha256']
    assert binding['scripted_provider_sha256']==sha('scripted-provider')==trace['source_files_sha256']['scripted-provider.json']
    assert trace['source_kind']=='SCRIPTED_SIM' and trace['split']['partition']==binding['actual_source_partition']=='development'
    assert {frame['phase_id'] for frame in trace['frames']}=={'approach','grasp','lift','transport','place_verify'}
    assert not fixture['actual_source_arm_executed'] and not fixture['object_expert_qualified'] and not fixture['closed_loop_learned_source']
    assert capture['actual_MuJoCo_CAD_provider'] and capture['initial_state_status']==raw['initial_state']['status']=='RECORDED'
    assert capture['actual_target_RGB_frames']==1 and capture['RGB_shape']==[240,320,3]
    assert capture['target_advance_calls']==capture['target_finite_steps']==raw['frames']==0
    assert capture['source_job_status']=='failed' and raw['failure']['code']=='POST_CAPTURE_SOFTWARE_DIAGNOSTIC_STOP'
    assert not raw['full_target_simulation_success'] and not raw['eligible_for_target_dataset'] and not raw['optimizer_partition_allowed']
    assert not raw['learned_target_actor_executed'] and not raw['physical_io'] and not raw['physical_success_validated']


def test_actual_MJB_CAD_and_registered_capture_sources_are_portably_bound():
    capture,manifest,library=[read(name) for name in ('capture','initial-manifest','source-library')]
    assert sha('initial-manifest')==capture['initial_manifest_sha256']==library['actual_initial_manifest_sha256']
    assert manifest['schema']=='explorer_stationary_sim_initial_state_v1' and manifest['profile']=='explorer_manipulation_stationary_initial_v1'
    assert manifest['payloads']['compiled-model.mjb']['bytes']==capture['compiled_MJB_bytes']==153859356
    assert len(manifest['assets'])==capture['CAD_assets']==20
    assert manifest['source_bindings']['initial_capture_helper']['sha256']==manifest['runtime']['helper_sha256']==library['recorded_runtime_helper_sha256']
    assert manifest['source_bindings']['capture_smoke_driver']['sha256']==capture['driver_sha256']
    assert library['recorded_payloads']==manifest['payloads'] and library['captured_original_file_count']==33
    assert manifest['original_runtime_OS_closure']=='PARTIAL' and manifest['initial_RGB_render_reexecution']=='UNQUALIFIED'
    assert not manifest['physical_io'] and not manifest['retroactive_capture']


def test_actual_authenticated_ID_only_queue_download_and_new_PID_exact_restore_are_scoped():
    capture,export,library,restored=[read(name) for name in ('capture','export','source-library','independent-restore')]
    assert export['actual_loopback_HTTP'] and export['authenticated_API'] and export['id_only_selection']
    assert export['actual_Supervisor_bootstrap_job_runner'] and export['worker_reaped']
    assert library['export_job']['bootstrap_admitted'] and library['export_job']['worker_entry_admitted'] and library['export_job']['worker_process_exit_code']==0
    assert library['source_job']['status']=='failed' and library['export_job']['status']=='completed'
    assert export['source_row_bytes_unchanged'] and export['source_files_unchanged']
    assert [row['status'] for row in export['HTTP_trace']]==[401,200,403,400,200,409,200,400]
    assert export['independent_restore_PID']==restored['pid']!=export['API_process_PID']
    assert restored['pid'] not in {row['pid'] for row in export['worker_processes'].values()}
    assert restored['original_source_read_positive_control_blocked'] and restored['original_source_open_attempts_during_restore']==0
    assert restored['exact_captured_job_files']==len(restored['matched_original_files_sha256'])==len(capture['job_files'])==33
    assert restored['matched_original_files_sha256']=={name:entry['sha256'] for name,entry in capture['job_files'].items()}
    assert restored['portable_byte_admission']['manifest_sha256']==capture['initial_manifest_sha256']
    result=export['export_result'];assert result['archive_bytes']==196956160 and result['archive_sha256']==restored['archive_sha256']
    assert result['verification']==restored['independent_restore_verification']=='PARTIAL'
    assert not restored['numeric_reexecution_performed'] and not restored['RGB_reexecution_performed']
    assert not export['numerical_reexecution_verified_by_export'] and not export['RGB_reexecution_verified_by_export']
    assert not library['auth_configuration_exported'] and not library['scientific_source_blobs_executed']


def test_actual_unit_kernel_budgets_and_all_published_receipt_hashes():
    summary=read('summary')
    for name,expected in summary['evidence_files_sha256'].items():
        assert Path(name).name==name and hashlib.sha256((AUDIT/name).read_bytes()).hexdigest()==expected
    for name,cap in [('capture-resources',3*1024**3),('export-resources',2*1024**3)]:
        row=read(name)
        assert row['failure'] is None and row['limits']['memory.max']==str(cap) and row['limits']['memory.swap.max']=='0'
        assert 0<row['memory_peak_bytes']<=cap and row['swap_peak_bytes']==0
        assert row['memory_events'].splitlines()==['low 0','high 0','max 0','oom 0','oom_kill 0','oom_group_kill 0']
    assert summary['verification']=='PARTIAL' and summary['original_runtime_OS_closure']=='PARTIAL'
    assert not summary['physical_success'] and not summary['source_full_skill_success'] and not summary['master_prompt_completed']
    assert not summary['production_state_writes'] and not summary['physical_io']
