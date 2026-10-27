"""ENGINEERING_GLUE: bounded local source04 transport verification/import only.

Reuses Source approval and canonical timeline validators. No Dataset/converter,
model, network, robot command, action modification or deletion of raw evidence.
"""
from contextlib import contextmanager
from copy import deepcopy
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import tarfile
import uuid

from explorer_shared.capture_timeline import select_source_samples,pause_intervals,resume_intervals
from explorer_shared.native_capture_approval import validate_source_approval,source_bytes
from explorer_shared.sealed_capture_review import atomic_bytes,recording_identity
from explorer_shared.so101_station import NAMES,UNITS,LOW,HIGH
from .capture_adapter import SCHEMA,CONTRACT

CHUNK=64*1024
MAX_JSON=64*1024**2
MAX_INNER=2*1024**3
MAX_OUTER=MAX_INNER+MAX_JSON+1024**2


def _sha(value):return hashlib.sha256(value).hexdigest()


def _checksum(value):
    if not isinstance(value,str) or re.fullmatch('[0-9a-f]{64}',value) is None:
        raise ValueError('Exact original SHA256 required')
    return value


def _json(raw,limit=MAX_JSON):
    if not isinstance(raw,bytes) or not 0<len(raw)<=limit:raise ValueError('Bounded original JSON bytes required')
    def pairs(items):
        result={}
        for key,value in items:
            if key in result:raise ValueError('Duplicate original JSON key')
            result[key]=value
        return result
    def invalid(value):raise ValueError('Nonfinite original JSON value')
    return json.loads(raw,object_pairs_hook=pairs,parse_constant=invalid)


def _name(value):
    if (not isinstance(value,str) or not value or len(value.encode())>4096 or '\\' in value or
            value.startswith('/') or any(ord(char)<32 or ord(char)==127 for char in value) or
            any(part in ('','.','..') for part in value.split('/')) or len(value.split('/'))>16):
        raise ValueError('Safe bounded original relative member required')
    return value


@contextmanager
def _regular(path,limit):
    descriptor=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    with os.fdopen(descriptor,'rb') as stream:
        info=os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or not 0<info.st_size<=limit:
            raise ValueError('Bounded regular source file required')
        yield stream,info


def _digest(path,limit=MAX_OUTER):
    with _regular(path,limit) as (stream,_):return hashlib.file_digest(stream,'sha256').hexdigest()


def _publish(stage,final):
    """Linux managed import publication must not replace even an empty directory."""
    import ctypes
    native=ctypes.CDLL(None,use_errno=True)
    rename=getattr(native,'renameat2',None)
    if rename is None:raise OSError('Atomic no-replace import publication is unavailable')
    rename.argtypes=[ctypes.c_int,ctypes.c_char_p,ctypes.c_int,ctypes.c_char_p,ctypes.c_uint]
    rename.restype=ctypes.c_int
    if rename(-100,os.fsencode(stage),-100,os.fsencode(final),1)!=0:
        number=ctypes.get_errno();raise OSError(number,os.strerror(number),str(final))


