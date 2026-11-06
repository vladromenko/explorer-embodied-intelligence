import hashlib
import json
import time
from pathlib import Path

import pytest

from so101_lab import recording
from so101_lab.recording import RecordingManager, EpisodeWriter, SyntheticSource, integer


def wait_for(manager, predicate, timeout=6):
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        status=manager.status()
        if predicate(status): return status
        if status.get('state')=='failed': pytest.fail(str(status))
        time.sleep(.01)
    pytest.fail('timeout '+str(manager.status()))


def manager_at(tmp_path):
    manager=RecordingManager(tmp_path)
    return manager


def test_integer_51_not_5():
    assert integer('51','episodes')==51
    assert integer(51,'episodes')==51
    for value in ('5.1',5.1,True,'51x','0','-2'):
        with pytest.raises(ValueError): integer(value,'episodes')


def test_unicode_name_and_no_path_injection(tmp_path):
    manager=manager_at(tmp_path)
    dataset=manager.create_dataset('../кубики 🦾 / вечер','Переложить красный кубик в коробку')
    assert dataset['name']=='../кубики 🦾 / вечер'
    assert Path(dataset['path']).parent==tmp_path/'data'
    assert dataset['source']=='synthetic_test'
    assert dataset['test_only'] is True
    with pytest.raises(ValueError): manager.dataset_info('../other')


def test_lifecycle_end_next_stop_hashes_timestamps(tmp_path):
    manager=manager_at(tmp_path)
    dataset=manager.create_dataset('smoke','fixture',source='synthetic_test')
    manager.start(dataset['dataset_id'],episode_count='51',fps=20,episode_seconds=10,width=160,height=120)
    assert manager.status()['target_episodes']==51
    wait_for(manager,lambda s:s.get('frames_written',0)>=3)
    # Reads and polling never keep the worker alive; recording continues without browser calls.
    time.sleep(.15)
    manager.command('end')
    wait_for(manager,lambda s:s['state']=='ready')
    manager.command('next')
    wait_for(manager,lambda s:s['state']=='recording' and s.get('frames_written',0)>=2)
    manager.command('stop'); status=manager.wait()
    assert status['state']=='stopped'
    info=manager.dataset_info(dataset['dataset_id'])
    assert info['episodes']==2
    assert info['version']==2
    assert len(info['sessions'])==1
    assert len({ep['session_id'] for ep in info['episode_list']})==1
    for episode in info['episode_list']:
        path=Path(dataset['path'])/'episodes'/episode['episode_id']
        content=(path/'frames.jsonl').read_bytes()
        assert hashlib.sha256(content).hexdigest()==episode['frames_sha256']
        frames=[json.loads(line) for line in content.splitlines()]
        assert len(frames)>=2
        previous=0
        for frame in frames:
            assert previous<frame['timestamp_ns']
            previous=frame['timestamp_ns']
            assert frame['state_timestamp_ns']<=frame['action_timestamp_ns']
            assert frame['state_valid'] is False
            assert frame['action_valid'] is False
            assert frame['source']=='synthetic_test'
            assert frame['feedback']['current']['valid'] is False
            assert frame['feedback']['current']['value'] is None
            assert len(frame['observation.state'])==6
            assert len(frame['action'])==6
            assert set(frame['cameras'])=={'top','wrist'}
            for camera in frame['cameras'].values():
                assert camera['timestamp_ns']<=frame['observation_timestamp_ns']
                image=path/camera['path']
                assert image.read_bytes().startswith(b'\x89PNG')
                assert hashlib.sha256(image.read_bytes()).hexdigest()==camera['sha256']
    assert manager.latest_frame('top').startswith(b'\x89PNG')
    assert manager.latest_frame('wrist').startswith(b'\x89PNG')


