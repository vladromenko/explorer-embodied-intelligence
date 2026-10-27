"""Generated real JPEGs + unchanged original fake Station writer; no hardware."""
from copy import deepcopy
import hashlib
import io
import json
import os
from pathlib import Path
import socket
import sys
import subprocess
import tarfile
import threading

from PIL import Image
import pytest

from test_so101_capture_adapter import FakeSource,Clock,context
from explorer_so101.capture_adapter import StationCaptureAdapter
from explorer_so101 import capture_archive as consumer
from explorer_shared.native_capture_approval import source_bytes


def checksum(path):
    with path.open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()


def pack(entries):
    output=io.BytesIO()
    with tarfile.open(fileobj=output,mode='w',format=tarfile.PAX_FORMAT) as archive:
        for name,content,kind,pax in entries:
            member=tarfile.TarInfo(name);member.type=kind;member.size=len(content);member.mtime=1.25
            member.pax_headers=pax
            if kind in (tarfile.SYMTYPE,tarfile.LNKTYPE):member.linkname='../../NO_EXTRACTION_ALLOWED'
            archive.addfile(member,io.BytesIO(content))
    return output.getvalue()


def entries(raw):
    with tarfile.open(fileobj=io.BytesIO(raw),mode='r:') as archive:
        return [(member.name,archive.extractfile(member).read(),member.type,dict(member.pax_headers)) for member in archive]


@pytest.fixture
def captured(tmp_path,monkeypatch):
    def prohibited(*args,**kwargs):raise AssertionError('No network/device/control belongs in archive import')
    monkeypatch.setattr(socket,'create_connection',prohibited)
    root=tmp_path/'source';root.mkdir();source=FakeSource(root);clock=Clock()
    sample=source.station._sample;image=io.BytesIO();Image.new('RGB',(4,3),(12,80,160)).save(image,format='JPEG')
    jpeg=image.getvalue()
    def real_jpeg(frame,*args):frame.jpeg=jpeg;return sample(frame,*args)
    source.station._sample=real_jpeg
    adapter=StationCaptureAdapter(tmp_path/'adapter',status=source.status,record_start=source.start,
        record_finish=source.finish,read_manifest=source.manifest,archive_original=source.archive,
        boot_id=source.station.boot,clock_domain=source.station.clock_domain,clock=clock)
    start=clock();ident=adapter.start(context(),'fixture-owner')['recording_id']
    source.sample(start+110_000_000);clock.value=start+120_000_000;adapter.pause(ident)
    source.sample(start+220_000_000);clock.value=start+230_000_000;adapter.resume(ident)
    source.sample(start+330_000_000);clock.value=start+350_000_000;adapter.finish(ident,'unknown')
    path=adapter.archive(ident);digest=checksum(path);review=adapter.review(ident,'approve','fixture-owner',digest)
    yield {'source':source,'adapter':adapter,'id':ident,'path':path,'sha':digest,'review':review,'jpeg':jpeg,
        'raw_manifest':source.manifest(ident),'calls':list(source.calls)}
    adapter.close();source.close_writer()


def imported(captured,destination,*,review=None,path=None):
    artifact=path or captured['path']
    return consumer.import_source_capture(artifact,checksum(artifact),destination,captured['id'],review or captured['review'])


def valid_review(captured,digest):
    review=deepcopy(captured['review']);review['source_review']['archive_sha256']=digest
    review['review_sha256']=hashlib.sha256(source_bytes(review['source_review'])).hexdigest()
    return review