def _preflight(stream,size,max_files):
    """Small raw-header allocation guard before stdlib's extension parser.

    This producer's short paths use regular USTAR files and local PAX times.
    Reject GNU/path/size/sparse extensions; no custom TAR extraction is performed.
    """
    if size%512:raise ValueError('Truncated source TAR block')
    stream.seek(0);headers=0;pending=False
    while stream.tell()<size:
        header=stream.read(512)
        if len(header)!=512:raise ValueError('Truncated source TAR header')
        if header==bytes(512):
            if pending or stream.read(512)!=bytes(512):raise ValueError('Incomplete source TAR terminator')
            while stream.tell()<size:
                if stream.read(min(CHUNK,size-stream.tell())).strip(b'\0'):raise ValueError('Nonzero source TAR trailer')
            stream.seek(0);return
        headers+=1
        if headers>2*max_files:raise ValueError('Source TAR header count exceeds bound')
        field=header[124:136].strip(b'\0 ')
        if not re.fullmatch(b'[0-7]{1,12}',field):raise ValueError('Unsupported source TAR size encoding')
        length=int(field,8);kind=header[156:157]
        if kind not in (b'0',b'\0',b'x'):raise ValueError('Source TAR links/GNU/sparse/other types forbidden')
        if length>size-stream.tell():raise ValueError('Truncated source TAR member')
        if kind==b'x':
            if pending or not 0<length<=CHUNK:raise ValueError('Source PAX metadata exceeds bound')
            raw=stream.read(length);offset=0
            while offset<len(raw):
                separator=raw.find(b' ',offset,offset+12);digits=raw[offset:separator] if separator!=-1 else b''
                if not re.fullmatch(b'[1-9][0-9]{0,8}',digits):raise ValueError('Malformed bounded PAX record')
                end=offset+int(digits)
                if end>len(raw) or end<=separator+2 or raw[end-1:end]!=b'\n':raise ValueError('Truncated PAX record')
                key,equals,value=raw[separator+1:end-1].partition(b'=')
                if not equals or key not in (b'mtime',b'atime',b'ctime') or re.fullmatch(rb'-?[0-9]{1,20}(?:\.[0-9]{1,20})?',value) is None:
                    raise ValueError('Unsupported PAX path/size/sparse metadata')
                offset=end
            pending=True
        else:
            stream.seek(length,os.SEEK_CUR);pending=False
        padding=(-length)%512
        if stream.read(padding)!=bytes(padding):raise ValueError('Truncated/nonzero source TAR padding')
    raise ValueError('Source TAR missing terminator')


class _ParserBounds:
    """Cap stdlib metadata reads even if an input changes after preflight."""
    def __init__(self,stream,size):self.stream,self.size=stream,size
    def read(self,size):
        if type(size) is not int or not 0<=size<=CHUNK:raise ValueError('TAR parser metadata allocation bound')
        return self.stream.read(min(size,self.size-self.tell()))
    def tell(self):return self.stream.tell()
    def seek(self,offset,whence=os.SEEK_SET):
        target=offset if whence==os.SEEK_SET else self.tell()+offset if whence==os.SEEK_CUR else self.size+offset
        if not 0<=target<=self.size:raise ValueError('TAR parser seek exceeds source bound')
        return self.stream.seek(target)


def _unpack(stream,size,expected,paths,*,json_member):
    _preflight(stream,size,len(expected));seen=set();checksums={};raw=None
    with tarfile.open(fileobj=_ParserBounds(stream,size),mode='r:') as archive:
        for member in archive:
            name=_name(member.name)
            if (name not in expected or name in seen or not member.isfile() or member.sparse is not None or
                    not 0<=member.size<=expected[name][0]):raise ValueError('Unexpected/duplicate/unsafe/oversized source TAR member')
            seen.add(name);output=None;parts=[];digest=hashlib.sha256();read=0
            target=paths.get(name)
            if target is not None:
                target.parent.mkdir(parents=True,exist_ok=True)
                output=os.fdopen(os.open(target,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600),'wb')
            try:
                with archive.extractfile(member) as content:
                    while read<member.size:
                        block=content.read(min(CHUNK,member.size-read))
                        if not block:raise ValueError('Incomplete original source member bytes')
                        read+=len(block);digest.update(block)
                        if output is not None:output.write(block)
                        if name==json_member:parts.append(block)
                if output is not None:output.flush();os.fsync(output.fileno())
            finally:
                if output is not None:output.close()
            checksum=digest.hexdigest()
            if expected[name][1] is not None and checksum!=expected[name][1]:raise ValueError('Original source member SHA changed')
            checksums[name]={'sha256':checksum,'bytes':read}
            if name==json_member:raw=b''.join(parts)
    if seen!=set(expected):raise ValueError('Missing original source TAR member')
    return raw,checksums


