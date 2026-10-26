"""Sensor HTTP fixtures and immutable raw evidence; never physical motor IO."""
import hashlib
import io
import json
from pathlib import Path
import tarfile
import threading
import time
from urllib.error import HTTPError
from urllib.request import Request,urlopen

import pytest

from explorer_shared.node_agent import CameraSource, EventDelivery, AgentHTTPServer, NodeAgent, make_handler
from explorer_shared.retarget_recording import (RetargetRecorder,NanoSnapshotClient,CachedImage,import_retarget_recording,
    BASE_BLOCKERS,FORMAT,digest)
from explorer_shared.outbox import EventOutbox

JPEG=b'\xff\xd8sensor-software-fixture\xff\xd9'
TOKEN='software-fixture-retarget-recording-token'


class Camera(CameraSource):
    def __init__(self,ident='pi-overview'):
        super().__init__(ident,'Existing cache fixture')
        self.publish(JPEG,640,480)
    def start(self):pass


class Target:
    def snapshot(self):
        return CachedImage(JPEG,dict(camera_id='nano-rgbd',frame_id='target-fixture:1',monotonic_ns=900000000000000000,
            clock_domain='nano-independent-fixture-boot',received_receiver_ns=time.monotonic_ns(),
            timestamp_basis='legacy_file_mtime_estimate',clock_offset_ns=None,clock_uncertainty_ns=None,
            exposure_timestamp_available=False,depth_available=False))


def wait_finished(recorder,ident):
    for _ in range(200):
        result=recorder.manifest(ident)
        if result['state'] in ('completed','interrupted','failed') and result.get('archive_sha256') and (recorder.root/ident/'completion-event.json').is_file() and recorder.status()['active']!=ident:return result
        time.sleep(.01)
    pytest.fail('Independent recording writer did not seal its evidence')


def make_recorder(tmp_path,target=True):
    delivery=EventDelivery('pi',tmp_path/'delivery',outbox=EventOutbox(tmp_path/'outbox.sqlite3'))
    camera=Camera()
    recorder=RetargetRecorder(tmp_path/'recordings',camera,delivery.emit,clock_domain=delivery.clock_domain,
        target_camera=Target() if target else None)
    return recorder,camera,delivery


def start(recorder,run='run-1'):
    return recorder.start(control_epoch='route-epoch-1',run_id=run,registration={'state':'software_fixture'},name='candy_fixture')


def append(recorder,camera,*,accepted=True):
    camera.publish(JPEG,640,480)
    ticket=recorder.capture('route-epoch-1','run-1');started=time.monotonic_ns()
    measurement={'measured':True,'positions':[0,-60,90,-30,0,50],'clock_domain':'pi-source-fixture',
        'monotonic_ns':started-1000,'calibration_sha256':'fixture-original-calibration'}
    receipt={'state':'API_ACCEPTED' if accepted else 'REJECTED','requested':[90,60,145,65,90,65],
        'accepted':[90,60,145,65,90,65] if accepted else None,'dispatched':None,'dispatch_confirmed':False}
    assert recorder.append(ticket,measurement,{'geometry':{'requested_goal':receipt['requested']},'target_receipt':receipt},
        action_started_ns=started,action_completed_ns=time.monotonic_ns())
    return ticket


def test_cached_frames_source_clocks_receipts_outcome_and_durable_completion_outbox(tmp_path):
    recorder,camera,delivery=make_recorder(tmp_path)
    try:
        recording=start(recorder);ident=recording['recording_id']
        for _ in range(100):
            if recorder.target_cache is not None:break
            time.sleep(.01)
        append(recorder,camera)
        recorder.finish(ident,'failure')
        saved=wait_finished(recorder,ident)
        assert saved['samples_written']==1 and saved['source_frames']==saved['target_frames']==1
        assert saved['outcome']=='failure' and saved['outcome_basis']=='operator_report_not_independent_validation'
        rows=[json.loads(line) for line in (recorder.root/ident/'samples.jsonl').read_text().splitlines()]
        row=rows[0]
        assert row['leader_measured_state']['measured']
        assert row['target_camera']['metadata']['clock_domain']=='nano-independent-fixture-boot'
        assert row['target_camera']['metadata']['monotonic_ns']>row['action_request_receiver_ns']
        assert row['target_camera']['received_before_action_request']  # Causal Pi receipt, never a comparison of two source clocks.
        assert row['dispatched_target_action'] is None and row['target_measured_state'] is None and row['base_action'] is None
        assert not row['target_dispatch_qualified'] and row['training_admission']=='analysis_only'
        for key in ['source_camera','target_camera']:
            frame=row[key];assert digest(recorder.root/ident/frame['file'])==frame['sha256']
        events=delivery.outbox.pending()
        assert len(events)==1
        event=events[0]['payload'];assert event['project_id']=='03' and event['payload']['kind']=='retarget.recording.finished'
        assert event['payload']['archive_sha256']==saved['archive_sha256']
        assert not event['payload']['physical_success_inferred']
        imported=import_retarget_recording(recorder.archive(ident),tmp_path/'nx-import')
        assert imported['source_observations']==1 and imported['outcome']=='failure'
        assert imported['dataset_manifest'] is None and not imported['BC_targets_created']
        assert set(BASE_BLOCKERS)<=set(imported['block_reasons'])
        assert import_retarget_recording(recorder.archive(ident),tmp_path/'nx-import')==imported
    finally:recorder.close();delivery.close()


