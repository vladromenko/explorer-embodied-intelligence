"""ENGINEERING_GLUE: phase evidence from TWO EXISTING authenticated caches.

No camera/device/actuator is opened. Original JPEG/JSON response bytes and source
clocks stay independent. This sidecar never invents native BC action targets;
admission delegates to retained native9D and SO6 episode inspectors.
"""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import hashlib
import io
import json
import os
from pathlib import Path
import queue
import re
import tarfile
import threading
import time
from urllib.parse import urlsplit
from urllib.request import Request,build_opener,HTTPRedirectHandler,ProxyHandler
import uuid

from .cooperative_contract05 import canonical,digest


def sha(raw):return hashlib.sha256(raw).hexdigest()


def sha_file(path):
    value=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda:stream.read(1024**2),b''):value.update(block)
    return value.hexdigest()


def write_original(path,raw):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('xb') as stream:stream.write(raw);stream.flush();os.fsync(stream.fileno())
    descriptor=os.open(path.parent,os.O_RDONLY|os.O_DIRECTORY)
    try:os.fsync(descriptor)
    finally:os.close(descriptor)


def write_projection(path,value):
    path=Path(path);temporary=path.with_suffix(path.suffix+'.pending')
    raw=canonical(value)
    with temporary.open('wb') as stream:stream.write(raw);stream.flush();os.fsync(stream.fileno())
    temporary.replace(path)
    descriptor=os.open(path.parent,os.O_RDONLY|os.O_DIRECTORY)
    try:os.fsync(descriptor)
    finally:os.close(descriptor)


class CachedNodeObservation:
    """GET-only observer HTTP; snapshot telemetry remains exact source bytes."""
    def __init__(self,origin,token,role,*,timeout=1.,opener=None,clock=time.monotonic_ns):
        parsed=urlsplit(origin)
        if (parsed.scheme not in ('http','https') or not parsed.hostname or parsed.username or parsed.password or
                parsed.path not in ('','/') or parsed.query or parsed.fragment or role not in ('giver','receiver') or
                not isinstance(token,str) or len(token)<16):
            raise ValueError('Explicit authenticated observer origin/native role required')
        self.origin,self.token,self.role=origin.rstrip('/'),token,role
        class NoRedirect(HTTPRedirectHandler):
            def redirect_request(self,*args,**kwargs):raise OSError('Authenticated fixed native source must not redirect')
        self.timeout,self.opener,self.clock=timeout,opener or build_opener(ProxyHandler({}),NoRedirect()).open,clock
        self.camera='nano-rgbd' if role=='giver' else 'pi-overview'
    def _get(self,path,limit):
        with self.opener(Request(self.origin+path,headers={'Authorization':'Bearer '+self.token}),timeout=self.timeout) as response:
            raw=response.read(limit+1)
            if len(raw)>limit:raise ValueError('Existing observer response exceeds bound')
            return raw,list(response.headers.raw_items())
    def snapshot(self,after_sequence=0):
        if type(after_sequence) is not int or not 0<=after_sequence<=2**63-1:
            raise ValueError('Bounded original dispatch cursor required')
        begun=self.clock();jpeg,raw_headers=self._get('/cameras/'+self.camera+'/snapshot',2*1024**2)
        camera_received=self.clock()
        headers={key.lower():value for key,value in raw_headers}
        if not jpeg.startswith(b'\xff\xd8') or not jpeg.endswith(b'\xff\xd9'):
            raise ValueError('Existing camera did not return original JPEG bytes')
        frame_id=headers.get('x-frame-id');domain=headers.get('x-clock-domain');tick=headers.get('x-monotonic-ns')
        if not frame_id or not domain or not tick or not re.fullmatch('[0-9]{1,20}',tick):
            raise ValueError('Original cached frame identity/clock not exposed')
        if headers.get('x-frame-sha256') and headers['x-frame-sha256']!=sha(jpeg):
            raise ValueError('Original camera frame identity/SHA mismatch')
        telemetry_path=('/legacy/explorer/retarget/dispatch?after_sequence='+str(after_sequence)
                        if self.role=='giver' else '/station/status')
        source_error=None;telemetry=None;telemetry_headers=[];cursor=after_sequence;gap=None
        try:
            telemetry,telemetry_headers=self._get(telemetry_path,8*1024**2)
            parsed=json.loads(telemetry)
            if not isinstance(parsed,dict):raise ValueError('Original role telemetry object required')
            if self.role=='giver':
                if (parsed.get('schema')!='explorer_native_dispatch_page_v1' or type(parsed.get('next_sequence')) is not int or
                        not after_sequence<=parsed['next_sequence']<=2**63-1):
                    raise ValueError('Actual native dispatch page not exposed')
                cursor=parsed['next_sequence'];gap=parsed.get('gap')
                if gap is not False:source_error='NATIVE_DISPATCH_CURSOR_GAP_OR_UNREPORTED_CONTINUITY'
        except (OSError,ValueError) as error:
            source_error=type(error).__name__+': '+str(error)
        return {'role':self.role,'camera_id':self.camera,'frame_id':frame_id,'jpeg':jpeg,'jpeg_sha256':sha(jpeg),
            'source_monotonic_ns':int(tick),'source_clock_domain':domain,
            'source_boot_id':headers.get('x-boot-id') or domain.rsplit(':',1)[-1],
            'source_boot_id_basis':'original_header_or_observer_clock_domain_suffix',
            'timestamp_basis':headers.get('x-timestamp-basis','UNREPORTED'),
            'exposure_timestamp_verified':False,'camera_response_header_fields':raw_headers,
            'request_started_coordinator_ns':begun,'camera_response_received_coordinator_ns':camera_received,
            'response_received_coordinator_ns':self.clock(),
            'telemetry':telemetry,'telemetry_response_header_fields':telemetry_headers,
            'telemetry_path':telemetry_path,'telemetry_error':source_error,'native_dispatch_gap':gap,'next_sequence':cursor,
            'clock_alignment_to_other_robot_verified':False,'control_POSTs':0}