def _capture(raw,recording_id):
    capture=_json(raw)
    if not isinstance(capture,dict):raise ValueError('Original source capture object required')
    life=capture.get('lifecycle');context=capture.get('source_context')
    if not isinstance(life,dict):raise ValueError('Original source lifecycle object required')
    if (capture.get('schema')!=SCHEMA or capture.get('recording_id')!=recording_id or capture.get('project_id')!='04' or
            capture.get('experiment_slug')!='so101_source_learning' or type(capture.get('experiment_numbering_version')) is not int or
            capture['experiment_numbering_version']!=2 or capture.get('action_contract_id')!=CONTRACT or
            capture.get('state')!='completed' or capture.get('block_reasons')!=[] or life.get('cancel_requested') is not False or
            capture.get('raw_paused_samples_retained') is not True or capture.get('raw_action_vectors_unchanged') is not True or
            capture.get('physical_success_inferred') is not False or capture.get('automatic_import_or_training') is not False):
        raise ValueError('Exact completed canonical source04/native6 envelope required')
    if (not isinstance(context,dict) or life.get('schema')!='explorer_source_capture_lifecycle_v1' or
            life.get('state')!='completed' or life.get('recording_id')!=recording_id or
            source_bytes(life.get('source_context'))!=source_bytes(context) or
            life.get('source_boot_id')!=capture.get('source_boot_id') or life.get('source_clock_domain')!=capture.get('source_clock_domain')):
        raise ValueError('Sealed source lifecycle/context/clock identity changed')
    for value in (capture.get('source_boot_id'),capture.get('source_clock_domain'),life.get('native_control_epoch'),life.get('actor_id')):
        if not isinstance(value,str) or not 1<=len(value)<=240:raise ValueError('Exact source boot/clock/owner/actor required')
    if (context.get('project_id')!='04' or context.get('experiment_slug')!='so101_source_learning' or
            type(context.get('experiment_numbering_version')) is not int or context.get('experiment_numbering_version')!=2 or not context.get('skill_id') or
            type(context.get('skill_version')) is not int or context['skill_version']<1 or
            context.get('split') not in ('train','development','sealed_test') or
            any(not isinstance(context.get(key),str) or not context[key] for key in ('scene_id','session_id'))):
        raise ValueError('Original pinned source skill/session/split context required')
    _checksum(context.get('skill_spec_sha256'))
    calibration=life.get('calibration_sha256')
    if not isinstance(calibration,dict) or set(calibration)!={'leader','follower'}:raise ValueError('Both original motor calibration identities required')
    for checksum in calibration.values():_checksum(checksum)
    archive=capture.get('original_archive') or {};manifest=capture.get('original_manifest') or {}
    if (archive.get('file')!='source/recording.tar' or manifest.get('member')!='manifest.json' or
            type(archive.get('bytes')) is not int or not 0<archive['bytes']<=MAX_INNER or
            type(manifest.get('bytes')) is not int or not 0<manifest['bytes']<=MAX_JSON):
        raise ValueError('Exact original inner archive/manifest descriptors required')
    _checksum(archive.get('sha256'));_checksum(manifest.get('sha256'))
    frames=capture.get('original_jpegs_sha256')
    if not isinstance(frames,dict) or len(frames)>10000:raise ValueError('Bounded exact source JPEG inventory required')
    for name,checksum in frames.items():
        _name(name);_checksum(checksum)
        if name=='manifest.json':raise ValueError('Original JPEG and manifest member collide')
    events=life.get('events');intervals=[]
    if not isinstance(events,list) or not 2<=len(events)<=1026:raise ValueError('Bounded source lifecycle event chain required')
    previous=life.get('started_monotonic_ns')
    for revision,event in enumerate(events):
        if not isinstance(event,dict):raise ValueError('Original lifecycle event object required')
        stamp=event.get('monotonic_ns')
        if type(stamp) is not int or type(previous) is not int or stamp<previous or type(event.get('revision')) is not int or event['revision']!=revision:
            raise ValueError('Source lifecycle event revision/time changed')
        operation=event.get('operation')
        if revision==0:
            if operation!='start' or stamp!=life['started_monotonic_ns']:raise ValueError('Original source start event changed')
        elif revision==len(events)-1:
            if operation!='finish' or stamp!=life.get('ended_monotonic_ns'):raise ValueError('Original source finish event changed')
        elif operation in ('pause','resume'):
            intervals=(pause_intervals if operation=='pause' else resume_intervals)(intervals,stamp,started_ns=life['started_monotonic_ns'])
        else:raise ValueError('Unknown source lifecycle transition')
        previous=stamp
    if (type(life.get('revision')) is not int or life['revision']!=len(events)-1 or
            source_bytes(intervals)!=source_bytes(life.get('pause_intervals'))):raise ValueError('Source pause interval/event chain changed')
    return capture


