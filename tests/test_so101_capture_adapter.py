"""Original recorder + injected fake source only; no bus/camera/control thread."""
from copy import deepcopy
import hashlib
import io
import json
from pathlib import Path
import sys
import tarfile
import threading
from types import SimpleNamespace

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'04-so101-source-learning/src'))
from explorer_so101.capture_adapter import StationCaptureAdapter,SCHEMA
from explorer_so101 import capture_adapter as implementation
from explorer_shared.native_capture_approval import validate_source_approval
from explorer_shared.so101_station import SO101Station


class Clock:
    value=2**53+1_000_000_000
    def __call__(self):return self.value


class FakeSource:
    """Use unchanged original record_start/_sample/file-writer/finish/archive.

    The operational motor/camera functions are fatal stubs. The profile's True
    flag is fixture input, never a claim about any connected hardware.
    """
    def __init__(self,root):
        config=root/'arms.json';config.write_text(json.dumps({'roles':{}}))
        self.station=SO101Station(config,root,SimpleNamespace(camera_id='pi-logitech-fixture',device='NO_DEVICE'))
        self.station.boot='fixture-pi-boot';self.station.clock_domain='pi:fixture-pi-boot'
        self.station.mode='teleoperation';self.station.control_epoch='fixture-original-native-epoch'
        def prohibited(*args,**kwargs):raise AssertionError('No control/serial/camera operation is permitted')
        self.station._connect=self.station._write=self.station._positions=self.station._enable_hold=prohibited
        self.station.profiles={role:{'calibration_sha256':hashlib.sha256(role.encode()).hexdigest(),
            'verified_on_connected_hardware':True} for role in ('leader','follower')}
        self.station.measurements={'clock_domain':self.station.clock_domain,'monotonic_ns':Clock.value}
        self.calls=[]
        self.writer=threading.Thread(target=self.station._record_writer,daemon=True);self.writer.start()

    def status(self):self.calls.append('status');return self.station.status()
    def start(self,body):
        self.calls.append('record/start');result=self.station.command('record/start',body)
        self.station.record['source_kind']='software_fixture'
        return result
    def finish(self,body):self.calls.append(('record/finish',deepcopy(body)));return self.station.command('record/finish',body)
    def manifest(self,ident):return (self.station.root/'recordings'/ident/'manifest.json').read_bytes()
    def archive(self,ident):self.calls.append('archive');return self.station.archive(ident)
    def sample(self,at):
        station=self.station;state=[1,2,3,4,5,6];requested=[110,20,30,40,50,60];accepted=[100,20,30,40,50,60]
        station.measurements={'leader':[11,12,13,14,15,16],'follower':state,'monotonic_ns':at,
            'clock_domain':station.clock_domain}
        station.last_command={'requested':requested,'dispatched':accepted,'monotonic_ns':at+10,
            'clock_domain':station.clock_domain,'control_epoch':station.control_epoch,'sequence':7}
        frame=SimpleNamespace(jpeg=b'\xff\xd8FAKE_RAW_RGB\xff\xd9',frame_id='fake-'+str(at),monotonic_ns=at-5,width=4,height=3)
        station._sample(frame,state,requested,accepted,at,at+10);station.record_queue.join()
    def close_writer(self):
        # Never Station.close(): its original local STOP belongs to another task.
        self.station.record_queue.put(None);self.writer.join(2);assert not self.writer.is_alive()


def context():
    return {'project_id':'04','experiment_slug':'so101_source_learning','experiment_numbering_version':2,
        'skill_id':'a94d8a92-90ca-40f7-aa09-c1f822b04046','skill_version':1,'skill_spec_sha256':'a'*64,
        'session_id':'fixture-session','scene_id':'fixture-scene','split':'train','phase_id':'reach',
        'context':{'variant_id':'fixture-variant','object_instance_id':'fixture-object','source_kind':'software_fixture'}}


@pytest.fixture
def capture(tmp_path):
    source=FakeSource(tmp_path);clock=Clock()
    def make(**overrides):
        options=dict(status=source.status,record_start=source.start,record_finish=source.finish,
            read_manifest=source.manifest,archive_original=source.archive,clock_domain=source.station.clock_domain,
            boot_id=source.station.boot,clock=clock)
        options.update(overrides);return StationCaptureAdapter(tmp_path/'adapter',**options)
    adapter=make()
    yield adapter,source,clock,make
    adapter.close();source.close_writer()


def finish_with_sample(adapter,source,clock):
    start=clock();ident=adapter.start(context(),'fixture-owner')['recording_id']
    source.sample(start+110_000_000);clock.value=start+130_000_000
    return ident,adapter.finish(ident)