def variant(captured,tmp_path,*,capture_change=None,original_change=None,outer_change=None,inner_change=None):
    with captured['path'].open('rb') as stream:outer=entries(stream.read())  # bounded small generated fixture only
    envelope=json.loads(outer[0][1]);inner=entries(outer[1][1]);original=json.loads(inner[0][1])
    if original_change is not None:
        original_change(original);raw=source_bytes(original)
        inner[0]=(inner[0][0],raw,inner[0][2],inner[0][3])
        envelope['original_manifest'].update(sha256=hashlib.sha256(raw).hexdigest(),bytes=len(raw))
        envelope['lifecycle']['original_finish_receipt']['sha256']=hashlib.sha256(raw).hexdigest()
    if inner_change is not None:inner_change(inner)
    inner_raw=pack(inner);envelope['original_archive'].update(sha256=hashlib.sha256(inner_raw).hexdigest(),bytes=len(inner_raw))
    if capture_change is not None:capture_change(envelope)
    outer=[('manifest.json',source_bytes(envelope),tarfile.REGTYPE,{}),('source/recording.tar',inner_raw,tarfile.REGTYPE,{})]
    if outer_change is not None:outer_change(outer)
    path=tmp_path/'variant.tar';path.write_bytes(pack(outer))
    return path,valid_review(captured,checksum(path))


def test_real_jpeg_native6_originals_and_review_roundtrip_idempotent_without_conversion(captured,tmp_path):
    destination=tmp_path/'import';literal=source_bytes(captured['review'])+b'\n'
    result=imported(captured,destination,review=literal);base=destination/captured['id']
    assert Path(result['source_manifest_path']).read_bytes()==captured['raw_manifest']
    assert Path(result['source_review_path']).read_bytes()==literal
    assert checksum(Path(result['original_archive_path']))==checksum(captured['source'].archive(captured['id']))
    envelope=json.loads(Path(result['capture_manifest_path']).read_bytes())
    assert [row['recording_segment_id'] for row in envelope['sample_selection']]==[0,None,1]
    original=json.loads(Path(result['source_manifest_path']).read_bytes());assert len(original['samples'])==3
    for sample in original['samples']:
        frame=base/'original'/sample['frame']['file'];assert frame.read_bytes()==captured['jpeg']
        with Image.open(frame) as decoded:assert decoded.size==(4,3)
        assert sample['state']==[1,2,3,4,5,6] and sample['action']==[100,20,30,40,50,60]
    assert result['source_archive_sha256']==captured['sha'] and result['source_review_sha256']==captured['review']['review_sha256']
    assert not result['training_admission_assessed'] and result['dataset_manifest'] is None and not result['physical_success_inferred']
    before={path.relative_to(base).as_posix():path.read_bytes() for path in base.rglob('*') if path.is_file()}
    second=imported(captured,destination,review=literal)
    assert second['idempotent_existing'] and not list(destination.glob('.source04-import-*'))
    assert {path.relative_to(base).as_posix():path.read_bytes() for path in base.rglob('*') if path.is_file()}==before
    root=Path(__file__).resolve().parents[1]
    env=dict(os.environ,PYTHONPATH=os.pathsep.join([str(root/'shared'),str(root/'04-so101-source-learning/src')]))
    subprocess.run([sys.executable,'-c',"import sys; import explorer_so101.capture_archive; assert 'torch' not in sys.modules"],
        check=True,timeout=10,env=env)
    assert [call for call in captured['source'].calls if call not in ('archive',)]==[call for call in captured['calls'] if call not in ('archive',)]


@pytest.mark.parametrize('kind',['rejected','wrong_recording','bad_review_sha'])
def test_approval_rejects_before_destination_creation(captured,tmp_path,kind):
    review=deepcopy(captured['review'])
    if kind=='rejected':
        review['source_review']['decision']='reject';review['transfer_ready']=False
        review['review_sha256']=hashlib.sha256(source_bytes(review['source_review'])).hexdigest()
    elif kind=='wrong_recording':
        review['source_review']['recording_id']='f'*32;review['review_sha256']=hashlib.sha256(source_bytes(review['source_review'])).hexdigest()
    else:review['review_sha256']='f'*64
    destination=tmp_path/'not-created'
    with pytest.raises(ValueError):imported(captured,destination,review=review)
    assert not destination.exists()