def _original(raw,capture):
    original=_json(raw);life=capture['lifecycle']
    if not isinstance(original,dict) or not isinstance(original.get('source_context'),dict):
        raise ValueError('Original native6 manifest/context object required')
    context=deepcopy(original['source_context'])
    context.pop('native_phase_annotation',None)
    if (original.get('episode_id')!=capture['recording_id'] or original.get('project_id')!='04' or
            original.get('experiment_slug')!='so101_source_learning' or type(original.get('experiment_numbering_version')) is not int or
            original['experiment_numbering_version']!=2 or original.get('state')!='complete' or
            original.get('boot_id')!=capture['source_boot_id'] or original.get('clock_domain')!=capture['source_clock_domain'] or
            source_bytes(context)!=source_bytes(capture['source_context']) or original.get('action_names')!=list(NAMES) or
            original.get('action_units')!=list(UNITS) or source_bytes(original.get('normalization'))!=source_bytes({'low':LOW,'high':HIGH}) or
            original.get('state_basis')!='measured_state' or original.get('action_basis')!='dispatched_command'):
        raise ValueError('Original native6 manifest/source context/clock changed')
    for role,checksum in life['calibration_sha256'].items():
        if (original.get('calibration_metadata',{}).get(role) or {}).get('calibration_sha256')!=checksum:
            raise ValueError('Original motor calibration SHA changed')
    for key in ('skill_id','skill_version','skill_spec_sha256','scene_id','session_id','split'):
        if original.get(key)!=context.get(key):raise ValueError('Original top-level skill context conflicts')
    finish=life.get('original_finish_receipt') or {};start=life.get('original_start_receipt') or {}
    if (finish.get('episode_id')!=capture['recording_id'] or start.get('episode_id')!=capture['recording_id'] or
            finish.get('project_id')!='04' or start.get('project_id')!='04' or start.get('state')!='recording' or
            finish.get('sha256')!=_sha(raw) or finish.get('state')!='complete' or finish.get('outcome')!=original.get('outcome')):
        raise ValueError('Original recording receipt/manifest binding changed')
    if (capture.get('operator_outcome')!=original.get('outcome') or
            capture.get('source_kind')!=original.get('source_kind','SOURCE_DECLARATION_UNSPECIFIED')):
        raise ValueError('Original outcome/source-kind declaration changed')
    selection=select_source_samples(original.get('samples'),life['pause_intervals'],started_ns=life['started_monotonic_ns'],
        ended_ns=life['ended_monotonic_ns'],clock_domain=capture['source_clock_domain'],boot_id=capture['source_boot_id'])
    if source_bytes(selection)!=source_bytes(capture.get('sample_selection')):raise ValueError('Source pause sample selection changed')
    frames={}
    for sample in original['samples']:
        for key in ('state','action','requested_action','accepted_action','dispatched_action','leader_state'):
            vector=sample.get(key)
            if vector is not None or key in ('state','action'):
                if not isinstance(vector,list) or len(vector)!=6 or any(type(value) not in (int,float) or not math.isfinite(value) for value in vector):
                    raise ValueError('Original native6 vector shape/values changed')
        frame=sample.get('frame') or {};name=_name(frame.get('file'))
        if name in frames:raise ValueError('Repeated original source JPEG path')
        frames[name]=_checksum(frame.get('sha256'))
        receipt=sample.get('dispatch_receipt') or {}
        if (receipt.get('control_epoch')!=life['native_control_epoch'] or receipt.get('boot_id')!=capture['source_boot_id'] or
                receipt.get('clock_domain')!=capture['source_clock_domain'] or receipt.get('available') is not True or
                receipt.get('monotonic_ns')!=sample['action_monotonic_ns']):
            raise ValueError('Original host dispatch owner/clock evidence changed')
    if source_bytes(frames)!=source_bytes(capture['original_jpegs_sha256']):raise ValueError('Original source JPEG inventory changed')
    return original