def test_pause_keeps_actual_original_writer_bytes_and_short_pause_cuts_chunks(capture):
    adapter,source,clock,_=capture;start=clock()
    assert not source.calls and adapter.status()['capture'] is None
    ident=adapter.start(context(),'Владелец')['recording_id']
    source.sample(start+110_000_000)
    clock.value=start+120_000_000;adapter.pause(ident)
    source.sample(start+220_000_000)
    clock.value=start+230_000_000;adapter.resume(ident)
    source.sample(start+330_000_000)
    clock.value=start+350_000_000;sealed=adapter.finish(ident,'unknown')
    assert sealed['schema']==SCHEMA and sealed['source_kind']=='software_fixture'
    assert sealed['state']=='completed' and sealed['operator_outcome']=='unknown'
    assert [row['recording_segment_id'] for row in sealed['sample_selection']]==[0,None,1]
    assert sealed['sample_selection'][0]['action_monotonic_ns']==start+110_000_010
    assert sealed['lifecycle']['pause_intervals']==[{'start_monotonic_ns':start+120_000_000,'end_monotonic_ns':start+230_000_000}]
    assert sealed['raw_paused_samples_retained'] and not sealed['physical_success_inferred']
    original_raw=source.manifest(ident);original=json.loads(original_raw);assert len(original['samples'])==3
    assert all(sample['action']==[100,20,30,40,50,60] and sample['requested_action']==[110,20,30,40,50,60] for sample in original['samples'])
    original_archive=source.archive(ident).read_bytes()
    with tarfile.open(adapter.archive(ident)) as archive:
        assert json.load(archive.extractfile('manifest.json'))==sealed
        assert archive.extractfile('source/recording.tar').read()==original_archive
    assert sealed['original_manifest']['sha256']==hashlib.sha256(original_raw).hexdigest()
    before=original_raw,hashlib.sha256(original_archive).hexdigest()
    assert adapter.recordings()[0]['transfer_ready'] is False
    checksum=hashlib.sha256(adapter.archive(ident).read_bytes()).hexdigest()
    approval=adapter.review(ident,'approve','Владелец',checksum)
    assert validate_source_approval(approval,ident,checksum,actor='Владелец')['verified']
    assert adapter.recordings()[0]['transfer_ready']
    rejection=adapter.review(ident,'reject','Владелец',checksum)
    assert rejection['source_review']['previous_review_sha256']==approval['review_sha256']
    assert not adapter.recordings()[0]['transfer_ready']
    assert (source.manifest(ident),hashlib.sha256(source.archive(ident).read_bytes()).hexdigest())==before
    assert len(list((adapter.root/ident/'reviews').glob('*.json')))==2
    assert [call for call in source.calls if isinstance(call,tuple)]==[('record/finish',{'recording_id':ident,'outcome':'unknown'})]


@pytest.mark.parametrize('cancelled',[False,True])
def test_cancel_or_original_interruption_never_becomes_completed_expert(capture,cancelled):
    adapter,source,clock,_=capture;start=clock();ident=adapter.start(context(),'fixture-owner')['recording_id']
    source.sample(start+110_000_000);clock.value=start+120_000_000;adapter.pause(ident)
    if not cancelled:source.station.record_errors[ident]='FAKE_STORAGE_INTERRUPTION'
    clock.value=start+130_000_000
    sealed=adapter.cancel(ident,'fixture-owner') if cancelled else adapter.finish(ident)
    assert sealed['state']==('cancelled' if cancelled else 'interrupted')
    checksum=hashlib.sha256(adapter.archive(ident).read_bytes()).hexdigest()
    with pytest.raises(ValueError,match='cannot be approved'):adapter.review(ident,'approve','fixture-owner',checksum)
    assert adapter.review(ident,'reject','fixture-owner',checksum)['transfer_ready'] is False
    assert not adapter.recordings()[0]['transfer_ready'] and source.station.mode=='teleoperation'
    assert sealed['lifecycle']['pause_intervals'][-1]['end_monotonic_ns'] is None


@pytest.mark.parametrize('change',['foreign_recording','boot','domain','calibration','context','native_epoch'])
def test_source_identity_fences_pause_without_mutating_foreign_source(capture,change):
    adapter,source,clock,_=capture;ident=adapter.start(context(),'fixture-owner')['recording_id']
    if change=='foreign_recording':source.station.record['episode_id']='f'*32
    elif change=='boot':source.station.record['boot_id']='other-boot'
    elif change=='domain':source.station.record['clock_domain']='other-clock'
    elif change=='calibration':source.station.profiles['leader']['calibration_sha256']='f'*64
    elif change=='context':source.station.record['source_context']['skill_version']=99
    else:source.station.control_epoch='another-native-owner'
    with pytest.raises(ValueError):adapter.pause(ident)
    assert adapter.status()['capture']['state']=='RECORDING'
    assert not any(isinstance(call,tuple) for call in source.calls)


