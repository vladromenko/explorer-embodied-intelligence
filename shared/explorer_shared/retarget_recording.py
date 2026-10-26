"""method_id: ENGINEERING_GLUE: immutable direct-teaching observation evidence.

Existing Pi camera/cache and authenticated Nano cached snapshots only. Source
clocks remain separate; API acceptance is not measured motion or dispatch. The
writer never creates BC targets from incomplete six-axis host-intent records.
"""
from __future__ import annotations

from dataclasses import dataclass
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import queue
import re
import shutil
import tarfile
import threading
import time
from urllib.request import Request, urlopen
import uuid


FORMAT = 'explorer_direct_retarget_observations_v1'
BASE_BLOCKERS = ('TARGET_DISPATCH_NOT_QUALIFIED', 'TARGET_MEASURED_STATE_ABSENT', 'CAUSAL_FULL_9D_TARGETS_ABSENT')


def digest(path):
    with Path(path).open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()


def canonical(value):
    return json.dumps(value,ensure_ascii=False,sort_keys=True,allow_nan=False).encode()


def write_json(path, value):
    temporary=path.with_suffix('.tmp')
    with temporary.open('wb') as stream:
        stream.write(canonical(value));stream.flush();os.fsync(stream.fileno())
    os.replace(temporary,path)
    fd=os.open(path.parent,os.O_RDONLY|os.O_DIRECTORY)
    try:os.fsync(fd)
    finally:os.close(fd)


def identity(value):
    if not isinstance(value,str) or not re.fullmatch('[0-9a-f]{32}',value):raise ValueError('A server-issued recording ID is required')
    return value


@dataclass(frozen=True)
class CachedImage:
    jpeg: bytes
    metadata: dict


class NanoSnapshotClient:
    """GET the existing single-owner Nano JPEG cache; no camera acquisition API."""
    def __init__(self,base_url,token,*,timeout=.5,clock=time.monotonic_ns):
        self.base_url,self._token,self.timeout,self.clock=base_url.rstrip('/'),token,timeout,clock

    def snapshot(self):
        started=self.clock()
        request=Request(self.base_url+'/cameras/nano-rgbd/snapshot',headers={'Authorization':'Bearer '+self._token})
        with urlopen(request,timeout=self.timeout) as response:
            data=response.read(10*1024**2+1)
            if len(data)>10*1024**2 or not data.startswith(b'\xff\xd8') or not data.endswith(b'\xff\xd9'):
                raise ValueError('Target cache did not provide a bounded JPEG')
            headers=response.headers
            source_ns=headers.get('X-Monotonic-NS')
            source_ns=int(source_ns) if source_ns is not None else None
            meta=dict(camera_id='nano-rgbd',frame_id=headers.get('X-Frame-ID'),
                monotonic_ns=source_ns,clock_domain=headers.get('X-Clock-Domain'),
                wall_utc=headers.get('X-Wall-UTC'),timestamp_basis=headers.get('X-Timestamp-Basis','UNREPORTED'),
                source_wall_utc=headers.get('X-Source-Wall-UTC'),
                exposure_timestamp_available=False,request_started_receiver_ns=started,
                received_receiver_ns=self.clock(),depth_available=False,
                depth_status='NOT_TRANSPORTED_BY_EXISTING_JPEG_SNAPSHOT',
                source='authenticated_existing_node_cached_snapshot',clock_offset_ns=None,clock_uncertainty_ns=None)
            return CachedImage(data,meta)


