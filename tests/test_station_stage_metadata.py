"""Native host phase annotations, existing recorder only; strict no-hardware IO."""
import hashlib
import json
from pathlib import Path
import tarfile

import pytest
from explorer_shared.so101_station import inspect_native_phase_annotation

from test_station_recording_provenance import station_fixture,sample,finish


def body(episode,phase,request):
    return dict(episode_id=episode,phase_id=phase,request_id=request,annotation_source='software-coordinator',phase_scope='coordinator_phase',
        coordinator_context=dict(clock_domain='separate-test-coordinator',monotonic_ns=123,boot_id='coordinator-boot'))


def test_native_clock_annotations_snapshot_in_existing_sample_context_and_final_archive(tmp_path,monkeypatch):
    station=station_fixture(tmp_path);episode=station._start_record({'phase_id':'observe_select'})['episode_id']
    with monkeypatch.context() as patch:
        patch.setattr('explorer_shared.so101_station.time.monotonic_ns',lambda:1_000_000_000)
        first=station.record_stage(body(episode,'grasp','stage-1'))
    requested,accepted=sample(station,1_000_000_100)
    with monkeypatch.context() as patch:
        patch.setattr('explorer_shared.so101_station.time.monotonic_ns',lambda:1_100_000_000)
        second=station.record_stage(body(episode,'place_release','stage-2'))
    sample(station,1_100_000_100)
    result,manifest=finish(station);rows=manifest['samples']
    assert first['accepted'] and not first['durable'] and not first['physical_commands_dispatched']
    assert first['annotation']['native_stamp']['monotonic_ns']==1_000_000_000
    assert first['annotation']['native_stamp']['clock_domain']==station.clock_domain and first['annotation']['native_stamp']['boot_id']==station.boot
    assert first['annotation']['coordinator_context']['monotonic_ns']==123
    assert not first['annotation']['camera_exposure_timestamp_available'] and not first['annotation']['contact_or_task_success_inferred']
    assert rows[0]['source_context']['native_phase_annotation']['phase_id']=='grasp'
    assert rows[1]['source_context']['native_phase_annotation']['phase_id']=='place_release'
    assert rows[0]['source_context']['phase_id']==rows[1]['source_context']['phase_id']=='observe_select'
    assert rows[0]['source_context']['native_phase_annotation']['native_stamp']['monotonic_ns']<=rows[0]['state_monotonic_ns']
    assert rows[0]['requested_action']==requested and rows[0]['accepted_action']==accepted
    interpreted=inspect_native_phase_annotation(rows[0],episode_id=episode)
    assert interpreted['available_for_state_sample'] and interpreted['phase_id']=='grasp' and not interpreted['available_for_camera_exposure']
    wrong_boot=json.loads(json.dumps(rows[0]));wrong_boot['boot_id']='different-boot'
    assert not inspect_native_phase_annotation(wrong_boot,episode_id=episode)['available_for_state_sample']
    assert [event['sequence'] for event in manifest['stage_events']]==[0,1]
    assert manifest['stage_events'][0]['annotation_id']==first['annotation']['annotation_id']
    first['annotation']['phase_id']='caller-mutation'
    assert manifest['stage_events'][0]['phase_id']=='grasp'
    archive=station.archive(episode)
    with tarfile.open(archive) as bundle:
        stored=json.load(bundle.extractfile('manifest.json'))
        assert stored['stage_events']==manifest['stage_events'] and stored['samples'][0]['source_context']['native_phase_annotation']['phase_id']=='grasp'


def test_same_request_returns_original_native_stamp_but_changed_intent_is_rejected(tmp_path,monkeypatch):
    station=station_fixture(tmp_path);episode=station._start_record({})['episode_id'];request=body(episode,'grasp','same')
    with monkeypatch.context() as patch:
        patch.setattr('explorer_shared.so101_station.time.monotonic_ns',lambda:1_000_000_000)
        first=station.record_stage(request)
    second=station.record_stage(request)
    assert first['annotation']==second['annotation'] and second['idempotent_retry'] and len(station.record['stage_events'])==1
    with pytest.raises(ValueError,match='identity changed'):station.record_stage(dict(request,phase_id='transport'))
    assert len(station.record['stage_events'])==1


@pytest.mark.parametrize('change',[{'episode_id':'different'},{'phase_id':'bad phase name'},{'request_id':''},{'native_stamp':{'monotonic_ns':0}},
    {'coordinator_context':{'invalid':float('nan')}},{'coordinator_context':{'oversize':'x'*3000}},{'phase_scope':'attainment_verified'},{'coordinator_context':[]}])
def test_wrong_or_forged_stage_metadata_never_changes_active_context_or_commands(tmp_path,change):
    station=station_fixture(tmp_path);episode=station._start_record({})['episode_id'];before=json.dumps(station.record,sort_keys=True)
    with pytest.raises(ValueError):station.record_stage(dict(body(episode,'grasp','new'),**change))
    assert json.dumps(station.record,sort_keys=True)==before and station.last_command is None


def test_inactive_and_finished_recording_cannot_be_annotated_or_rewritten(tmp_path):
    station=station_fixture(tmp_path)
    with pytest.raises(ValueError,match='active'):station.record_stage(body('not-active','grasp','none'))
    episode=station._start_record({})['episode_id'];station.record_stage(body(episode,'grasp','one'));result,_=finish(station)
    path=Path(result['manifest']);before=path.read_bytes()
    with pytest.raises(ValueError,match='active'):station.record_stage(body(episode,'transport','after-finish'))
    assert path.read_bytes()==before


def test_annotation_has_no_second_disk_writer_and_caller_must_verify_sample_clock_causality(tmp_path,monkeypatch):
    station=station_fixture(tmp_path);episode=station._start_record({})['episode_id'];path=station.root/'recordings'/episode/'manifest.json';before=path.read_bytes()
    with monkeypatch.context() as patch:
        patch.setattr('explorer_shared.so101_station.time.monotonic_ns',lambda:2_000_000_000)
        receipt=station.record_stage(body(episode,'grasp','future-relative-to-fixture'))
    assert path.read_bytes()==before and not receipt['durable']
    sample(station,1_000_000_000);_,manifest=finish(station)
    row=manifest['samples'][0];annotation=row['source_context']['native_phase_annotation']
    assert annotation['native_stamp']['monotonic_ns']>row['state_monotonic_ns']
    assert not inspect_native_phase_annotation(row,episode_id=episode)['available_for_state_sample']
    assert annotation['sample_association']=='source_context_snapshot_consumer_must_verify_stamp_against_native_sample_clock'
    assert 'not_camera_exposure' in annotation['timestamp_basis'] and not annotation['contact_or_task_success_inferred']