def test_rerecord_preserves_rejected_raw_in_quarantine(tmp_path):
    manager=manager_at(tmp_path)
    dataset=manager.create_dataset('test','test')
    manager.start(dataset['dataset_id'],episode_count=2,fps=30,episode_seconds=10,width=160,height=120)
    wait_for(manager,lambda s:s.get('frames_written',0)>=2)
    old=manager.status()['episode_id']
    manager.command('rerecord')
    wait_for(manager,lambda s:s.get('episode_id')!=old and s.get('frames_written',0)>=2)
    manager.command('stop'); manager.wait()
    info=manager.dataset_info(dataset['dataset_id'])
    assert info['episodes']==1
    assert info['quarantine_count']==1
    rejected=Path(dataset['path'])/'quarantine'/old
    assert json.loads((rejected/'episode.json').read_text())['status']=='rerecorded'
    assert (rejected/'frames.jsonl').stat().st_size>0


def test_rerecord_finalized_episode_is_excluded(tmp_path):
    manager=manager_at(tmp_path)
    dataset=manager.create_dataset('test','test')
    manager.start(dataset['dataset_id'],episode_count=2,fps=30,episode_seconds=10,width=160,height=120)
    wait_for(manager,lambda s:s.get('frames_written',0)>=2)
    old=manager.status()['episode_id']
    manager.command('end'); wait_for(manager,lambda s:s['state']=='ready')
    manager.command('rerecord')
    wait_for(manager,lambda s:s['state']=='recording' and s.get('frames_written',0)>=2)
    manager.command('stop'); manager.wait()
    info=manager.dataset_info(dataset['dataset_id'])
    assert info['episodes']==1
    assert info['quarantine_count']==1
    rejected=Path(dataset['path'])/'quarantine'/old
    assert json.loads((rejected/'episode.json').read_text())['status']=='superseded'


def test_server_restart_quarantines_partial(tmp_path):
    partial=tmp_path/'data/ds_1234567890abcdef/.partial/ep_crash.partial'
    partial.mkdir(parents=True)
    (partial/'episode.json').write_text(json.dumps({'episode_id':'ep_crash','status':'recording'}))
    (partial/'frames.jsonl').write_text('{"incomplete":true}\n')
    manager=manager_at(tmp_path)
    recovered=tmp_path/'data/ds_1234567890abcdef/quarantine/ep_crash'
    assert recovered.exists()
    meta=json.loads((recovered/'episode.json').read_text())
    assert meta['status']=='incomplete'
    assert meta['reason']=='server_restart_before_atomic_finalization'
    assert (recovered/'frames.jsonl').read_text()=='{"incomplete":true}\n'


def test_hardware_busy_rejected_before_source_construction(tmp_path,monkeypatch):
    manager=manager_at(tmp_path)
    dataset=manager.create_dataset('real','real',source='hardware')
    def busy(paths): raise RuntimeError('foreign owner test')
    monkeypatch.setattr(recording,'assert_hardware_available',busy)
    def forbidden(*args,**kwargs): pytest.fail('HardwareSource constructed before busy check')
    monkeypatch.setattr(recording,'HardwareSource',forbidden)
    with pytest.raises(PermissionError): manager.start(dataset['dataset_id'])
    with pytest.raises(RuntimeError,match='foreign owner'):
        manager.start(dataset['dataset_id'],explicit_user_start=True)
    assert manager.status()['active'] is False


def test_disk_guard_before_recording(tmp_path,monkeypatch):
    manager=manager_at(tmp_path)
    dataset=manager.create_dataset('disk','test')
    def full(*args,**kwargs): raise RuntimeError('disk full')
    monkeypatch.setattr(recording,'assert_disk_space',full)
    with pytest.raises(RuntimeError,match='disk full'): manager.start(dataset['dataset_id'])
    assert manager.status()['active'] is False