class RetargetRecorder:
    """A bounded independent writer, with no robot/camera/bus ownership.

    capture() is nonblocking and only snapshots existing immutable bytes. The
    writer refreshes target cache and fsyncs data outside all control locks.
    finish() seals asynchronously after prior queued rows, without blocking STOP.
    """
    def __init__(self,root,source_camera,emit,*,clock_domain,target_camera=None,clock=time.monotonic_ns,queue_limit=32):
        self.root=Path(root);self.root.mkdir(parents=True,exist_ok=True,mode=0o700)
        self.source_camera,self.target_camera,self.emit=source_camera,target_camera,emit
        self.clock_domain,self.clock=clock_domain,clock
        self.lock=threading.RLock();self.active=None;self.target_cache=None
        self.manifest_io_lock=threading.Lock()
        self.items=queue.Queue(maxsize=queue_limit);self.shutdown=threading.Event();self.wake=threading.Event()
        self.error=None;self.serial=0
        self.inflight=set()
        self.frame_ids={'source':set(),'target':set()}
        self._recover()
        self.thread=threading.Thread(target=self._work,name='retarget-recording-writer',daemon=True);self.thread.start()

    def _folder(self,ident):return self.root/identity(ident)

    def manifest(self,ident):
        path=self._folder(ident)/'manifest.json'
        if not path.is_file():raise LookupError('Direct teaching recording not found')
        result=json.loads(path.read_text())
        info=path.parent/'archive-info.json'
        if info.is_file():result={**result,**json.loads(info.read_text())}
        return result

    def recordings(self):
        return [self.manifest(path.parent.name) for path in sorted(self.root.glob('*/manifest.json'),reverse=True)]

    def status(self):
        with self.lock:
            return dict(active=self.active['recording_id'] if self.active else None,
                state=self.active['state'] if self.active else 'idle',writer_error=self.error,
                queued_samples=self.items.qsize(),training_admission='analysis_only',block_reasons=list(BASE_BLOCKERS))

    def _checkpoint_active(self,ident):
        with self.manifest_io_lock:
            with self.lock:
                if self.active is None or self.active['recording_id']!=ident or self.active['state']=='sealing':return
                snapshot=copy.deepcopy(self.active)
            write_json(self._folder(ident)/'manifest.json',snapshot)

    def start(self,*,control_epoch,run_id,registration,name='Показ SO-101 → ROSMASTER',task=None):
        if not isinstance(control_epoch,str) or not control_epoch or not isinstance(run_id,str) or not run_id:
            raise ValueError('Recording requires the current explicit direct teaching session')
        with self.lock:
            if self.active is not None:raise RuntimeError('Finish the current direct recording first')
            if shutil.disk_usage(self.root).free<256*1024**2:raise RuntimeError('Insufficient local recording storage')
            ident=uuid.uuid4().hex;folder=self._folder(ident);folder.mkdir(mode=0o700)
            (folder/'frames').mkdir()
            with (folder/'samples.jsonl').open('wb') as stream:stream.flush();os.fsync(stream.fileno())
            manifest=dict(format=FORMAT,schema_version=1,recording_id=ident,project_id='03',state='recording',
                name=str(name)[:120],task=str(task or name)[:240],direct_run_id=run_id,initial_control_epoch=control_epoch,
                registration=copy.deepcopy(registration),source_type='direct_so101_leader_rosmaster_observations',
                source_receiver_clock_domain=self.clock_domain,created_receiver_ns=self.clock(),
                source_clock_relationship='independent_domains_no_offset_estimate',samples_written=0,samples_dropped=0,
                attempted_sample_tickets=0,source_frames=0,target_frames=0,outcome='unknown',outcome_basis='not_yet_reported',
                unique_source_frames=0,unique_target_frames=0,frame_count_basis='sampled_references_may_repeat_cached_frame',
                action_contract='ARM_ONLY_six_servo_endpoints_not_full_9D',state_basis='target_state_unknown_not_command_echo',
                action_basis='requested_and_API_accepted_host_intent_not_dispatched',training_admission='analysis_only',
                dataset_manifest=None,block_reasons=list(BASE_BLOCKERS),physical_success_inferred=False,
                target_depth_available=False,controller_setting_writes=0,chassis_commands=0)
            write_json(folder/'manifest.json',manifest)
            self.active=manifest;self.target_cache=None;self.serial=0;self.error=None;self.inflight=set()
            self.frame_ids={'source':set(),'target':set()}
        self.wake.set();return dict(manifest)

    def capture(self,control_epoch,run_id=None):
        with self.lock:
            if self.active is None or self.active['state']!='recording':return None
            if run_id is not None and self.active['direct_run_id']!=run_id:return None
            ident=self.active['recording_id'];self.serial+=1
            self.inflight.add(self.serial)
            self.active['attempted_sample_tickets']=self.serial
            target=self.target_cache
            serial=self.serial
        source=None
        if self.source_camera is not None:
            frame=self.source_camera.latest()
            if frame is not None:
                source=CachedImage(frame.jpeg,{**frame.metadata(),'camera_id':'pi-overview','clock_domain':self.clock_domain,
                    'exposure_timestamp_available':False,'source':'existing_single_owner_Pi_camera_cache'})
        return dict(recording_id=ident,ticket=serial,route_control_epoch=control_epoch,
            captured_receiver_ns=self.clock(),source=source,target=target)

    def append(self,ticket,measurement,result,*,action_started_ns,action_completed_ns):
        if ticket is None:return False
        with self.lock:
            if self.active is None or self.active['recording_id']!=ticket['recording_id'] or self.active['state'] not in ('recording','finalizing') or ticket['ticket'] not in self.inflight:
                return False  # A late old attempt can never label a new recording.
            self.inflight.remove(ticket['ticket'])
            item=dict(kind='sample',ticket=ticket,measurement=copy.deepcopy(measurement),result=copy.deepcopy(result),
                      action_started_ns=action_started_ns,action_completed_ns=action_completed_ns)
            try:self.items.put_nowait(item)
            except queue.Full:
                self.active['samples_dropped']+=1;return False
        self.wake.set();return True

    def finish(self,ident,outcome='unknown'):
        identity(ident)
        if outcome not in ('success','failure','unknown'):raise ValueError('Outcome must be success, failure or unknown')
        with self.lock:
            if self.active is None or self.active['recording_id']!=ident:
                saved=self.manifest(ident)
                if saved['state'] in ('completed','interrupted','failed') and saved.get('outcome')==outcome:return saved
                raise ValueError('Recording session changed; old finish cannot label the current attempt')
            if self.active['state'] in ('finalizing','sealing'):
                if self.active['outcome']!=outcome:raise ValueError('Outcome is already being sealed for this attempt')
                return dict(self.active)
            self.active.update(state='finalizing',outcome=outcome,outcome_basis='operator_report_not_independent_validation',
                finish_requested_receiver_ns=self.clock(),pending_action_tickets=len(self.inflight))
        self._checkpoint_active(ident)
        self.wake.set();return self.manifest(ident)

    def _image(self,folder,captured,role,action_started_ns):
        if captured is None:return dict(available=False,reason='NO_PREACTION_CACHED_FRAME')
        checksum=hashlib.sha256(captured.jpeg).hexdigest()
        name='frames/'+role+'-'+checksum+'.jpg';path=folder/name
        if not path.exists():
            with path.open('xb') as stream:
                stream.write(captured.jpeg);stream.flush();os.fsync(stream.fileno())
            fd=os.open(path.parent,os.O_RDONLY|os.O_DIRECTORY)
            try:os.fsync(fd)
            finally:os.close(fd)
        if digest(path)!=checksum:raise ValueError('Recorded cached image checksum mismatch')
        metadata=copy.deepcopy(captured.metadata)
        receiver_ns=metadata.get('received_receiver_ns') if role=='target' else metadata.get('monotonic_ns')
        same_clock=role=='target' or metadata.get('clock_domain')==self.clock_domain
        causal=bool(same_clock and type(receiver_ns) is int and receiver_ns<=action_started_ns)
        return dict(available=True,file=name,sha256=checksum,bytes=len(captured.jpeg),metadata=metadata,
            received_before_action_request=causal,clock_comparison_basis='Pi_receipt_to_Pi_action_request' if role=='target' else 'same_Pi_boot_clock',
            source_capture_to_target_dispatch_alignment='UNKNOWN',target_exposure_alignment_qualified=False)

    def _save_sample(self,item):
        ticket=item['ticket'];ident=ticket['recording_id'];folder=self._folder(ident)
        with self.lock:
            active=self.active
            if active is None or active['recording_id']!=ident:raise ValueError('Writer sample belongs to another attempt')
        start,done=item['action_started_ns'],item['action_completed_ns']
        if type(start) is not int or type(done) is not int or not ticket['captured_receiver_ns']<=start<=done:
            raise ValueError('Direct recording request chronology differs')
        pending_bytes=sum(len(image.jpeg) for image in (ticket['source'],ticket['target']) if image is not None)
        if shutil.disk_usage(self.root).free<256*1024**2+pending_bytes*2:
            raise RuntimeError('Local recording storage reserve reached; raw originals retained')
        source=self._image(folder,ticket['source'],'source',start)
        target=self._image(folder,ticket['target'],'target',start)
        result=item['result'];receipt=result.get('target_receipt') or {}
        blockers=set(BASE_BLOCKERS)
        for role,frame in (('source',source),('target',target)):
            if not frame['available']:blockers.add(role.upper()+'_CAMERA_UNAVAILABLE')
            elif not frame['received_before_action_request']:blockers.add(role.upper()+'_CAMERA_NOT_CAUSALLY_BEFORE_REQUEST')
        if target['available'] and not target['metadata'].get('clock_domain'):blockers.add('TARGET_CLOCK_DOMAIN_UNREPORTED')
        if item['measurement'].get('clock_domain')!=self.clock_domain:blockers.add('SOURCE_STATE_CLOCK_ALIGNMENT_UNREPORTED')
        row=dict(schema_version=1,recording_id=ident,ticket=ticket['ticket'],route_control_epoch=ticket['route_control_epoch'],
            receiver_clock_domain=self.clock_domain,observation_snapshot_receiver_ns=ticket['captured_receiver_ns'],
            action_request_receiver_ns=start,action_reply_receiver_ns=done,leader_measured_state=item['measurement'],
            source_camera=source,target_camera=target,retarget_result=result,
            requested_target_action=receipt.get('requested'),API_accepted_target_action=receipt.get('accepted'),
            dispatched_target_action=None,target_measured_state=None,base_action=None,
            target_dispatch_qualified=False,training_admission='analysis_only',block_reasons=sorted(blockers),physical_success_inferred=False)
        with (folder/'samples.jsonl').open('ab') as stream:
            stream.write(canonical(row)+b'\n');stream.flush();os.fsync(stream.fileno())
        with self.lock:
            self.active['samples_written']+=1
            self.active['pending_action_tickets']=len(self.inflight)
            self.active['block_reasons']=sorted(set(self.active['block_reasons'])|blockers)
            self.active['source_frames']+=int(source['available']);self.active['target_frames']+=int(target['available'])
            for role,frame in (('source',source),('target',target)):
                metadata=frame.get('metadata') or {}
                if frame['available'] and metadata.get('frame_id'):
                    self.frame_ids[role].add((metadata.get('clock_domain'),metadata['frame_id']))
                self.active['unique_'+role+'_frames']=len(self.frame_ids[role])
        self._checkpoint_active(ident)

    def _seal(self,manifest):
        ident=manifest['recording_id'];folder=self._folder(ident)
        if manifest['state']=='finalizing':manifest={**manifest,'state':'completed','completed_receiver_ns':self.clock()}
        write_json(folder/'manifest.json',manifest)
        files={str(path.relative_to(folder)):digest(path) for path in [folder/'manifest.json',folder/'samples.jsonl',*sorted((folder/'frames').glob('*.jpg'))]}
        if shutil.disk_usage(self.root).free<256*1024**2+sum((folder/name).stat().st_size for name in files):
            raise RuntimeError('Insufficient space to seal archive; raw originals retained')
        fingerprint=hashlib.sha256(canonical(files)).hexdigest()
        transport=dict(format=FORMAT,source_files_sha256=files,fingerprint=fingerprint,original_retained=True,
            training_admission='analysis_only',block_reasons=manifest['block_reasons'])
        archive=folder/'recording.tar';temporary=folder/'recording.tar.tmp'
        with tarfile.open(temporary,'w') as bundle:
            for name,expected in files.items():
                path=folder/name
                info=tarfile.TarInfo(name);info.size=path.stat().st_size
                with path.open('rb') as stream:bundle.addfile(info,stream)
                if digest(path)!=expected:raise ValueError('Retarget source changed while sealing archive')
            raw=canonical(transport);info=tarfile.TarInfo('transport.json');info.size=len(raw);bundle.addfile(info,io.BytesIO(raw))
        with temporary.open('rb') as stream:os.fsync(stream.fileno())
        os.replace(temporary,archive)
        info=dict(archive_sha256=digest(archive),archive_bytes=archive.stat().st_size,source_fingerprint=fingerprint)
        write_json(folder/'archive-info.json',info)
        event_id=self.emit(dict(kind='retarget.recording.finished',recording_id=ident,state=manifest['state'],
            frame_count=manifest.get('unique_source_frames',0)+manifest.get('unique_target_frames',0),
            frame_observation_references=manifest['source_frames']+manifest['target_frames'],samples=manifest['samples_written'],
            archive_sha256=info['archive_sha256'],archive_bytes=info['archive_bytes'],training_admission='analysis_only',
            block_reasons=manifest['block_reasons'],physical_success_inferred=False),run_id=ident,project_id='03')
        write_json(folder/'completion-event.json',dict(event_id=event_id,archive_sha256=info['archive_sha256']))

    def _recover(self):
        for path in self.root.glob('*/manifest.json'):
            manifest=json.loads(path.read_text())
            if manifest.get('format')!=FORMAT:raise ValueError('Unexpected direct recording source format')
            if manifest['state'] in ('recording','finalizing'):
                manifest.update(state='interrupted',error='observer_restarted',pending_samples_lost_on_restart='UNKNOWN',
                    completed_receiver_ns=self.clock())
                self._seal(manifest)
            elif not (path.parent/'completion-event.json').is_file():self._seal(manifest)

    def _work(self):
        next_capture=0.
        while not self.shutdown.is_set():
            try:
                with self.lock:
                    active=dict(self.active) if self.active else None
                if active and active['state']=='recording' and self.target_camera is not None and self.items.empty() and time.monotonic()>=next_capture:
                    ident=active['recording_id'];next_capture=time.monotonic()+.2
                    try:
                        target=self.target_camera.snapshot()
                        self.error=None
                    except Exception as error:
                        self.error='Target cached snapshot unavailable: '+type(error).__name__;target=None
                    with self.lock:
                        if self.active and self.active['recording_id']==ident and self.active['state']=='recording':self.target_cache=target
                item=None
                try:item=self.items.get_nowait()
                except queue.Empty:pass
                if item is not None:
                    try:self._save_sample(item)
                    finally:self.items.task_done()
                with self.lock:
                    finishing=copy.deepcopy(self.active) if self.active and self.active['state']=='finalizing' and self.items.empty() and not self.inflight else None
                if finishing is not None:
                    with self.manifest_io_lock:
                        with self.lock:self.active['state']='sealing'
                        self._seal(finishing)
                        with self.lock:
                            if self.active and self.active['recording_id']==finishing['recording_id']:self.active=None;self.target_cache=None
                if item is None:self.wake.wait(.05);self.wake.clear()
            except Exception as error:
                self.error=type(error).__name__+': '+str(error)
                with self.lock:
                    if self.active:
                        self.active.update(state='failed',error=self.error)
                        write_json(self._folder(self.active['recording_id'])/'manifest.json',self.active)
                self.shutdown.wait(.1)

    def archive(self,ident):
        descriptor=self.manifest(ident)
        if descriptor['state'] not in ('completed','interrupted','failed'):raise ValueError('Finish the direct recording before transfer')
        path=self._folder(ident)/'recording.tar'
        if not path.is_file() or digest(path)!=descriptor.get('archive_sha256'):raise ValueError('Direct recording archive is missing or changed')
        return path

    def close(self):
        with self.lock:
            if self.active and self.active['state']=='recording':
                self.active.update(state='finalizing',outcome='unknown',outcome_basis='observer_shutdown_not_operator_report')
        self.wake.set()
        deadline=time.monotonic()+5
        while self.active is not None and time.monotonic()<deadline:time.sleep(.02)
        self.shutdown.set();self.wake.set();self.thread.join(timeout=2)