class CooperativeRecorder:
    def __init__(self,root,sources,*,clock_domain,clock=time.monotonic_ns,poll_s=.5,queue_limit=8):
        if set(sources)!={'giver','receiver'} or not clock_domain:
            raise ValueError('Both existing cached observation sources and coordinator clock required')
        self.root=Path(root);self.root.mkdir(parents=True,exist_ok=True)
        self.sources,self.domain,self.clock,self.poll_s=sources,clock_domain,clock,poll_s
        self.lock=threading.RLock();self.queue=queue.Queue(maxsize=queue_limit)
        self.wake,self.closed,self.done=threading.Event(),threading.Event(),threading.Event()
        self.done.set()
        self.state='IDLE';self.ident=None;self.inflight=False;self.pending_finish=None
        self.pool=ThreadPoolExecutor(max_workers=2,thread_name_prefix='cooperative-cached-GET')
        for path in self.root.glob('*/manifest.json'):
            try:
                previous=json.loads(path.read_bytes())
                if previous.get('state') in ('RECORDING','SEALING') or (
                        previous.get('state')=='SEALED' and not path.with_name('archive-info.json').exists()):
                    backup=path.with_name('manifest.interrupted-original.json')
                    if not backup.exists():write_original(backup,path.read_bytes())
                    write_projection(path,{**previous,'state':'INTERRUPTED','analysis_only':True,
                        'block_reasons':['COORDINATOR_RESTART_NO_AUTOMATIC_CAPTURE_OR_MOTION_RESUME']})
            except (OSError,ValueError):pass
        self.collector=threading.Thread(target=self._collect,name='cooperative-two-cache-collector',daemon=True)
        self.writer=threading.Thread(target=self._write,name='cooperative-file-writer',daemon=True)
        self.collector.start();self.writer.start()
    def _save(self,name,raw):
        if name in self.files:
            if self.files[name]['sha256']!=sha(raw):raise ValueError('Original source key changed')
            return
        write_original(self.folder/name,raw);self.files[name]={'sha256':sha(raw),'bytes':len(raw)}
    def start(self,context,native_source_start=None):
        with self.lock:
            if self.closed.is_set() or self.state not in ('IDLE','SEALED') or self.inflight:
                raise ValueError('Finish existing cooperative recording first')
            self.ident=uuid.uuid4().hex;self.folder=self.root/self.ident;self.folder.mkdir(mode=0o700)
            self.context=deepcopy(context);self.state='RECORDING';self.started=self.clock();self.finish_ns=None
            self.files={};self.samples=[];self.phase_events=[];self.phase='PREPARE';self.serial=0
            self.cursors={'giver':0,'receiver':0};self.frames={'giver':set(),'receiver':set()}
            self.blockers=set();self.drops=0;self.native_sources=deepcopy(native_source_start or {})
            self.pending_finish=None;self.done.clear()
            self._save('sources/context.json',canonical(context))
            self._save('sources/native-producer-start.json',canonical(self.native_sources))
            write_projection(self.folder/'manifest.json',self._manifest())
        self.wake.set();return self.status()
    def stage(self,phase_id,source_annotations=None):
        if not isinstance(phase_id,str) or not phase_id or len(phase_id)>100:
            raise ValueError('Bounded explicit phase ID required')
        with self.lock:
            if self.state!='RECORDING':raise ValueError('Recording is not active')
            self.phase=phase_id
            event={'phase_id':phase_id,'received_coordinator_ns':self.clock(),'clock_domain':self.domain,
                'source_annotation_receipts':deepcopy(source_annotations or {}),
                'coordinator_phase_is_not_source_exposure_label':True}
            self.phase_events.append(event)
            self._save('sources/phase-'+str(len(self.phase_events))+'.json',canonical(event))
            return deepcopy(event)
    def acknowledge_native_source(self,recording_id,role,reference):
        """Append a known DATA ACK sidecar; the original start bytes stay intact."""
        with self.lock:
            if (self.state!='RECORDING' or self.ident!=recording_id or role not in ('giver','receiver') or
                    reference.get('role')!=role or
                    (reference.get('known_capture_intent') or {}).get('session_id')!=self.context['session_id'] or
                    reference.get('known_capture_intent')!=(self.context.get('native_capture_start_intents') or {}).get(role) or
                    reference.get('metadata_only_no_control_started') is not True):
                raise ValueError('Current cooperative recording and known DATA source intent required')
            previous=self.native_sources.get(role)
            if previous:
                if previous.get('recording_id')!=reference.get('recording_id'):raise ValueError('Cannot replace another original DATA source')
                if (not previous.get('source_capture_binding_error') and
                        previous.get('known_capture_intent')==reference.get('known_capture_intent')):
                    return self.status()
            self._save('sources/native-producer-reconciled-'+role+'.json',canonical(reference))
            self.native_sources[role]=deepcopy(reference)
            write_projection(self.folder/'manifest.json',self._manifest())
            return self.status()
    def finish(self,outcome,native_source_finish=None):
        with self.lock:
            if self.state!='RECORDING':raise ValueError('Recording changed or finish already pending')
            self.state='SEALING';self.finish_ns=self.clock()
            self.pending_finish={'outcome':deepcopy(outcome),'native_source_finish':deepcopy(native_source_finish or {})}
        self.wake.set();return self.status()
    def status(self):
        with self.lock:
            return {'schema':'explorer_cooperative_recording_v1','recording_id':self.ident,'state':self.state,
                    'fetch_inflight':self.inflight,'camera_capture_owners_added':0,
                    'sample_packets':len(getattr(self,'samples',[])),
                    'unique_camera_frames':{role:len(ids) for role,ids in getattr(self,'frames',{}).items()},
                    'block_reasons':sorted(getattr(self,'blockers',set())),'physical_commands':0}
    def _manifest(self):
        return {'schema':'explorer_cooperative_raw_recording_v1','recording_id':self.ident,
            'state':self.state,'context':deepcopy(self.context),'coordinator_clock_domain':self.domain,
            'started_coordinator_ns':self.started,'finish_coordinator_ns':self.finish_ns,
            'source_files':deepcopy(self.files),'samples':deepcopy(self.samples),'phase_events':deepcopy(self.phase_events),
            'native_role_sources':deepcopy(self.native_sources),'dropped_packets':self.drops,
            'analysis_only':True,'bc_targets_constructed':False,'admission_requires_native_role_imports':True,
            'block_reasons':sorted(self.blockers),'clock_domains_equated':False,'camera_capture_owners_added':0,
            'outcome':deepcopy(self.pending_finish.get('outcome')) if self.pending_finish else None,
            'native_source_finish':deepcopy(self.pending_finish.get('native_source_finish')) if self.pending_finish else None}
    def _collect(self):
        while not self.closed.is_set():
            self.wake.wait(self.poll_s);self.wake.clear()
            with self.lock:
                active=self.state=='RECORDING' and not self.inflight
                if active:
                    self.inflight=True;ident=self.ident;phase=self.phase;requested=self.clock();cursors=dict(self.cursors)
            if active:
                futures={role:self.pool.submit(source.snapshot,cursors[role]) for role,source in self.sources.items()}
                packets=[]
                for role,future in futures.items():
                    try:packet=future.result();packets.append({'role':role,'packet':packet})
                    except Exception as error:packets.append({'role':role,'error':type(error).__name__+': '+str(error)})
                with self.lock:
                    self.serial+=1
                    item={'recording_id':ident,'serial':self.serial,'phase_at_sampling_request':phase,
                        'sampling_requested_coordinator_ns':requested,'responses_received_coordinator_ns':self.clock(),
                        'packets':packets,'late_after_finish':self.finish_ns is not None and self.clock()>self.finish_ns}
                    try:self.queue.put_nowait(item)
                    except queue.Full:self.drops+=1;self.blockers.add('COOPERATIVE_SIDECAR_QUEUE_DROPPED')
                    self.inflight=False
    def _write(self):
        while not self.closed.is_set() or not self.queue.empty():
            try:item=self.queue.get(timeout=.05)
            except queue.Empty:item=None
            if item is not None:
                try:
                    with self.lock:
                        if item['recording_id']!=self.ident:raise ValueError('Late packet from another recording')
                        row={key:value for key,value in item.items() if key!='packets'};row['roles']={}
                        for entry in item['packets']:
                            role=entry['role']
                            if entry.get('error'):
                                self.blockers.add(role.upper()+'_CACHE_UNAVAILABLE');row['roles'][role]={'error':entry['error']}
                            else:
                                packet=entry['packet'];jpeg=packet['jpeg'];name='sources/jpeg-'+sha(jpeg)+'.jpg';self._save(name,jpeg)
                                if packet.get('role')!=role or packet.get('camera_id')!=('nano-rgbd' if role=='giver' else 'pi-overview'):
                                    self.blockers.add(role.upper()+'_SOURCE_IDENTITY_MISMATCH')
                                if packet.get('telemetry_error'):self.blockers.add(role.upper()+'_NATIVE_TELEMETRY_INVALID')
                                metadata={key:value for key,value in packet.items() if key not in ('jpeg','telemetry')}
                                metadata['jpeg_source_file']=name
                                telemetry=packet.get('telemetry')
                                if telemetry is not None:
                                    target='sources/'+role+'-telemetry-'+str(item['serial'])+'.json';self._save(target,telemetry)
                                    metadata['telemetry_source_file']=target
                                    self.cursors[role]=packet.get('next_sequence',self.cursors[role])
                                else:self.blockers.add(role.upper()+'_NATIVE_TELEMETRY_UNAVAILABLE')
                                self.frames[role].add(packet['frame_id']);row['roles'][role]=metadata
                        self.samples.append(row)
                        self._save('sources/sample-'+str(item['serial'])+'.json',canonical(row))
                except Exception as error:
                    with self.lock:self.blockers.add('COOPERATIVE_SOURCE_WRITE_FAILED:'+type(error).__name__)
                finally:self.queue.task_done()
            with self.lock:
                if self.state=='SEALING' and not self.inflight and self.queue.empty():
                    try:
                        self._save('sources/outcome.json',canonical(self.pending_finish))
                        if not self.samples:self.blockers.add('NO_CACHED_SOURCE_SAMPLES')
                        projection={**self._manifest(),'state':'SEALED'}
                        write_projection(self.folder/'manifest.json',projection)
                        archive=self.folder/'recording.tar';temporary=self.folder/'recording.tar.pending'
                        with tarfile.open(temporary,'w') as tar:
                            for path in sorted(self.folder.rglob('*')):
                                if path.is_file() and path.name not in ('recording.tar','recording.tar.pending','archive-info.json'):
                                    tar.add(path,arcname=str(path.relative_to(self.folder)),recursive=False)
                        with temporary.open('rb') as stream:os.fsync(stream.fileno())
                        temporary.replace(archive)
                        write_projection(self.folder/'archive-info.json',{'archive_sha256':sha_file(archive),
                            'archive_bytes':archive.stat().st_size,'recording_id':self.ident})
                        self.state='SEALED'
                    except Exception as error:
                        self.state='FAILED';self.blockers.add('ARCHIVE_WRITE_FAILED:'+type(error).__name__)
                        write_projection(self.folder/'manifest.json',self._manifest())
                    self.done.set()
    def close(self):
        with self.lock:
            if self.state=='RECORDING':self.finish({'status':'interrupted','method':'observer_shutdown_not_operator_success'})
        self.wake.set();self.done.wait(5)
        with self.lock:
            if self.state=='SEALING':
                self.state='INTERRUPTED';self.blockers.add('GET_SOURCE_DID_NOT_DRAIN_BEFORE_SHUTDOWN')
                write_projection(self.folder/'manifest.json',self._manifest())
        self.closed.set();self.wake.set();self.collector.join(timeout=2);self.writer.join(timeout=2)
        self.pool.shutdown(wait=False,cancel_futures=True)