def test_writer_failure_quarantines_and_reports(tmp_path,monkeypatch):
    manager=manager_at(tmp_path)
    dataset=manager.create_dataset('failure','test')
    original=SyntheticSource.sample
    def fail(self,frame_id):
        if frame_id>=4: raise RuntimeError('injected camera loss')
        return original(self,frame_id)
    monkeypatch.setattr(SyntheticSource,'sample',fail)
    manager.start(dataset['dataset_id'],fps=30,episode_seconds=10,width=160,height=120)
    status=manager.wait()
    assert status['state']=='failed'
    assert 'injected camera loss' in status['error']
    assert Path(status['traceback_path']).is_file()
    info=manager.dataset_info(dataset['dataset_id'])
    assert info['episodes']==0
    assert info['quarantine_count']==1
    raw=next((Path(dataset['path'])/'quarantine').glob('*/frames.jsonl'))
    assert len(raw.read_text().splitlines())==3


def test_no_second_recording_worker(tmp_path):
    manager=manager_at(tmp_path)
    dataset=manager.create_dataset('one','test')
    manager.start(dataset['dataset_id'],fps=20,episode_seconds=10,width=160,height=120)
    with pytest.raises(RuntimeError,match='активная запись'): manager.start(dataset['dataset_id'])
    manager.command('stop'); manager.wait()


def test_stop_closes_source_before_writer_drain(tmp_path,monkeypatch):
    manager=manager_at(tmp_path);dataset=manager.create_dataset('stop order','fixture')
    events=[]
    original_close=SyntheticSource.close
    original_finish=EpisodeWriter.finish
    def close(source):
        events.append('source_closed');return original_close(source)
    def finish(writer,**kwargs):
        assert events and events[0]=='source_closed'
        events.append('writer_drain');return original_finish(writer,**kwargs)
    monkeypatch.setattr(SyntheticSource,'close',close)
    monkeypatch.setattr(EpisodeWriter,'finish',finish)
    manager.start(dataset['dataset_id'],fps=30,episode_seconds=10,width=160,height=120)
    wait_for(manager,lambda status:status.get('frames_written',0)>=2)
    manager.command('stop');status=manager.wait()
    assert status['state']=='stopped'
    assert events==['source_closed','writer_drain']


@pytest.mark.parametrize('finish_action',['end','rerecord'])
def test_stop_during_slow_end_drain_closes_source_promptly(tmp_path,monkeypatch,finish_action):
    import threading
    from PIL import Image
    saving,release,draining,closed=(threading.Event() for _ in range(4))
    save_original=Image.Image.save
    close_original=SyntheticSource.close
    finish_original=EpisodeWriter.finish
    def slow_save(image,*args,**kwargs):
        saving.set();release.wait(3);return save_original(image,*args,**kwargs)
    def close(source):
        closed.set();return close_original(source)
    def finish(writer,**kwargs):
        draining.set();return finish_original(writer,**kwargs)
    monkeypatch.setattr(Image.Image,'save',slow_save)
    monkeypatch.setattr(SyntheticSource,'close',close)
    monkeypatch.setattr(EpisodeWriter,'finish',finish)
    manager=manager_at(tmp_path);dataset=manager.create_dataset('slow drain','fixture')
    try:
        manager.start(dataset['dataset_id'],fps=10,episode_count=2,episode_seconds=10,width=160,height=120)
        assert saving.wait(2)
        manager.command(finish_action);assert draining.wait(2)
        manager.command('stop')
        assert closed.wait(.5), 'STOP waited for PNG encoding instead of closing source'
    finally:release.set()
    assert manager.wait()['state']=='stopped'


def test_writer_timeout_retains_live_partial_directory(tmp_path,monkeypatch):
    import threading
    from PIL import Image
    saving,release=threading.Event(),threading.Event()
    original=Image.Image.save
    def slow_save(image,*args,**kwargs):
        saving.set();release.wait(3);return original(image,*args,**kwargs)
    monkeypatch.setattr(Image.Image,'save',slow_save)
    writer=EpisodeWriter(tmp_path,{'episode_id':'ep_timeout'})
    source=SyntheticSource({'width':160,'height':120});source.start()
    try:
        writer.put(source.sample(1));assert saving.wait(2)
        started=time.monotonic();result=writer.finish(timeout=.05)
        assert time.monotonic()-started<.5
        assert result['status']=='incomplete' and result['writer_pending']
        assert writer.partial.exists()
        assert not (tmp_path/'episodes/ep_timeout').exists()
    finally:release.set()
    writer.thread.join(2)
    result=writer.finish(timeout=1)
    assert result['status']=='complete' and not result.get('writer_pending')