def import_source_capture(artifact,archive_sha256,destination,recording_id,source_review):
    """Local verified import, preserving failed staging and every source byte.

    Caller owns fixed authenticated GETs/latest-review revalidation and the outer
    managed artifact. This function never decides expert/Dataset/train readiness.
    """
    recording_identity(recording_id);_checksum(archive_sha256)
    review_raw=source_review if isinstance(source_review,bytes) else source_bytes(source_review)
    review=_json(review_raw,32*1024);approval=validate_source_approval(review,recording_id,archive_sha256)
    artifact=Path(artifact);destination=Path(destination).resolve()
    with _regular(artifact,MAX_OUTER) as (source,before):
        if hashlib.file_digest(source,'sha256').hexdigest()!=archive_sha256:raise ValueError('Source outer archive SHA mismatch')
        destination.mkdir(parents=True,exist_ok=True,mode=0o700)
        lock=os.open(destination/('.source04-'+recording_id+'.lock'),os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW|os.O_NONBLOCK,0o600)
        with os.fdopen(lock,'a+b') as held:
            if not stat.S_ISREG(os.fstat(held.fileno()).st_mode):raise ValueError('Managed import lock must be regular')
            fcntl.flock(held,fcntl.LOCK_EX|fcntl.LOCK_NB)
            final=destination/recording_id;existing=os.path.lexists(final);stage=None
            if existing and (final.is_symlink() or not final.is_dir()):raise ValueError('Existing source destination is not a regular directory')
            if not existing:
                stage=destination/('.source04-import-'+recording_id+'-'+uuid.uuid4().hex);stage.mkdir(mode=0o700)
            work=final if existing else stage
            try:
                outer_paths={} if existing else {'manifest.json':work/'capture-manifest.json','source/recording.tar':work/'source/recording.tar'}
                raw,outer=_unpack(source,before.st_size,{'manifest.json':(MAX_JSON,None),'source/recording.tar':(MAX_INNER,None)},outer_paths,json_member='manifest.json')
                capture=_capture(raw,recording_id)
                if outer['source/recording.tar']!={'sha256':capture['original_archive']['sha256'],'bytes':capture['original_archive']['bytes']}:
                    raise ValueError('Original inner archive size/SHA differs from sealed source descriptor')
                if existing and _digest(work/'capture-manifest.json',MAX_JSON)!=_sha(raw):raise ValueError('Existing sealed capture metadata conflicts')
                expected={'manifest.json':(MAX_JSON,capture['original_manifest']['sha256'])}
                expected.update({name:(MAX_JSON,checksum) for name,checksum in capture['original_jpegs_sha256'].items()})
                paths={} if existing else {name:work/'original'/name for name in expected}
                with _regular(work/'source/recording.tar',MAX_INNER) as (inner,info):
                    if info.st_size!=capture['original_archive']['bytes'] or hashlib.file_digest(inner,'sha256').hexdigest()!=capture['original_archive']['sha256']:
                        raise ValueError('Retained original inner archive changed')
                    original_raw,members=_unpack(inner,info.st_size,expected,paths,json_member='manifest.json')
                if len(original_raw)!=capture['original_manifest']['bytes']:raise ValueError('Original source manifest length changed')
                original=_original(original_raw,capture)
                pins={'capture-manifest.json':_sha(raw),'source/recording.tar':capture['original_archive']['sha256'],'source-review.json':_sha(review_raw)}
                pins.update({'original/'+name:entry['sha256'] for name,entry in members.items()})
                provenance={'schema':'explorer_so101_source_capture_import_v1','recording_id':recording_id,'project_id':'04',
                    'source_archive_sha256':archive_sha256,'source_review_sha256':approval['review_sha256'],
                    'source_files_sha256':pins,'original_bytes_preserved':True,'training_admission_assessed':False,
                    'physical_success_inferred':False,'automatic_training_started':False}
                provenance_raw=source_bytes(provenance);pins=dict(pins,**{'import-provenance.json':_sha(provenance_raw)})
                source.seek(0)
                identity=lambda value:(value.st_dev,value.st_ino,value.st_size,value.st_mtime_ns,value.st_ctime_ns)
                if (hashlib.file_digest(source,'sha256').hexdigest()!=archive_sha256 or identity(os.fstat(source.fileno()))!=identity(before) or
                        identity(artifact.stat(follow_symlinks=False))!=identity(before)):
                    raise ValueError('Source outer archive changed during import')
                if not existing:
                    atomic_bytes(work/'source-review.json',review_raw);atomic_bytes(work/'import-provenance.json',provenance_raw)
                actual=set()
                for path in work.rglob('*'):
                    name=path.relative_to(work).as_posix()
                    if path.is_symlink():raise ValueError('Imported source symlink is forbidden')
                    if path.is_file():
                        actual.add(name)
                        if name not in pins or _digest(path,MAX_INNER if name=='source/recording.tar' else MAX_JSON)!=pins[name]:
                            raise ValueError('Existing/imported original source bytes conflict')
                    elif not path.is_dir():raise ValueError('Imported source special file forbidden')
                    if len(actual)>len(pins):raise ValueError('Unexpected existing source payload')
                if actual!=set(pins):raise ValueError('Existing/imported source files incomplete')
                if not existing:
                    for folder in [path for path in work.rglob('*') if path.is_dir()]+[work]:
                        descriptor=os.open(folder,os.O_RDONLY|os.O_DIRECTORY)
                        try:os.fsync(descriptor)
                        finally:os.close(descriptor)
                    if os.path.lexists(final):raise ValueError('Source destination appeared before publication')
                    _publish(work,final)
                    descriptor=os.open(destination,os.O_RDONLY|os.O_DIRECTORY)
                    try:os.fsync(descriptor)
                    finally:os.close(descriptor)
            except Exception as error:
                if stage is not None and stage.is_dir():
                    try:atomic_bytes(stage/'.import-failure.json',source_bytes({'error':type(error).__name__,'detail':str(error)[:500],
                        'recording_id':recording_id,'archive_sha256':archive_sha256,'staging_retained':True}))
                    except OSError:pass
                raise
    return {'schema':'explorer_so101_source_capture_import_v1','episode_id':recording_id,'recording_id':recording_id,'project_id':'04',
        'source_format':'so101_source_capture_v1','source_manifest_path':str(final/'original/manifest.json'),
        'source_manifest_sha256':capture['original_manifest']['sha256'],'capture_manifest_path':str(final/'capture-manifest.json'),
        'capture_manifest_sha256':_sha(raw),'original_archive_path':str(final/'source/recording.tar'),
        'original_archive_sha256':capture['original_archive']['sha256'],'source_archive_path':str(artifact.resolve()),
        'source_archive_sha256':archive_sha256,'source_review_path':str(final/'source-review.json'),
        'source_review_sha256':approval['review_sha256'],'source_review_bytes_sha256':_sha(review_raw),'source_approval':approval,
        'provenance_path':str(final/'import-provenance.json'),'provenance_sha256':_sha(provenance_raw),
        'source_original_retained':True,'idempotent_existing':existing,'training_admission_assessed':False,
        'dataset_manifest':None,'physical_success_inferred':False,'automatic_training_started':False}
