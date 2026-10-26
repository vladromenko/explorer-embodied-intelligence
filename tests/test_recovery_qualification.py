"""Actual isolated subprocess/loopback loss qualification; no production services."""
import importlib.util
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('qualify_recovery', ROOT/'scripts/qualify_recovery.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
module.source_imports(ROOT)


def test_actual_outbox_process_crashes_outage_lost_ack_and_reconnect(tmp_path):
    result=module.outbox_probe(ROOT,tmp_path/'outbox')
    assert result['passed']
    assert result['preserved_pending_after_outage_and_SIGKILL']==3
    assert result['preserved_pending_after_durable_ingest_without_ACK']==3
    assert result['central_unique_events']==result['acknowledged_events']==3
    assert len(result['HTTP_requests'])==4
    assert sum(not request['inserted'] for request in result['HTTP_requests'])==1
    assert result['immutable_event_bodies_preserved'] and result['conflicting_outbox_identity_rejected']
    assert not result['physical_WiFi_interrupted']


def test_cancelled_action_and_lease_do_not_replay_after_process_restart(tmp_path):
    result=module.epoch_probe(ROOT,tmp_path/'epochs')
    assert result['passed'] and result['stale_action_and_lease_rejected_before_and_after_process_restart']
    assert result['original_executor_calls_after_restart']==[]
    assert result['physical_commands_dispatched']==0
    assert not result['physical_controller_reset_or_reboot_tested']