def verify_recording(folder):
    folder=Path(folder);raw=(folder/'manifest.json').read_bytes();manifest=json.loads(raw)
    if manifest.get('schema')!='explorer_cooperative_raw_recording_v1' or manifest.get('state')!='SEALED':
        raise ValueError('Completed source cooperative sidecar required')
    for name,metadata in manifest.get('source_files',{}).items():
        path=(folder/name).resolve()
        if (not isinstance(name,str) or Path(name).is_absolute() or not path.is_relative_to(folder.resolve()) or
                not path.is_file() or sha_file(path)!=metadata.get('sha256') or path.stat().st_size!=metadata.get('bytes')):
            raise ValueError('Original cooperative source payload changed or escaped managed recording')
    return manifest


def import_cooperative_recording(archive,destination,*,expected_sha256,max_bytes=2*1024**3,max_files=100000):
    """Retain original TAR and source bytes; no native targets or expert labels."""
    archive=Path(archive);destination=Path(destination)
    if sha_file(archive)!=expected_sha256:raise ValueError('Original cooperative archive SHA mismatch')
    destination.mkdir(parents=True,exist_ok=True);source=destination/'source';source.mkdir(exist_ok=True)
    total=0;seen=set()
    with tarfile.open(archive,'r:') as tar:
        for member in tar:
            path=(source/member.name).resolve()
            if (member.name in seen or not member.isfile() or Path(member.name).is_absolute() or
                    not path.is_relative_to(source.resolve()) or '..' in Path(member.name).parts):
                raise ValueError('Unsafe or duplicate original cooperative archive entry')
            total+=member.size
            if total>max_bytes or member.size>16*1024**2 or len(seen)>=max_files:raise ValueError('Cooperative source archive exceeds import budget')
            seen.add(member.name);raw=tar.extractfile(member).read()
            if path.exists():
                if path.read_bytes()!=raw:raise ValueError('Imported original cooperative payload changed')
            else:write_original(path,raw)
    manifest=verify_recording(source);original=destination/'original-recording.tar'
    if original.exists():
        if sha_file(original)!=expected_sha256:raise ValueError('Retained original TAR conflict')
    else:
        import shutil
        with archive.open('rb') as incoming,original.open('xb') as outgoing:
            shutil.copyfileobj(incoming,outgoing,1024**2);outgoing.flush();os.fsync(outgoing.fileno())
    result={'schema':'explorer_cooperative_import_v1','recording_id':manifest['recording_id'],
        'cooperative_manifest':str(source/'manifest.json'),'source_archive_sha256':expected_sha256,
        'native_role_sources':manifest.get('native_role_sources',{}),'admitted_for_training':False,
        'admission_scope':'raw_two_robot_sidecar_native_role_targets_require_separate_import_and_review',
        'original_source_bytes_retained':True,'dataset_action_spaces_merged':False}
    write_projection(destination/'import-result.json',result)
    return result
