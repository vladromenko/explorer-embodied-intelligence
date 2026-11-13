from pathlib import Path
import pytest
from so101_lab.resources import ResourceLease,ResourceBusy,assert_hardware_available,snapshot

def test_exclusive_lease():
    with ResourceLease('test-exclusive'):
        with pytest.raises(ResourceBusy):
            with ResourceLease('test-exclusive'):pass
    with ResourceLease('test-exclusive'):pass

def test_missing_device_fail_closed(tmp_path):
    with pytest.raises(ResourceBusy):assert_hardware_available([tmp_path/'missing-device'])

def test_resource_snapshot_no_hardware_open():
    s=snapshot();assert s['memory_available_bytes']>0;assert s['disk_free_bytes']>0

def test_recording_rejects_training_lease_before_source_start(tmp_path):
    from so101_lab.resources import dataset_lease
    from so101_lab.recording import RecordingManager
    m=RecordingManager(tmp_path);d=m.create_dataset('Lease test','synthetic')
    with dataset_lease(d['path']):
        with pytest.raises(ResourceBusy):m.start(d['dataset_id'])
    assert not m.status()['active']

def test_corrupt_job_does_not_break_dashboard(tmp_path,monkeypatch):
    from so101_lab import jobs
    monkeypatch.setattr(jobs,'DIR',tmp_path)
    d=tmp_path/('a'*32);d.mkdir();(d/'job.json').write_text('{broken')
    data=jobs.list_jobs();assert len(data)==1 and data[0]['state']=='failed'

def test_duplicate_resume_of_live_job_refused(tmp_path,monkeypatch):
    from so101_lab import jobs
    monkeypatch.setattr(jobs,'DIR',tmp_path)
    d=tmp_path/('b'*32);d.mkdir();(d/'job.json').write_text('{"kind":"train","options":{},"state":"running"}');(d/'pid').write_text('123')
    monkeypatch.setattr(jobs,'alive',lambda *a:True)
    with pytest.raises(ValueError,match='выполняется'):jobs.resume('b'*32)

def test_identical_active_training_submission_is_deduplicated(tmp_path,monkeypatch):
    from so101_lab import jobs
    monkeypatch.setattr(jobs,'DIR',tmp_path)
    jid='c'*32;folder=tmp_path/jid;folder.mkdir()
    existing={'id':jid,'kind':'train','dataset':'dataset','options':{'seed':42},'state':'waiting_resources','created_at':1}
    jobs.atomic(folder/'job.json',existing)
    returned=jobs.submit('train','dataset',{'seed':42})
    assert returned['id']==jid
    assert len(list(tmp_path.glob('*/job.json')))==1