def import_retarget_recording(archive_path,destination,*,expected_archive_sha256=None):
    """Immutable raw import only; absent full causal 9D evidence blocks BC export."""
    archive_path,destination=Path(archive_path),Path(destination)
    source_sha=digest(archive_path)
    if expected_archive_sha256 is not None and source_sha!=expected_archive_sha256:
        raise ValueError('Transferred retarget archive SHA256 differs from the expected source')
    destination.mkdir(parents=True,exist_ok=True)
    marker=destination/'import-result.json'
    if marker.is_file():
        previous=json.loads(marker.read_text())
        if previous['archive_sha256']!=source_sha:raise ValueError('Import destination belongs to another immutable archive')
        for name,expected in previous['source_files_sha256'].items():
            if digest(destination/'source_originals'/name)!=expected:raise ValueError('Imported retarget original changed')
        if digest(destination/'source_originals/transport.json')!=previous['transport_sha256']:
            raise ValueError('Imported retarget transport metadata changed')
        return previous
    originals=destination/'source_originals';originals.mkdir(exist_ok=True)
    with tarfile.open(archive_path) as bundle:
        entries=bundle.getmembers()
        if len(entries)>100000 or len({entry.name for entry in entries})!=len(entries):raise ValueError('Duplicate or excessive archive members')
        transport=json.load(bundle.extractfile('transport.json'))
        files=transport['source_files_sha256']
        if transport.get('format')!=FORMAT or hashlib.sha256(canonical(files)).hexdigest()!=transport.get('fingerprint'):
            raise ValueError('Retarget transport fingerprint differs')
        if set(entry.name for entry in entries)!=set(files)|{'transport.json'}:raise ValueError('Unindexed archive member')
        for entry in entries:
            name=entry.name
            if not entry.isfile() or Path(name).is_absolute() or '..' in Path(name).parts or entry.size>1024**3:
                raise ValueError('Retarget archive member escapes its immutable source')
            data=bundle.extractfile(entry).read()
            if name in files and hashlib.sha256(data).hexdigest()!=files[name]:raise ValueError('Retarget source checksum mismatch')
            target=originals/name;target.parent.mkdir(parents=True,exist_ok=True)
            if target.exists() and target.read_bytes()!=data:raise ValueError('Existing imported original differs; no overwrite')
            if not target.exists():
                with target.open('xb') as stream:stream.write(data);stream.flush();os.fsync(stream.fileno())
    manifest=json.loads((originals/'manifest.json').read_text())
    if manifest.get('format')!=FORMAT or manifest.get('state') not in ('completed','interrupted','failed'):
        raise ValueError('Retarget recording is not a sealed compatible raw source')
    rows=[json.loads(line) for line in (originals/'samples.jsonl').read_text().splitlines() if line.strip()]
    if len(rows)!=manifest['samples_written']:raise ValueError('Retarget raw row count differs from sealed manifest')
    for row in rows:
        if row.get('recording_id')!=manifest['recording_id'] or row.get('receiver_clock_domain')!=manifest['source_receiver_clock_domain']:
            raise ValueError('Raw retarget sample belongs to another source recording/clock')
        for camera in ('source_camera','target_camera'):
            frame=row[camera]
            if frame.get('available') and (frame['file'] not in files or files[frame['file']]!=frame['sha256']):
                raise ValueError('Raw retarget frame reference differs from indexed original')
    result=dict(format='explorer_direct_retarget_analysis_import_v1',recording_id=manifest['recording_id'],
        archive_sha256=source_sha,source_files_sha256=files,transport_sha256=digest(originals/'transport.json'),
        source_original_retained=True,source_observations=len(rows),
        outcome=manifest['outcome'],outcome_basis=manifest['outcome_basis'],training_admission='analysis_only',
        dataset_manifest=None,BC_targets_created=False,block_reasons=sorted(set(BASE_BLOCKERS)|set(manifest.get('block_reasons',[]))),
        clock_alignment='UNKNOWN_between_original_Pi_and_Nano_domains',physical_success_inferred=False)
    write_json(marker,result);return result