@pytest.mark.parametrize('field',['project','contract','boot','clock','owner','calibration','context','selection_bool','intervals','manifest_length'])
def test_rehashed_foreign_envelope_or_selection_is_not_accepted(captured,tmp_path,field):
    def change(capture):
        if field=='project':capture['project_id']='03'
        elif field=='contract':capture['action_contract_id']='rosmaster_9d'
        elif field=='boot':capture['source_boot_id']='foreign-boot'
        elif field=='clock':capture['source_clock_domain']='foreign-clock'
        elif field=='owner':capture['lifecycle']['native_control_epoch']='FOREIGN_NATIVE_EPOCH'
        elif field=='calibration':capture['lifecycle']['calibration_sha256']['leader']='f'*64
        elif field=='context':capture['source_context']['session_id']='FOREIGN_SESSION'
        elif field=='selection_bool':capture['sample_selection'][0]['included_after_pause_filter']=1
        elif field=='intervals':capture['lifecycle']['pause_intervals'][0]['start_monotonic_ns']+=1
        else:capture['original_manifest']['bytes']+=1
    path,review=variant(captured,tmp_path,capture_change=change);destination=tmp_path/'import'
    with pytest.raises(ValueError):imported(captured,destination,path=path,review=review)
    assert not (destination/captured['id']).exists()
    stages=list(destination.glob('.source04-import-*'));assert len(stages)==1 and (stages[0]/'.import-failure.json').is_file()


@pytest.mark.parametrize('field',['project','boot','calibration','context','owner'])
def test_rehashed_original_manifest_foreign_facts_are_rejected(captured,tmp_path,field):
    def change(original):
        if field=='project':original['project_id']='02'
        elif field=='boot':original['boot_id']='FOREIGN_BOOT'
        elif field=='calibration':original['calibration_metadata']['follower']['calibration_sha256']='f'*64
        elif field=='context':original['source_context']['skill_version']=99
        else:original['samples'][0]['dispatch_receipt']['control_epoch']='FOREIGN_EPOCH'
    path,review=variant(captured,tmp_path,original_change=change)
    with pytest.raises(ValueError):imported(captured,tmp_path/'import',path=path,review=review)


@pytest.mark.parametrize('kind',['outer_extra','outer_duplicate','outer_traversal','outer_symlink','inner_extra','inner_missing','inner_symlink','inner_sparse','inner_checksum','sparse_pax','huge_pax'])
def test_unsafe_extra_incomplete_or_unbounded_tar_retains_failed_staging(captured,tmp_path,kind):
    def outer_change(outer):
        if kind=='outer_extra':outer.append(('extra.json',b'{}',tarfile.REGTYPE,{}))
        elif kind=='outer_duplicate':outer.append(outer[0])
        elif kind=='outer_traversal':outer[0]=('../escape',outer[0][1],tarfile.REGTYPE,{})
        elif kind=='outer_symlink':outer[0]=('manifest.json',b'',tarfile.SYMTYPE,{})
        elif kind=='sparse_pax':outer[0]=(outer[0][0],outer[0][1],tarfile.REGTYPE,{'GNU.sparse.map':'0,999999999999'})
        elif kind=='huge_pax':outer[0]=(outer[0][0],outer[0][1],tarfile.REGTYPE,{'mtime':'9'*70000})
    def inner_change(inner):
        if kind=='inner_extra':inner.append(('unexpected.jpg',b'EXTRA',tarfile.REGTYPE,{}))
        elif kind=='inner_missing':inner.pop()
        elif kind=='inner_symlink':inner[1]=(inner[1][0],b'',tarfile.SYMTYPE,{})
        elif kind=='inner_sparse':inner[1]=(inner[1][0],b'',tarfile.GNUTYPE_SPARSE,{})
        elif kind=='inner_checksum':inner[1]=(inner[1][0],inner[1][1]+b'CHANGED',tarfile.REGTYPE,{})
    path,review=variant(captured,tmp_path,outer_change=outer_change,inner_change=inner_change)
    destination=tmp_path/'import'
    with pytest.raises((ValueError,tarfile.TarError)):imported(captured,destination,path=path,review=review)
    assert not (tmp_path/'escape').exists() and not (destination/captured['id']).exists()
    stages=list(destination.glob('.source04-import-*'));assert len(stages)==1 and (stages[0]/'.import-failure.json').exists()
    before={path.name for path in stages};
    with pytest.raises((ValueError,tarfile.TarError)):imported(captured,destination,path=path,review=review)
    assert before.issubset({path.name for path in destination.glob('.source04-import-*')})