def test_finish_waits_for_preexisting_control_sample_without_blocking_or_losing_it(tmp_path):
    recorder,camera,delivery=make_recorder(tmp_path,target=False)
    try:
        ident=start(recorder)['recording_id'];ticket=recorder.capture('route-epoch-1','run-1')
        begun=time.monotonic_ns()
        response=recorder.finish(ident,'success')
        assert response['state']=='finalizing' and recorder.status()['active']==ident
        time.sleep(.08)
        assert recorder.manifest(ident)['state']=='finalizing'
        assert recorder.append(ticket,{'measured':True,'clock_domain':'source-fixture'},
            {'state':'UNCHANGED','motion_requested':False},action_started_ns=begun,action_completed_ns=time.monotonic_ns())
        saved=wait_finished(recorder,ident)
        assert saved['samples_written']==1 and saved['outcome']=='success' and not saved['physical_success_inferred']
    finally:recorder.close();delivery.close()


def test_old_outcome_or_sample_cannot_relabel_new_recording(tmp_path):
    recorder,camera,delivery=make_recorder(tmp_path,target=False)
    try:
        old=start(recorder)['recording_id'];ticket=append(recorder,camera)
        recorder.finish(old,'unknown');wait_finished(recorder,old)
        new=start(recorder,run='run-2')['recording_id']
        assert recorder.capture('new-epoch','another-run') is None
        with pytest.raises(ValueError):recorder.finish(old,'success')
        assert not recorder.append(ticket,{}, {},action_started_ns=0,action_completed_ns=1)
        assert recorder.manifest(new)['outcome']=='unknown'
        recorder.finish(new,'unknown');wait_finished(recorder,new)
    finally:recorder.close();delivery.close()


def test_unknown_target_clock_and_missing_target_frame_remain_explicit(tmp_path):
    recorder,camera,delivery=make_recorder(tmp_path,target=False)
    try:
        ident=start(recorder)['recording_id'];append(recorder,camera,accepted=False)
        recorder.finish(ident,'failure');wait_finished(recorder,ident)
        row=json.loads((recorder.root/ident/'samples.jsonl').read_text())
        assert row['target_camera']=={'available':False,'reason':'NO_PREACTION_CACHED_FRAME'}
        assert row['API_accepted_target_action'] is None and not row['target_dispatch_qualified']
    finally:recorder.close();delivery.close()


def test_slow_target_capture_never_blocks_control_cache_snapshot_or_enqueue(tmp_path):
    entered,release=threading.Event(),threading.Event()
    class SlowTarget:
        def snapshot(self):entered.set();assert release.wait(3);return Target().snapshot()
    recorder,camera,delivery=make_recorder(tmp_path,target=False)
    recorder.target_camera=SlowTarget()
    try:
        ident=start(recorder)['recording_id'];assert entered.wait(2)
        before=time.monotonic();append(recorder,camera)
        assert time.monotonic()-before<.1
        recorder.finish(ident,'unknown');release.set();wait_finished(recorder,ident)
    finally:release.set();recorder.close();delivery.close()


def test_restart_seals_interrupted_recording_without_replaying_actions(tmp_path):
    recorder,camera,delivery=make_recorder(tmp_path,target=False)
    ident=start(recorder)['recording_id'];append(recorder,camera)
    recorder.items.join()
    recorder.shutdown.set();recorder.wake.set();recorder.thread.join(2)
    restored=RetargetRecorder(recorder.root,camera,delivery.emit,clock_domain=delivery.clock_domain)
    try:
        saved=restored.manifest(ident)
        assert saved['state']=='interrupted' and saved['samples_written']==1
        assert saved['pending_samples_lost_on_restart']=='UNKNOWN' and saved['outcome']=='unknown'
        assert restored.status()['active'] is None and len(delivery.outbox.pending())==1
    finally:restored.close();delivery.close()