def recorded_dataset(manager):
    dataset=manager.create_dataset('validation fixture','fixture')
    manager.start(dataset['dataset_id'],fps=30,episode_seconds=10,width=160,height=120)
    wait_for(manager,lambda status:status.get('frames_written',0)>=2)
    manager.command('stop');assert manager.wait()['state']=='stopped'
    return dataset


def test_finalize_files_test_source_guard_and_reopen(tmp_path):
    manager=manager_at(tmp_path);dataset=recorded_dataset(manager)
    rejected=manager.finalize_dataset(dataset['dataset_id'])
    assert rejected['status']=='invalid' and not rejected['finalized']
    report=manager.finalize_dataset(dataset['dataset_id'],allow_test_data=True)
    assert report['status']=='valid' and report['finalized']
    assert report['software_only'] and report['hardware_verification']=='NOT_TESTED_BY_FILE_VALIDATOR'
    assert not report['training_eligible'] and report['frames']>=2
    assert report['episodes'][0]['fps_observed']>0
    assert (Path(dataset['path'])/'validation/raw-validation.json').is_file()
    assert manager.dataset_info(dataset['dataset_id'])['lifecycle_status']=='finalized'
    manager.start(dataset['dataset_id'],fps=30,episode_seconds=10,width=160,height=120)
    wait_for(manager,lambda status:status.get('frames_written',0)>=2)
    with pytest.raises(RuntimeError,match='остановите'):manager.finalize_dataset(dataset['dataset_id'],allow_test_data=True)
    manager.command('stop');manager.wait()
    current=manager.dataset_info(dataset['dataset_id'])
    assert current['lifecycle_status']=='draft'
    assert current['finalization']['status']=='stale_after_recording'


def test_finalize_detects_corrupted_rgb_bytes(tmp_path):
    manager=manager_at(tmp_path);dataset=recorded_dataset(manager)
    image=next((Path(dataset['path'])/'episodes').glob('*/images/top/*.png'))
    image.write_bytes(image.read_bytes()+b'corruption')
    report=manager.finalize_dataset(dataset['dataset_id'],allow_test_data=True)
    assert report['status']=='invalid'
    assert any('raw image hash mismatch' in error for error in report['errors'])


def test_finalize_rejects_future_camera_and_unknown_session(tmp_path):
    manager=manager_at(tmp_path);dataset=recorded_dataset(manager)
    path=Path(dataset['path']);episode=next((path/'episodes').glob('*'))
    manifest=episode/'frames.jsonl'
    rows=[json.loads(line) for line in manifest.read_text().splitlines()]
    rows[0]['cameras']['top']['timestamp_ns']=rows[0]['timestamp_ns']+1
    manifest.write_text(''.join(json.dumps(row)+'\n' for row in rows))
    metadata=json.loads((episode/'episode.json').read_text())
    metadata['frames_sha256']=hashlib.sha256(manifest.read_bytes()).hexdigest()
    (episode/'episode.json').write_text(json.dumps(metadata))
    report=manager.finalize_dataset(dataset['dataset_id'],allow_test_data=True)
    assert report['status']=='invalid' and any('будущего' in error for error in report['errors'])
    metadata['session_id']='missing_session'
    (episode/'episode.json').write_text(json.dumps(metadata))
    report=manager.finalize_dataset(dataset['dataset_id'],allow_test_data=True)
    assert any('session_id' in error for error in report['errors'])
