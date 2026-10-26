"""Recording serialization only, strict no-hardware fakes and temporary files."""
import hashlib
import io
import json
from pathlib import Path
import threading
from types import SimpleNamespace

from PIL import Image
import pytest
from explorer_shared.so101_station import SO101Station


def prohibited(*_args,**_kwargs):raise AssertionError('No hardware/servo operation belongs in recording provenance')


def station_fixture(tmp_path):
    configuration=tmp_path/'arms.json';configuration.write_text(json.dumps({'roles':{}}))
    camera=SimpleNamespace(camera_id='pi-overview',device='software-fixture-not-device',latest=prohibited,stale_s=2)
    station=SO101Station(configuration,tmp_path,camera)
    station.mode='teleoperation';station._write=station._positions=station._connect=station._enable_hold=prohibited
    station.profiles={role:{'calibration_sha256':hashlib.sha256(role.encode()).hexdigest(),'verified_on_connected_hardware':False} for role in ('leader','follower')}
    return station


def frame(stamp):
    memory=io.BytesIO();Image.new('RGB',(16,12),'blue').save(memory,format='JPEG')
    return SimpleNamespace(jpeg=memory.getvalue(),frame_id='software-frame-'+str(stamp),monotonic_ns=stamp,
        timestamp_basis='software_fixture_host_receive',width=16,height=12)


def finish(station):
    writer=threading.Thread(target=station._record_writer,daemon=True);writer.start()
    try:
        result=station._finish_record('success')
        return result,json.loads(Path(result['manifest']).read_text())
    finally:
        station.record_queue.put(None);writer.join(2);assert not writer.is_alive()


def sample(station,stamp,*,leader=None,stale=False,follower=None):
    follower=follower or [1,2,3,4,5,6];leader=leader or [11,12,13,14,15,16]
    requested=[110,20,30,40,50,60];accepted=[100,20,30,40,50,60];dispatch=stamp+10
    station.measurements={'leader':leader,'follower':follower,'monotonic_ns':stamp-1 if stale else stamp,'clock_domain':station.clock_domain}
    station.last_command={'requested':requested,'dispatched':accepted,'monotonic_ns':dispatch,'clock_domain':station.clock_domain,
        'control_epoch':station.control_epoch,'sequence':7,'source_observation':{'frame_id':'prior-source'}}
    station._sample(frame(stamp-5),follower,requested,accepted,stamp,dispatch)
    return requested,accepted


def test_records_actual_existing_cycle_leader_requested_accepted_host_dispatch_and_preaction_follower(tmp_path):
    station=station_fixture(tmp_path)
    context={'project_id':'06','skill_id':'software-skill','skill_version':2,'session_id':'source-session','scene_id':'source-scene',
        'phase_id':'reach','task_type':'joint_trajectory','context':{'object':'fixture','observation_basis':'software only'}}
    station._start_record(context);requested,accepted=sample(station,1_000_000_000)
    result,saved=finish(station);row=saved['samples'][0]
    assert saved['recording_provenance_version']==2 and saved['project_id']=='06'
    assert saved['boot_id']==station.boot and saved['session_id']=='source-session'
    assert saved['source_context']['skill_version']==2 and row['source_context']==saved['source_context']
    assert saved['producer_runtime_binary_identity_verified'] is False
    assert saved['camera_source']['camera_id']=='pi-overview' and saved['camera_calibration'] is None
    assert row['leader_state']==[11,12,13,14,15,16] and row['leader_state_monotonic_ns']==row['state_monotonic_ns']
    assert row['requested_action']==requested and row['accepted_action']==row['dispatched_action']==row['action']==accepted
    assert row['state']==row['measured_follower']['values']==[1,2,3,4,5,6]
    assert row['state']!=row['action'] and row['next_measured_follower'] is None
    assert row['source_valid_masks']['leader_state']==[True]*6
    receipt=row['dispatch_receipt'];assert receipt['available'] and receipt['stage']=='host_bus_write_completed'
    assert receipt['monotonic_ns']==row['action_monotonic_ns'] and receipt['clock_domain']==station.clock_domain
    from explorer_sim.so101_dataset_qualification import inspect_station_episode
    evidence=inspect_station_episode(result['manifest'])
    assert evidence['technical_dataset_ready']
    assert evidence['recorded_channels']['leader_measured']['complete'] and evidence['recorded_channels']['follower_dispatched_separate']['complete']
    assert not evidence['full_teaching_provenance_ready'] and 'camera_calibration_not_recorded' in evidence['missing_facts']
    assert not receipt['MCU_acknowledgement_verified'] and not receipt['motor_application_time_verified'] and not receipt['attainment_verified']


def test_stale_leader_and_unmatched_write_receipt_remain_null_and_masked(tmp_path):
    station=station_fixture(tmp_path);station._start_record({'project_id':'06'})
    sample(station,1_000_000_000,stale=True)
    station.last_command=None
    station._sample(frame(1_100_000_000),[0]*6,[0]*6,[0]*6,1_100_000_001,1_100_000_002)
    result,saved=finish(station);first,second=saved['samples']
    from explorer_sim.so101_dataset_qualification import inspect_station_episode
    assert 'new_station_host_dispatch_receipt_not_verified' in inspect_station_episode(result['manifest'])['reasons']
    assert first['leader_state'] is None and first['source_valid_masks']['leader_state']==[False]*6
    assert second['dispatched_action'] is None and second['source_valid_masks']['dispatched_action']==[False]*6
    assert not second['dispatch_receipt']['available'] and second['dispatch_receipt']['stage'] is None
    assert second['state']==[0]*6 and second['source_valid_masks']['follower_measured']==[True]*6
    assert second['accepted_action']==[0]*6 and second['source_valid_masks']['accepted_action']==[True]*6


def test_later_measured_follower_remains_separate_from_previous_target_without_attainment_claim(tmp_path):
    station=station_fixture(tmp_path);station._start_record({'project_id':'06'})
    sample(station,1_000_000_000);sample(station,1_100_000_000,follower=[2,3,4,5,6,7])
    _,saved=finish(station);first,second=saved['samples']
    assert second['state']!=first['action']
    assert first['next_measured_follower'] is None and not first['next_measured_follower_available']
    assert second['state_monotonic_ns']>first['action_monotonic_ns']
    assert not first['dispatch_receipt']['attainment_verified'] and not second['dispatch_receipt']['attainment_verified']


def test_old_completed_source_bytes_unchanged_and_new_context_is_validated_before_directory_creation(tmp_path):
    old=tmp_path/'station/recordings/old/manifest.json';old.parent.mkdir(parents=True)
    raw=b'{"state":"complete","schema_version":1,"legacy":true}\n';old.write_bytes(raw)
    station=station_fixture(tmp_path)
    for body in ({'skill_id':'missing-version'},{'skill_id':'skill','skill_version':True},{'context':{'bad':float('nan')}}):
        with pytest.raises(ValueError):station._start_record(body)
    assert old.read_bytes()==raw and set(old.parent.parent.iterdir())=={old.parent}
    station._start_record({'project_id':'personal'})
    _,saved=finish(station)
    assert old.read_bytes()==raw and saved['schema_version']==1 and saved['project_id']=='personal'