def test_import_rejects_tampered_archived_frame_and_changed_imported_original(tmp_path):
    recorder,camera,delivery=make_recorder(tmp_path,target=False)
    try:
        ident=start(recorder)['recording_id'];append(recorder,camera);recorder.finish(ident,'unknown');wait_finished(recorder,ident)
        original=recorder.archive(ident)
        tampered=tmp_path/'tampered.tar'
        with tarfile.open(original) as source,tarfile.open(tampered,'w') as destination:
            for member in source:
                data=source.extractfile(member).read()
                if member.name.endswith('.jpg'):data=b'tampered sensor source'
                info=tarfile.TarInfo(member.name);info.size=len(data);destination.addfile(info,io.BytesIO(data))
        with pytest.raises(ValueError,match='checksum'):import_retarget_recording(tampered,tmp_path/'bad-import')
        imported=import_retarget_recording(original,tmp_path/'good-import')
        frame=next((tmp_path/'good-import/source_originals/frames').glob('*.jpg'));frame.write_bytes(b'changed')
        with pytest.raises(ValueError,match='changed'):import_retarget_recording(original,tmp_path/'good-import')
    finally:recorder.close();delivery.close()


def test_real_localhost_authenticated_snapshot_retains_original_source_clock(tmp_path,monkeypatch):
    monkeypatch.setattr(Path,'home',lambda:tmp_path/'empty-home')
    camera=Camera('nano-rgbd')
    node=NodeAgent('nano','nano',tmp_path/'node',TOKEN,sources={'nano-rgbd':camera},
        legacy_dir=tmp_path/'original/data',retarget_config_dir=tmp_path/'config')
    server=AgentHTTPServer(('127.0.0.1',0),make_handler(node))
    threading.Thread(target=server.serve_forever,daemon=True).start()
    base='http://127.0.0.1:'+str(server.server_port)
    try:
        with pytest.raises(HTTPError):NanoSnapshotClient(base,'wrong-token').snapshot()
        result=NanoSnapshotClient(base,TOKEN).snapshot()
        assert result.jpeg==JPEG and result.metadata['clock_domain']==node.delivery.clock_domain
        assert result.metadata['monotonic_ns']==camera.latest().monotonic_ns
        assert result.metadata['timestamp_basis']=='host_receive_estimate'
        assert result.metadata['request_started_receiver_ns']<=result.metadata['received_receiver_ns']
        assert result.metadata['clock_offset_ns'] is None and not result.metadata['depth_available']
    finally:server.shutdown();server.server_close();node.close()


from test_direct03_nodes import http_nodes,begin,held,request,TOKEN as TOKEN_FOR_NODE


def test_complete_pi_recording_endpoints_direct_action_target_camera_archive_and_raw_import(http_nodes,tmp_path):
    pi_url,nano_url,pi,nano,station,original=http_nodes
    source_camera=Camera();target_camera=Camera('nano-rgbd')
    pi.arm_retarget.camera=source_camera
    nano.sources['nano-rgbd']=target_camera
    pi.arm_retarget.recorder=RetargetRecorder(tmp_path/'recording-runtime',source_camera,pi.delivery.emit,
        clock_domain=pi.delivery.clock_domain,target_camera=NanoSnapshotClient(nano_url,TOKEN_FOR_NODE))
    started=begin(pi_url)
    recording=request(pi_url,'/arm-retarget/record/start',dict(expected_control_epoch=started['control_epoch'],observing=True,name='candy-fixture'))
    ident=recording['recording_id']
    for _ in range(100):
        if pi.arm_retarget.recorder.target_cache is not None:break
        time.sleep(.01)
    assert pi.arm_retarget.recorder.target_cache is not None
    station.update([3.,-60.,90.,-30.,0.,75.]);held(pi_url,started,0)
    for _ in range(100):
        if pi.arm_retarget.recorder.manifest(ident)['samples_written']:break
        time.sleep(.01)
    request(pi_url,'/arm-retarget/record/finish',dict(recording_id=ident,outcome='failure'))
    saved=wait_finished(pi.arm_retarget.recorder,ident)
    assert saved['samples_written']==1 and saved['target_frames']==1
    listed=request(pi_url,'/arm-retarget/recordings')['recordings']
    assert [entry['recording_id'] for entry in listed]==[ident]
    with urlopen(Request(pi_url+'/arm-retarget/recordings/'+ident+'/download',headers={'Authorization':'Bearer '+TOKEN_FOR_NODE}),timeout=3) as response:
        raw=response.read();assert hashlib.sha256(raw).hexdigest()==response.headers['X-Content-SHA256']
    archive=tmp_path/'transferred.tar';archive.write_bytes(raw)
    imported=import_retarget_recording(archive,tmp_path/'nx-target',expected_archive_sha256=hashlib.sha256(raw).hexdigest())
    assert not imported['BC_targets_created'] and imported['dataset_manifest'] is None
    rows=[json.loads(line) for line in (tmp_path/'nx-target/source_originals/samples.jsonl').read_text().splitlines()]
    assert rows[0]['target_camera']['metadata']['clock_domain']==nano.delivery.clock_domain
    assert rows[0]['source_camera']['metadata']['clock_domain']==pi.delivery.clock_domain
    assert rows[0]['requested_target_action']==rows[0]['API_accepted_target_action']
    assert rows[0]['dispatched_target_action'] is None and original.calls[-1][0]=='arm/trajectory/start'


