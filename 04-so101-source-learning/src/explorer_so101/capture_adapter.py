"""ENGINEERING_GLUE: data-only source04 lifecycle around the ORIGINAL writer.

Fixed injected callbacks are colocated with the existing station. No controller,
serial port, camera, sampling thread, network, inference or control operation.
Pause retains raw samples; only the sealed, source-clock selection marks gaps.
"""
from copy import deepcopy
import fcntl
import hashlib
import io
import json
import os
from pathlib import Path,PurePosixPath
import re
import stat
import tarfile
import threading
import time
import uuid

from explorer_shared.capture_timeline import pause_intervals,resume_intervals,source_segment
from explorer_shared.sealed_capture_review import (atomic_bytes,source_bytes,recording_identity,
    read_source_review,review_sealed_capture)
from explorer_shared.so101_station import _record_context

SCHEMA='explorer_so101_source_capture_v1'
CONTRACT='so101_follower_calibrated_position6_v1'
MAX_MANIFEST_BYTES=64*1024**2
MAX_ARCHIVE_BYTES=2*1024**3


def _sha(content):return hashlib.sha256(content).hexdigest()


def _file_digest(path):
    with Path(path).open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()


def _safe_member(name):
    if (not isinstance(name,str) or not name or '\\' in name or '\x00' in name or
            name.startswith('/') or any(part in ('','.','..') for part in name.split('/'))):
        raise ValueError('Original source member must be a safe relative filename')
    return str(PurePosixPath(name))


def _calibrations(status):
    result={}
    for role in ('leader','follower'):
        profile=(status.get('roles') or {}).get(role) or {}
        checksum=profile.get('calibration_sha256')
        if profile.get('verified_on_connected_hardware') is not True or re.fullmatch('[0-9a-f]{64}',str(checksum)) is None:
            raise ValueError('Existing verified leader/follower calibration required')
        result[role]=checksum
    return result


class _HashedReader:
    def __init__(self,source):self.source=source;self.hash=hashlib.sha256();self.count=0
    def read(self,count=-1):
        block=self.source.read(count);self.hash.update(block);self.count+=len(block);return block