def test_mismatched_outer_sha_and_symlink_artifact_are_never_imported(captured,tmp_path):
    with pytest.raises(ValueError,match='outer archive SHA'):consumer.import_source_capture(captured['path'],'f'*64,tmp_path/'import',captured['id'],valid_review(captured,'f'*64))
    assert not (tmp_path/'import').exists()
    link=tmp_path/'artifact-link';link.symlink_to(captured['path'])
    with pytest.raises(OSError):imported(captured,tmp_path/'import',path=link)


@pytest.mark.parametrize('kind',['frame_corrupt','empty_existing','different_review'])
def test_existing_corrupt_or_conflicting_destination_is_never_overwritten(captured,tmp_path,kind):
    destination=tmp_path/'import';base=destination/captured['id'];review=captured['review']
    if kind=='empty_existing':base.mkdir(parents=True);(base/'original-kept').write_bytes(b'KEEP')
    else:
        imported(captured,destination)
        if kind=='frame_corrupt':next((base/'original').glob('*.jpg')).write_bytes(b'CORRUPT_ORIGINAL_KEEP')
        else:
            review=deepcopy(review);review['source_review']['review_id']='f'*32
            review['review_sha256']=hashlib.sha256(source_bytes(review['source_review'])).hexdigest()
    before={path.relative_to(base).as_posix():path.read_bytes() for path in base.rglob('*') if path.is_file()}
    with pytest.raises((ValueError,OSError)):imported(captured,destination,review=review)
    assert {path.relative_to(base).as_posix():path.read_bytes() for path in base.rglob('*') if path.is_file()}==before


def test_concurrent_same_original_import_uses_one_managed_id_lock(captured,tmp_path,monkeypatch):
    real=consumer._capture;entered,release=threading.Event(),threading.Event();results=[]
    def blocked(raw,ident):entered.set();assert release.wait(2);return real(raw,ident)
    monkeypatch.setattr(consumer,'_capture',blocked);destination=tmp_path/'import'
    thread=threading.Thread(target=lambda:results.append(imported(captured,destination)));thread.start();assert entered.wait(1)
    with pytest.raises(BlockingIOError):imported(captured,destination)
    release.set();thread.join(2);assert not thread.is_alive() and results[0]['recording_id']==captured['id']


def test_late_empty_destination_is_atomically_preserved_and_complete_stage_retained(captured,tmp_path,monkeypatch):
    real=consumer._publish;destination=tmp_path/'import';final=destination/captured['id']
    def late(stage,target):target.mkdir();return real(stage,target)
    monkeypatch.setattr(consumer,'_publish',late)
    with pytest.raises(FileExistsError):imported(captured,destination)
    assert final.is_dir() and not list(final.iterdir())
    stages=list(destination.glob('.source04-import-*'));assert len(stages)==1
    assert (stages[0]/'source/recording.tar').is_file() and (stages[0]/'.import-failure.json').is_file()


def test_capture_json_duplicate_keys_and_truncated_tar_are_rejected(captured,tmp_path):
    with captured['path'].open('rb') as stream:outer=entries(stream.read())
    outer[0]=('manifest.json',b'{"schema": "first", "schema": "second"}',tarfile.REGTYPE,{})
    path=tmp_path/'duplicate-json.tar';path.write_bytes(pack(outer));review=valid_review(captured,checksum(path))
    with pytest.raises(ValueError,match='Duplicate'):imported(captured,tmp_path/'import',path=path,review=review)
    truncated=tmp_path/'truncated.tar'
    with captured['path'].open('rb') as source:truncated.write_bytes(source.read(1000))
    review=valid_review(captured,checksum(truncated))
    with pytest.raises(ValueError,match='Truncated'):imported(captured,tmp_path/'import-truncated',path=truncated,review=review)