@pytest.mark.parametrize('phase',['start','finish'])
def test_unknown_original_return_and_restart_never_clear_or_adopt(capture,phase):
    adapter,source,clock,make=capture;adapter.close()
    if phase=='start':
        def unknown(body):source.start(body);raise TimeoutError('original reply unavailable')
        adapter=make(record_start=unknown)
        with pytest.raises(TimeoutError):adapter.start(context(),'fixture-owner')
        assert adapter.status()['recording_id'] is None
    else:
        def unknown(body):source.finish(body);raise TimeoutError('original finish reply unavailable')
        adapter=make(record_finish=unknown);start=clock();ident=adapter.start(context(),'fixture-owner')['recording_id']
        source.sample(start+110_000_000);clock.value=start+130_000_000
        with pytest.raises(TimeoutError):adapter.finish(ident)
        assert adapter.status()['capture']['state']=='UNCERTAIN'
    assert adapter.status()['recovery_required'];adapter.close()
    before=deepcopy(source.calls);restarted=make()
    try:
        assert restarted.status()['recovery_required'] and source.calls==before
        with pytest.raises(ValueError):restarted.start(context(),'fixture-owner')
        assert source.calls==before
    finally:restarted.close()


def test_stale_finish_and_tampered_original_cannot_seal_or_approve(capture):
    adapter,source,clock,_=capture;start=clock();ident=adapter.start(context(),'fixture-owner')['recording_id']
    with pytest.raises(ValueError):adapter.finish('f'*32)
    assert not any(isinstance(call,tuple) for call in source.calls)
    source.sample(start+110_000_000);clock.value=start+130_000_000
    original=source.manifest
    def changed(recording_id):return original(recording_id)+b' '
    adapter._manifest=changed
    with pytest.raises(ValueError,match='manifest SHA'):adapter.finish(ident)
    assert adapter.status()['recovery_required'] and not adapter.recordings()


def test_cached_status_stays_readonly_while_original_archive_callback_blocks(capture):
    adapter,source,clock,_=capture;start=clock();ident=adapter.start(context(),'fixture-owner')['recording_id']
    source.sample(start+110_000_000);clock.value=start+130_000_000
    entered,release=threading.Event(),threading.Event();real=adapter._archive;result=[]
    def blocked(recording_id):entered.set();assert release.wait(2);return real(recording_id)
    adapter._archive=blocked
    thread=threading.Thread(target=lambda:result.append(adapter.finish(ident)));thread.start();assert entered.wait(1)
    before=list(source.calls)
    assert adapter.status()['capture']['state']=='SEALING' and source.calls==before
    release.set();thread.join(2);assert not thread.is_alive() and result[0]['state']=='completed'


def test_sealed_wrapper_and_archive_tampering_cannot_be_reviewed(capture):
    adapter,source,clock,_=capture;ident,_=finish_with_sample(adapter,source,clock)
    path=adapter.archive(ident);checksum=hashlib.sha256(path.read_bytes()).hexdigest()
    path.write_bytes(path.read_bytes()+b'tampered')
    with pytest.raises(ValueError,match='exact unchanged'):adapter.review(ident,'approve','fixture-owner',checksum)
    with pytest.raises(ValueError,match='archive changed'):adapter.archive(ident)
    assert not adapter.recording_review(ident)['transfer_ready']


def test_close_only_releases_metadata_owner_and_never_calls_original_finish_or_stop(capture):
    adapter,source,clock,_=capture;adapter.start(context(),'fixture-owner');before=list(source.calls)
    adapter.close();assert adapter.status()['closed'] and source.calls==before
    assert source.station.record and source.station.mode=='teleoperation'


def test_competing_original_start_is_never_adopted_or_data_finished(capture):
    adapter,source,clock,_=capture
    def raced(body):
        source.start(dict(body,actor='FOREIGN_FIXTURE_ACTOR'))
        return source.start(body)
    adapter._start=raced
    with pytest.raises(ValueError,match='already active'):adapter.start(context(),'fixture-owner')
    assert adapter.status()['recording_id'] is None and adapter.status()['recovery_required']
    assert source.station.record['demonstration_actor']=='FOREIGN_FIXTURE_ACTOR'
    assert not any(isinstance(call,tuple) for call in source.calls)


def test_original_finish_identity_fence_survives_race_after_status(capture):
    adapter,source,clock,_=capture;ident=adapter.start(context(),'fixture-owner')['recording_id']
    def raced(body):
        source.station.record['episode_id']='f'*32
        return source.finish(body)
    adapter._finish=raced;clock.value+=1
    with pytest.raises(ValueError,match='identity changed'):adapter.finish(ident)
    assert source.station.record['episode_id']=='f'*32 and adapter.status()['recovery_required']