class StationCaptureAdapter:
    """One metadata owner. Callbacks expose only the existing recording ABI.

status() and construction never call the injected source. start/finish callbacks
must invoke only record/start or identity-fenced record/finish, never teleop/STOP.
read_manifest(id) returns exact original UTF-8 bytes; archive_original(id) returns
a trusted local Path from the existing Station.archive, never a browser path.
"""
    def __init__(self,root,*,status,record_start,record_finish,read_manifest,archive_original,
                 clock_domain,boot_id,clock=time.monotonic_ns):
        if any(not callable(item) for item in (status,record_start,record_finish,read_manifest,archive_original,clock)):
            raise ValueError('Fixed recording/status/archive callbacks required')
        if any(not isinstance(item,str) or not item for item in (clock_domain,boot_id)):
            raise ValueError('Actual source clock/boot identity required')
        self.root=Path(root);self.root.mkdir(parents=True,exist_ok=True,mode=0o700)
        self._file_lock=(self.root/'.metadata-owner.lock').open('a+b')
        fcntl.flock(self._file_lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        self._status,self._start,self._finish=status,record_start,record_finish
        self._manifest,self._archive=read_manifest,archive_original
        self.domain,self.boot,self.clock=clock_domain,boot_id,clock
        self.lock=threading.RLock();self.operations=threading.Lock()
        self.active=None;self.current=None;self.closed=False;self.recovery_required=False;self._folder_owned=False;self.error=None
        try:
            for path in self.root.glob('*/lifecycle.json'):
                record=json.loads(path.read_bytes())
                if record.get('state') not in ('completed','cancelled','interrupted'):
                    self.recovery_required=True
            # A start may have reached the original writer without returning its ID.
            for path in self.root.glob('attempts/*.json'):
                if json.loads(path.read_bytes()).get('state')!='RETURNED':self.recovery_required=True
        except (OSError,ValueError,TypeError,AttributeError):
            self.recovery_required=True;self.error='LOCAL_CAPTURE_JOURNAL_UNREADABLE'

    def status(self):
        with self.lock:
            return {'schema':SCHEMA,'recording_id':self.active,'capture':deepcopy(self.current),
                'recovery_required':self.recovery_required,'closed':self.closed,'error':self.error,
                'physical_commands_dispatched':False,'original_writer_only':True,
                'pause_retains_raw_samples':True,'automatic_import_or_training':False,
                'source04_runtime_deployment_qualified':False}

    def _save(self):
        if not self._folder_owned:raise RuntimeError('Only the folder created by this capture attempt can be mutated')
        record=deepcopy(self.current)
        atomic_bytes(self.root/record['recording_id']/'lifecycle.json',source_bytes(record))

    def _uncertain(self,reason):
        with self.lock:
            self.recovery_required=True
            self.error=reason
            if self.current is not None:self.current.update(state='UNCERTAIN',error=reason)
        try:
            if self.current is not None and self._folder_owned:self._save()
        except OSError:pass

    def _owned(self,recording_id,states):
        recording_identity(recording_id)
        with self.lock:
            if self.closed or self.recovery_required or self.active!=recording_id or self.current['state'] not in states:
                raise ValueError('Exact active recording/state required; uncertain source is never adopted')
            return deepcopy(self.current)

    def _source(self,owner,*,allow_owner_change=False):
        page=self._status();record=page.get('recording') or {}
        if record.get('episode_id')!=owner['recording_id']:
            raise ValueError('Original writer recording identity changed; never finish a foreign recording')
        domain=(page.get('measurements') or {}).get('clock_domain')
        if domain!=self.domain or record.get('clock_domain')!=self.domain or record.get('boot_id')!=self.boot:
            raise ValueError('Original source boot/clock changed')
        if _calibrations(page)!=owner['calibration_sha256']:
            raise ValueError('Original source calibration changed')
        context=deepcopy(record.get('source_context') or {});context.pop('native_phase_annotation',None)
        if context!=owner['source_context']:raise ValueError('Original recording context changed')
        if not allow_owner_change and (page.get('control_epoch')!=owner['native_control_epoch'] or page.get('mode') not in ('teleoperation','policy')):
            raise ValueError('Original native owner changed; capture cannot adopt another control epoch')
        return page

    def start(self,record_start_body,actor_id):
        if not isinstance(record_start_body,dict) or not isinstance(actor_id,str) or not 1<=len(actor_id)<=200:
            raise ValueError('Validated source04 context/authenticated actor required')
        body=deepcopy(record_start_body)
        if (body.get('project_id')!='04' or body.get('experiment_numbering_version')!=2 or
                body.get('experiment_slug')!='so101_source_learning'):
            raise ValueError('Explicit canonical source04 context required')
        context=_record_context(body)
        if (not context.get('skill_id') or type(context.get('skill_version')) is not int or
                re.fullmatch('[0-9a-f]{64}',str(context.get('skill_spec_sha256'))) is None or
                not all(context.get(key) for key in ('session_id','scene_id','split'))):
            raise ValueError('Pinned existing skill/version/spec/session/scene/split required')
        body['actor']=actor_id
        with self.operations:
            with self.lock:
                if self.closed or self.active is not None or self.recovery_required:
                    raise ValueError('Finish current recording/recovery before starting another')
            page=self._status()
            if page.get('available') is not True or page.get('recording') or page.get('mode') not in ('teleoperation','policy'):
                raise ValueError('Use existing separately user-started station; no automatic teleoperation')
            if (page.get('measurements') or {}).get('clock_domain')!=self.domain:
                raise ValueError('Existing native measured cache clock required')
            calibrations=_calibrations(page);started=self.clock();attempt=uuid.uuid4().hex
            attempts=self.root/'attempts';attempts.mkdir(exist_ok=True)
            pending={'attempt_id':attempt,'state':'STARTING','source_context':context,'actor_id':actor_id,
                'boot_id':self.boot,'clock_domain':self.domain,'started_monotonic_ns':started}
            atomic_bytes(attempts/(attempt+'.json'),source_bytes(pending))
            with self.lock:
                # A failed later start must never rewrite a previous sealed capture.
                self.current=None;self._folder_owned=False
            try:
                result=self._start(body);ident=recording_identity(result.get('episode_id'))
                # Remember the actual returned recording before local persistence.
                with self.lock:
                    self.active=ident
                    self.current={'schema':'explorer_source_capture_lifecycle_v1','recording_id':ident,'attempt_id':attempt,
                        'state':'RECORDING','actor_id':actor_id,'source_context':context,'source_boot_id':self.boot,
                        'source_clock_domain':self.domain,'native_control_epoch':page['control_epoch'],
                        'calibration_sha256':calibrations,'started_monotonic_ns':started,'ended_monotonic_ns':None,
                        'pause_intervals':[],'events':[{'operation':'start','monotonic_ns':started,'revision':0}],
                        'revision':0,'original_start_receipt':deepcopy(result)}
                (self.root/ident).mkdir(mode=0o700)
                self._folder_owned=True
                self._source(self.current);self._save()
                atomic_bytes(attempts/(attempt+'.json'),source_bytes(dict(pending,state='RETURNED',recording_id=ident)))
            except Exception:
                self._uncertain('ORIGINAL_START_OR_LOCAL_PERSISTENCE_UNKNOWN');raise
            return self.status()

    def _pause_transition(self,recording_id,operation):
        with self.operations:
            expected=('RECORDING',) if operation=='pause' else ('PAUSED',)
            owner=self._owned(recording_id,expected);self._source(owner);at=self.clock()
            intervals=(pause_intervals if operation=='pause' else resume_intervals)(owner['pause_intervals'],at,
                started_ns=owner['started_monotonic_ns'])
            with self.lock:
                self.current.update(state='PAUSED' if operation=='pause' else 'RECORDING',pause_intervals=intervals,
                    revision=owner['revision']+1)
                self.current['events'].append({'operation':operation,'monotonic_ns':at,'revision':self.current['revision']})
            try:self._save()
            except Exception:
                self._uncertain('LIFECYCLE_PERSISTENCE_UNKNOWN');raise
            return self.status()

    def pause(self,recording_id):return self._pause_transition(recording_id,'pause')
    def resume(self,recording_id):return self._pause_transition(recording_id,'resume')

    def finish(self,recording_id,outcome='unknown'):
        return self._seal(recording_id,outcome,cancelled=False)

    def cancel(self,recording_id,actor_id):
        owner=self._owned(recording_id,('RECORDING','PAUSED'))
        if actor_id!=owner['actor_id']:raise ValueError('Only the exact authenticated capture actor can cancel')
        return self._seal(recording_id,'unknown',cancelled=True)

    def _seal(self,recording_id,outcome,*,cancelled):
        if outcome not in ('success','failure','unknown'):raise ValueError('Explicit outcome required')
        with self.operations:
            owner=self._owned(recording_id,('RECORDING','PAUSED'));page=self._source(owner,allow_owner_change=True)
            ended=self.clock()
            with self.lock:
                self.current.update(state='FINISHING',ended_monotonic_ns=ended,cancel_requested=cancelled,
                    revision=owner['revision']+1)
                self.current['events'].append({'operation':'cancel' if cancelled else 'finish','monotonic_ns':ended,
                    'revision':self.current['revision']})
            try:
                self._save()
                result=self._finish({'recording_id':recording_id,'outcome':outcome})
                if result.get('episode_id')!=recording_id:raise ValueError('Original finish returned a foreign recording')
                raw=self._manifest(recording_id)
                if not isinstance(raw,bytes) or not 0<len(raw)<=MAX_MANIFEST_BYTES:raise ValueError('Bounded exact original manifest bytes required')
                if result.get('sha256')!=_sha(raw):raise ValueError('Original finish manifest SHA differs from exact source bytes')
                original=json.loads(raw);self._validate_original(original,owner)
                blockers=[]
                if original.get('state')!='complete':blockers.append('ORIGINAL_RECORDING_INTERRUPTED')
                if page.get('control_epoch')!=owner['native_control_epoch']:blockers.append('NATIVE_OWNER_CHANGED')
                selection=[]
                for index,sample in enumerate(original['samples']):
                    at=sample.get('action_monotonic_ns')
                    if sample.get('clock_domain')!=self.domain or sample.get('boot_id')!=self.boot:
                        raise ValueError('Original sample source clock/boot changed')
                    segment=source_segment(at,owner['pause_intervals'],started_ns=owner['started_monotonic_ns'],ended_ns=ended)
                    state_at=sample.get('state_monotonic_ns')
                    state_segment=source_segment(state_at,owner['pause_intervals'],started_ns=owner['started_monotonic_ns'],ended_ns=ended)
                    selected=segment is not None and state_segment==segment
                    selection.append({'source_sample_index':index,'action_monotonic_ns':at,'state_monotonic_ns':state_at,
                        'recording_segment_id':segment if selected else None,'included_after_pause_filter':selected,
                        'selection_basis':'source_state_and_action_in_same_unpaused_segment',
                        'exclusion_reason':None if selected else 'ACTION_OUTSIDE_UNPAUSED_SEGMENT' if segment is None else 'STATE_CROSSES_LIFECYCLE_BOUNDARY'})
                    receipt=sample.get('dispatch_receipt') or {}
                    if receipt.get('control_epoch')!=owner['native_control_epoch'] and 'NATIVE_OWNER_CHANGED' not in blockers:
                        blockers.append('NATIVE_OWNER_CHANGED')
                if not selection:blockers.append('NO_NATIVE_SAMPLES')
                state='interrupted' if blockers else 'cancelled' if cancelled else 'completed'
                with self.lock:
                    self.current.update(state='SEALING',original_finish_receipt=deepcopy(result))
                source=Path(self._archive(recording_id))
                envelope,info=self._bundle(source,raw,original,selection,blockers,state)
                with self.lock:self.current=deepcopy(envelope['lifecycle'])
                self._save()
                atomic_bytes(self.root/recording_id/'archive-info.json',source_bytes(info))
                with self.lock:self.active=None
                return deepcopy(envelope)
            except Exception:
                self._uncertain('ORIGINAL_FINISH_OR_SEAL_UNKNOWN');raise

    def _validate_original(self,original,owner):
        if (original.get('episode_id')!=owner['recording_id'] or original.get('project_id')!='04' or
                original.get('experiment_slug')!='so101_source_learning' or original.get('experiment_numbering_version')!=2 or
                original.get('boot_id')!=self.boot or original.get('clock_domain')!=self.domain):
            raise ValueError('Original sealed source identity changed')
        context=deepcopy(original.get('source_context') or {});context.pop('native_phase_annotation',None)
        if context!=owner['source_context']:raise ValueError('Original sealed source context changed')
        profiles=original.get('calibration_metadata') or {}
        if any((profiles.get(role) or {}).get('calibration_sha256')!=checksum for role,checksum in owner['calibration_sha256'].items()):
            raise ValueError('Original sealed calibration changed')
        if not isinstance(original.get('samples'),list) or len(original['samples'])>10000:
            raise ValueError('Bounded original samples required')

    def _bundle(self,path,raw,original,selection,blockers,sealed_state):
        frames={}
        for sample in original['samples']:
            frame=sample['frame'];name=_safe_member(frame['file'])
            if name in frames:raise ValueError('Original frame member duplicated')
            if re.fullmatch('[0-9a-f]{64}',str(frame.get('sha256'))) is None:raise ValueError('Original JPEG SHA required')
            frames[name]=frame['sha256']
        descriptor=os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
        with os.fdopen(descriptor,'rb') as source:
            size=os.fstat(source.fileno()).st_size
            if not stat.S_ISREG(os.fstat(source.fileno()).st_mode) or not 0<size<=MAX_ARCHIVE_BYTES:
                raise ValueError('Bounded regular original source archive required')
            digest=hashlib.file_digest(source,'sha256').hexdigest();source.seek(0);seen=set()
            with tarfile.open(fileobj=source,mode='r|') as archive:
                for member in archive:
                    if not member.isfile() or member.name in seen or member.name not in {'manifest.json',*frames}:
                        raise ValueError('Unexpected/duplicate source archive member')
                    seen.add(member.name)
                    if member.size>MAX_MANIFEST_BYTES:raise ValueError('Original source member exceeds metadata/frame budget')
                    stream=archive.extractfile(member)
                    if member.name=='manifest.json':
                        if stream.read()!=raw:raise ValueError('Archived original manifest differs from source bytes')
                    elif hashlib.file_digest(stream,'sha256').hexdigest()!=frames[member.name]:
                        raise ValueError('Original archived JPEG changed')
            if seen!={'manifest.json',*frames}:raise ValueError('Original source archive is incomplete')
            envelope={'schema':SCHEMA,'recording_id':original['episode_id'],'project_id':'04',
                'experiment_slug':'so101_source_learning','experiment_numbering_version':2,'action_contract_id':CONTRACT,
                'state':sealed_state,'source_boot_id':self.boot,'source_clock_domain':self.domain,
                'source_context':deepcopy(self.current['source_context']),'lifecycle':dict(deepcopy(self.current),state=sealed_state),
                'original_archive':{'file':'source/recording.tar','sha256':digest,'bytes':size},
                'original_manifest':{'member':'manifest.json','sha256':_sha(raw),'bytes':len(raw)},
                'original_jpegs_sha256':frames,'sample_selection':selection,'block_reasons':blockers,
                'source_kind':original.get('source_kind','SOURCE_DECLARATION_UNSPECIFIED'),
                'raw_paused_samples_retained':True,'raw_action_vectors_unchanged':True,
                'training_admission':'NOT_ASSESSED_USE_EXISTING_NATIVE6_INSPECTOR',
                'operator_outcome':original.get('outcome'),'physical_success_inferred':False,
                'automatic_import_or_training':False}
            folder=self.root/original['episode_id'];payload=source_bytes(envelope)
            temporary=folder/'recording.tar.tmp';source.seek(0);reader=_HashedReader(source)
            with temporary.open('wb') as output:
                with tarfile.open(fileobj=output,mode='w') as bundle:
                    info=tarfile.TarInfo('manifest.json');info.size=len(payload);bundle.addfile(info,io.BytesIO(payload))
                    info=tarfile.TarInfo('source/recording.tar');info.size=size;bundle.addfile(info,reader)
                output.flush();os.fsync(output.fileno())
            if reader.count!=size or reader.hash.hexdigest()!=digest:
                raise ValueError('Original archive changed during sealing; no approval published')
        os.replace(temporary,folder/'recording.tar')
        atomic_bytes(folder/'manifest.json',payload)
        info={'recording_id':original['episode_id'],'archive_sha256':_file_digest(folder/'recording.tar'),
            'manifest_sha256':_sha(payload)}
        return envelope,info

    def manifest(self,recording_id):
        recording_identity(recording_id);folder=self.root/recording_id
        raw=(folder/'manifest.json').read_bytes();info=json.loads((folder/'archive-info.json').read_bytes())
        if _sha(raw)!=info['manifest_sha256']:raise ValueError('Sealed capture metadata changed')
        result=json.loads(raw)
        if result.get('recording_id')!=recording_id:raise ValueError('Sealed recording identity changed')
        if json.loads((folder/'lifecycle.json').read_bytes())!=result['lifecycle']:
            raise ValueError('Capture lifecycle persistence is uncertain or changed')
        return result

    def archive(self,recording_id):
        self.manifest(recording_id);path=self.root/recording_id/'recording.tar'
        if _file_digest(path)!=json.loads(path.with_name('archive-info.json').read_bytes())['archive_sha256']:
            raise ValueError('Sealed source transport archive changed')
        return path

    def review(self,recording_id,decision,actor_id,expected_archive_sha256):
        with self.operations:
            manifest=self.manifest(recording_id);folder=self.root/recording_id
            info=json.loads((folder/'archive-info.json').read_bytes())
            return review_sealed_capture(folder,recording_id,decision,actor_id,expected_archive_sha256,
                state=manifest['state'],original_recording_id=manifest['recording_id'],recorded_archive_sha256=info['archive_sha256'],
                boot_id=self.boot,clock_domain=self.domain,clock=self.clock)

    def recording_review(self,recording_id):return read_source_review(self.root/recording_identity(recording_id),recording_id)

    def recordings(self):
        result=[]
        for path in sorted(self.root.glob('*/archive-info.json')):
            ident=path.parent.name;manifest=self.manifest(ident);info=json.loads(path.read_bytes());review=self.recording_review(ident)
            receipt=review.get('source_review') or {}
            result.append({'recording_id':ident,'project_id':'04','producer_schema':SCHEMA,'state':manifest['state'],
                'archive_sha256':info['archive_sha256'],'manifest_sha256':info['manifest_sha256'],**review,
                'transfer_ready':manifest['state']=='completed' and receipt.get('decision')=='approve' and receipt.get('archive_sha256')==info['archive_sha256']})
        return result

    def close(self):
        with self.operations:
            with self.lock:self.closed=True
            if not self._file_lock.closed:self._file_lock.close()