def test_slow_checkpoint_does_not_hold_control_snapshot_or_status_locks(tmp_path,monkeypatch):
    import explorer_shared.retarget_recording as module
    recorder,camera,delivery=make_recorder(tmp_path,target=False)
    entered,release=threading.Event(),threading.Event()
    original=module.write_json
    def slow(path,value):
        if path.name=='manifest.json' and value.get('samples_written')==1 and value.get('state')=='recording':
            entered.set();assert release.wait(3)
        return original(path,value)
    try:
        ident=start(recorder)['recording_id'];monkeypatch.setattr(module,'write_json',slow)
        append(recorder,camera);assert entered.wait(2)
        begun=time.monotonic();ticket=recorder.capture('route-epoch-1','run-1')
        status=recorder.status()
        assert time.monotonic()-begun<.1 and status['active']==ident
        started=time.monotonic_ns()
        assert recorder.append(ticket,{}, {'state':'UNCHANGED'},action_started_ns=started,action_completed_ns=time.monotonic_ns())
        release.set();recorder.finish(ident,'unknown')
        assert wait_finished(recorder,ident)['samples_written']==2
    finally:release.set();recorder.close();delivery.close()


def test_repeated_cached_frame_is_counted_as_reference_not_new_independent_frame(tmp_path):
    recorder,camera,delivery=make_recorder(tmp_path,target=False)
    try:
        ident=start(recorder)['recording_id']
        for _ in range(2):
            ticket=recorder.capture('route-epoch-1','run-1');started=time.monotonic_ns()
            recorder.append(ticket,{}, {'state':'UNCHANGED'},action_started_ns=started,action_completed_ns=time.monotonic_ns())
        recorder.finish(ident,'unknown');saved=wait_finished(recorder,ident)
        assert saved['source_frames']==2 and saved['unique_source_frames']==1
        assert delivery.outbox.pending()[0]['payload']['payload']['frame_count']==1
    finally:recorder.close();delivery.close()


def test_writer_queue_overflow_is_counted_and_cannot_block_control(tmp_path):
    entered,release=threading.Event(),threading.Event()
    class SlowTarget:
        def snapshot(self):entered.set();assert release.wait(3);return Target().snapshot()
    delivery=EventDelivery('pi',tmp_path/'delivery',outbox=EventOutbox(tmp_path/'outbox.sqlite3'))
    camera=Camera()
    recorder=RetargetRecorder(tmp_path/'recordings',camera,delivery.emit,clock_domain=delivery.clock_domain,
        target_camera=SlowTarget(),queue_limit=1)
    try:
        ident=start(recorder)['recording_id'];assert entered.wait(2)
        first=recorder.capture('route-epoch-1','run-1');now=time.monotonic_ns()
        assert recorder.append(first,{}, {},action_started_ns=now,action_completed_ns=now)
        second=recorder.capture('route-epoch-1','run-1');now=time.monotonic_ns()
        begun=time.monotonic()
        assert not recorder.append(second,{}, {},action_started_ns=now,action_completed_ns=now)
        assert time.monotonic()-begun<.1
        recorder.finish(ident,'unknown');release.set();saved=wait_finished(recorder,ident)
        assert saved['samples_written']==1 and saved['samples_dropped']==1 and saved['attempted_sample_tickets']==2
    finally:release.set();recorder.close();delivery.close()