def test_user_ended_native_epoch_can_finish_only_its_data_as_interrupted(capture):
    adapter,source,clock,_=capture;start=clock();ident=adapter.start(context(),'fixture-owner')['recording_id']
    source.sample(start+110_000_000);clock.value=start+130_000_000
    source.station.mode='idle';source.station.control_epoch='USER-ended-or-new-native-epoch'
    result=adapter.finish(ident)
    assert result['state']=='interrupted' and 'NATIVE_OWNER_CHANGED' in result['block_reasons']
    assert source.station.mode=='idle' and source.station.control_epoch=='USER-ended-or-new-native-epoch'
    checksum=hashlib.sha256(adapter.archive(ident).read_bytes()).hexdigest()
    with pytest.raises(ValueError):adapter.review(ident,'approve','fixture-owner',checksum)


@pytest.mark.parametrize('failure',['lifecycle.json','archive-info.json'])
def test_metadata_persistence_failure_never_publishes_or_clears_original_unknown(capture,monkeypatch,failure):
    adapter,source,clock,_=capture;real=implementation.atomic_bytes
    if failure=='archive-info.json':
        start=clock();ident=adapter.start(context(),'fixture-owner')['recording_id']
        source.sample(start+110_000_000);clock.value=start+130_000_000
    def failed(path,content):
        if path.name==failure:raise OSError('FAKE_FSYNC_OR_DISK_FAILURE')
        return real(path,content)
    monkeypatch.setattr(implementation,'atomic_bytes',failed)
    with pytest.raises(OSError):
        adapter.finish(ident) if failure=='archive-info.json' else adapter.start(context(),'fixture-owner')
    assert adapter.status()['recovery_required'] and adapter.status()['capture']['state']=='UNCERTAIN'
    assert adapter.recordings()==[]
    assert source.station.mode=='teleoperation'
    if failure=='lifecycle.json':assert source.station.record is not None


def test_one_metadata_owner_and_package_do_not_load_optional_models(capture):
    adapter,source,clock,make=capture
    with pytest.raises(BlockingIOError):make()
    assert not source.calls and 'torch' not in sys.modules


@pytest.mark.parametrize('kind',['unknown_second_start','old_id_collision'])
def test_failed_later_start_never_rewrites_previous_immutable_capture(capture,kind):
    adapter,source,clock,_=capture;ident,_=finish_with_sample(adapter,source,clock)
    old={path.name:path.read_bytes() for path in (adapter.root/ident).iterdir() if path.is_file()}
    if kind=='unknown_second_start':
        def changed(body):source.start(body);raise TimeoutError('unknown later original reply')
    else:
        def changed(body):return dict(source.start(body),episode_id=ident)
    adapter._start=changed;clock.value+=100
    with pytest.raises((TimeoutError,FileExistsError)):adapter.start(context(),'fixture-owner')
    assert adapter.status()['recovery_required']
    assert {path.name:path.read_bytes() for path in (adapter.root/ident).iterdir() if path.is_file()}==old
    assert adapter.manifest(ident)['state']=='completed'


def test_corrupt_journal_is_preserved_and_failclosed_without_touching_existing_writer(capture):
    adapter,source,clock,make=capture;ident=adapter.start(context(),'fixture-owner')['recording_id'];adapter.close()
    path=adapter.root/ident/'lifecycle.json';path.write_bytes(b'CORRUPT_SOURCE_METADATA_ORIGINAL')
    before=list(source.calls);restarted=make()
    try:
        assert restarted.status()['recovery_required'] and restarted.status()['error']=='LOCAL_CAPTURE_JOURNAL_UNREADABLE'
        assert source.calls==before and path.read_bytes()==b'CORRUPT_SOURCE_METADATA_ORIGINAL'
        with pytest.raises(ValueError):restarted.start(context(),'fixture-owner')
        assert source.calls==before
    finally:restarted.close()


def test_state_before_resume_and_action_after_resume_remains_raw_not_training_sample(capture):
    adapter,source,clock,_=capture;start=clock();ident=adapter.start(context(),'fixture-owner')['recording_id']
    source.sample(start+110_000_000);clock.value=start+120_000_000;adapter.pause(ident)
    clock.value=start+220_000_005;adapter.resume(ident)
    source.sample(start+220_000_000);clock.value=start+240_000_000
    sealed=adapter.finish(ident)
    assert len(json.loads(source.manifest(ident))['samples'])==2
    crossing=sealed['sample_selection'][1]
    assert not crossing['included_after_pause_filter'] and crossing['exclusion_reason']=='STATE_CROSSES_LIFECYCLE_BOUNDARY'
