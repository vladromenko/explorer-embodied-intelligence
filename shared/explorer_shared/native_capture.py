"""Same Nano canonical producer for native keyboard02 and leader03.

Consumes original native HTTP receipt bytes and the existing camera history.
Its independent writer owns files only, never cameras, servo ports or settings.
Missing source facts remain analysis-only; finish cannot create expert labels.
"""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import queue
import re
import shutil
import tarfile
import tempfile
import threading
import time
import uuid

from .contracts import ACTION_NAMES,ACTION_UNITS
from .embodied_episode import (SCHEMA,ACTION_CONTRACT_ID,COMMAND_ESTIMATE_PROFILE,
                               COMMAND_ESTIMATE_REQUIRED,REQUIRED_CHANNELS,inspect_embodied_episode)


def _bytes(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()


def _sha(value):
    return hashlib.sha256(value).hexdigest()


def _write(path,content):
    temporary=path.with_suffix(path.suffix+'.tmp')
    with temporary.open('wb') as stream:
        stream.write(content);stream.flush();os.fsync(stream.fileno())
    os.replace(temporary,path)
    descriptor=os.open(path.parent,os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _record(value,stamp,source_id,*,available=True,reason=None):
    result={'present':True if available else None,'available':available,'value':value if available else None,
            'mask':True if available else False,'stamp':deepcopy(stamp),'source_id':source_id}
    if not available:
        result['unavailable_reason']=reason or 'NOT_EXPOSED_BY_NATIVE_CAPTURE_V1'
    return result


class NativeCaptureWriter:
    """Metadata-only start; original source packets and images are preserved."""
    def __init__(self,root,camera,*,clock_domain,boot_id,clock=time.monotonic_ns,emit=None,queue_limit=4,
                 admission_byte_budget=128*1024**2,max_sample_metadata=20000):
        self.root,self.camera=Path(root),camera
        self.root.mkdir(parents=True,exist_ok=True,mode=0o700)
        self.domain,self.boot,self.clock,self.emit=clock_domain,boot_id,clock,emit
        self.admission_byte_budget,self.max_sample_metadata=admission_byte_budget,max_sample_metadata
        self.lock=threading.RLock();self.items=queue.Queue(maxsize=queue_limit)
        self.wake,self.closed,self.done=threading.Event(),threading.Event(),threading.Event()
        self.state='IDLE';self.active=None;self.pending_finish=None;self.error=None
        for manifest in self.root.glob('*/manifest.json'):
            try:
                raw=manifest.read_bytes();previous=json.loads(raw)
                if previous.get('state') in ('RECORDING','SEALING'):
                    _write(manifest.with_name('manifest.interrupted-original.json'),raw)
                    _write(manifest,_bytes({**previous,'state':'interrupted','analysis_only':True,
                        'block_reasons':['OBSERVER_RESTART_DURING_RECORDING']}))
            except (OSError,ValueError):
                self.error='INTERRUPTED_CAPTURE_MANIFEST_UNREADABLE'
        self.thread=threading.Thread(target=self._work,name='native-canonical-file-writer',daemon=True)
        self.thread.start()

    def _save(self,name,content):
        path=self.folder/name;path.parent.mkdir(parents=True,exist_ok=True)
        _write(path,content);self.payloads[name]={'sha256':_sha(content),'bytes':len(content)}

    def start(self,skill_context,initial_source_packet):
        if not isinstance(skill_context,dict) or not isinstance(initial_source_packet,bytes):
            raise ValueError('Validated skill metadata and exact native source packet bytes required')
        page=json.loads(initial_source_packet)
        if page.get('boot_id')!=self.boot or page.get('clock_domain')!=self.domain:
            raise ValueError('Native and camera producer must share verified original clock/boot')
        cursor=page.get('available_latest_sequence')
        if type(cursor) is not int or cursor<0:
            raise ValueError('Original native recording boundary cursor required')
        with self.lock:
            if self.state not in ('IDLE','SEALED'):
                raise RuntimeError('Finish current native recording first')
            self.active=uuid.uuid4().hex;self.folder=self.root/self.active
            self.folder.mkdir(mode=0o700);self.payloads={};self.samples=[];self.sequences=set()
            self.context=deepcopy(skill_context);self.cursor=cursor;self.packet_number=0
            self.initial_page=deepcopy(page);self.dropped=0;self.blockers=set();self.phase_events=[]
            self.state,self.error,self.pending_finish='RECORDING',None,None;self.done.clear()
            self.started_ns=self.clock()
            self._save('sources/initial-native-page.json',initial_source_packet)
            self._save('sources/skill-context.json',_bytes(skill_context))
            if skill_context.get('phase_id'):
                self._stage_locked(skill_context['phase_id'],'explicit_record_start',None)
            _write(self.folder/'manifest.json',_bytes(self.status()))
            return self.status()

    def _stage_locked(self,phase_id,reason,recovery_of):
        if not isinstance(phase_id,str) or not phase_id or not isinstance(reason,str) or not reason:
            raise ValueError('Explicit operator phase and transition reason required')
        event={'phase_id':phase_id,'transition_reason':reason,'recovery_of':recovery_of,
               'stamp':{'clock_domain':self.domain,'boot_id':self.boot,'monotonic_ns':self.clock()},
               'timestamp_basis':'annotation_received_on_native_capture_host'}
        occurrence=1+sum(previous['phase_id']==phase_id for previous in self.phase_events)
        event['occurrence_id']=phase_id if occurrence==1 else phase_id+'#'+str(occurrence)
        self.phase_events.append(event)
        return deepcopy(event)

    def stage(self,recording_id,phase_id,*,reason='explicit_operator_annotation',recovery_of=None):
        with self.lock:
            if recording_id!=self.active or self.state!='RECORDING':
                raise ValueError('Recording changed or is sealing')
            return self._stage_locked(phase_id,reason,recovery_of)

    def submit(self,recording_id,source_packet):
        if not isinstance(source_packet,bytes) or len(source_packet)>8*1024**2:
            raise ValueError('Bounded exact native source bytes required')
        page=json.loads(source_packet)
        # Snapshot immutable existing JPEG bytes before queueing. File I/O and
        # fsync happen in the independent writer, never the native servo loop.
        frames={}
        if page.get('boot_id')==self.boot and page.get('clock_domain')==self.domain:
            for row in page.get('receipts',[]):
                if row.get('kind')=='arm_host_publish':
                    tick=row.get('stamp',{}).get('monotonic_ns')
                    frame=self.camera.before(tick) if self.camera is not None else None
                    if frame is not None:
                        frames[row['receipt_id']]={'jpeg':frame.jpeg,
                            'metadata':{**frame.metadata(),'camera_id':self.camera.camera_id}}
        with self.lock:
            if recording_id!=self.active or self.state!='RECORDING':
                return False
            try:
                self.items.put_nowait((recording_id,source_packet,frames))
            except queue.Full:
                self.dropped+=1;self.blockers.add('SOURCE_PACKET_QUEUE_DROP');return False
        self.wake.set();return True

    def finish(self,recording_id,outcome_source_bytes=None):
        with self.lock:
            if recording_id!=self.active or self.state!='RECORDING':
                raise ValueError('Recording changed or is already sealing')
            if outcome_source_bytes is not None and not isinstance(outcome_source_bytes,bytes):
                raise ValueError('Exact authenticated outcome evidence bytes required')
            self.state='SEALING';self.pending_finish=outcome_source_bytes or b'{}'
        self.wake.set();return self.status()

    def status(self):
        with self.lock:
            return {'schema':'explorer_native_capture_status_v1','recording_id':self.active,'state':self.state,
                    'cursor':getattr(self,'cursor',None),'samples_written':len(getattr(self,'samples',[])),
                    'packets_dropped':getattr(self,'dropped',0),'queued':self.items.qsize(),'error':self.error,
                    'embodied_manifest':str(self.folder/'manifest.json') if hasattr(self,'folder') and self.state=='SEALED' else None,
                    'motor_commands':0,'camera_owners_created':0,'controller_settings_written':False}

    def close(self):
        self.closed.set();self.wake.set();self.thread.join(timeout=2)

    def archive(self,recording_id):
        if not isinstance(recording_id,str) or not re.fullmatch('[0-9a-f]{32}',recording_id):
            raise ValueError('Original server recording identity required')
        return archive_native_capture(self.root/recording_id)

    def _consume(self,raw,frames):
        page=json.loads(raw);self.packet_number+=1
        name='sources/native-page-'+str(self.packet_number).zfill(6)+'.json'
        self._save(name,raw)
        self._save(name.removesuffix('.json')+'-camera.json',_bytes({ident:{
            'metadata':image['metadata'],'jpeg_sha256':_sha(image['jpeg']),
            'source_file':'frames/'+_sha(image['jpeg'])+'.jpg'} for ident,image in frames.items()}))
        if page.get('boot_id')!=self.boot or page.get('clock_domain')!=self.domain:
            self.blockers.add('NATIVE_CLOCK_OR_BOOT_CHANGED');return
        if page.get('gap') or page.get('capture_failures',0)>self.initial_page.get('capture_failures',0):
            self.blockers.add('NATIVE_DISPATCH_GAP_OR_CAPTURE_FAILURE')
        for row in page.get('receipts',[]):
            sequence=row.get('sequence')
            if type(sequence) is int and sequence>self.cursor:
                if sequence!=self.cursor+1:
                    self.blockers.add('NATIVE_DISPATCH_SEQUENCE_GAP')
                self.cursor=sequence
                if row.get('kind')=='arm_host_publish':
                    self._sample(row,frames.get(row.get('receipt_id')),name)
            elif type(sequence) is not int:
                self.blockers.add('INVALID_NATIVE_RECEIPT_SEQUENCE')

    def _sample(self,row,frame,source_file):
        if (row.get('schema')!='explorer_native_host_dispatch_v1' or
                row.get('native_host_publication_returned') is not True or
                row.get('command9_semantics')!='current_native_command_snapshot_after_this_publication'):
            self.blockers.add('NATIVE_HOST_PUBLICATION_NOT_QUALIFIED')
        stamp=row.get('stamp',{})
        before={**stamp,'monotonic_ns':row.get('call_started_monotonic_ns')}
        channels={name:_record(None,before,'native_capture_capability_v1',available=False) for name in REQUIRED_CHANNELS}
        channels['clocks']=_record({'clock_domain':self.domain,'boot_id':self.boot},before,'native_kernel_clock')
        prior=row.get('preceding_command9')
        channels['preceding_command9']=_record({**deepcopy(prior),'names':list(ACTION_NAMES)} if prior else None,
            before,'original_factory_ruckig',available=prior is not None,reason='PRIOR_COMMAND9_NOT_RECORDED')
        if prior:
            channels['preceding_command9']['mask']=[True]*9
        if frame is not None:
            metadata=frame['metadata'];path='frames/'+_sha(frame['jpeg'])+'.jpg'
            if path not in self.payloads:
                self._save(path,frame['jpeg'])
            camera_stamp={'clock_domain':self.domain,'boot_id':self.boot,'monotonic_ns':metadata['monotonic_ns']}
            channels['camera_rgb']=_record({'source_file':path,'sha256':_sha(frame['jpeg']),
                'camera_id':metadata['camera_id'],'frame_id':metadata['frame_id'],'encoding':'jpeg',
                'width':metadata['width'],'height':metadata['height'],'timestamp_basis':metadata['timestamp_basis']},
                camera_stamp,'existing_single_owner_camera_history')
        else:
            channels['camera_rgb']['unavailable_reason']='NO_ORIGINAL_CAMERA_FRAME_BEFORE_DISPATCH'
        estimate=row.get('original_payload',{}).get('q_estimated_deg')
        if estimate is not None:
            channels['joint_command']=_record({'values':deepcopy(estimate),'units':'deg',
                'state_source':'command_trajectory_estimate'},before,'original_factory_ruckig')
        action={'values':deepcopy(row.get('command9')),'mask':deepcopy(row.get('command9_mask')),
                'stage':'native_dispatch','source_id':row.get('source_id'),'command_id':row.get('receipt_id'),
                'stamp':deepcopy(stamp),'delivery':{'level':'native_dispatch','observer_kind':'native_dispatch_callback',
                    'receipt_id':row.get('receipt_id'),'command_id':row.get('receipt_id'),'stamp':deepcopy(stamp),
                    'native_host_publication_returned':row.get('native_host_publication_returned'),
                    'hardware_dispatch_verified':False,'scope':'native_host_publication_not_MCU_or_attainment'},
                'source_projection':{'source_file':source_file,'source_receipt_id':row.get('receipt_id'),
                    'method':'lossless_native_command9_field_projection'},
                'native_component_receipt_ids':deepcopy(row.get('component_receipt_ids')),
                'components_published_in_event':deepcopy(row.get('components_published_in_this_event'))}
        if len(self.samples)<self.max_sample_metadata:
            self.samples.append({'sample_id':row.get('receipt_id'),'action':action,'channels':channels,
                                 'source_intent_binding':deepcopy(row.get('source_intent_binding')),
                                 'native_wire_input_source':row.get('original_payload',{}).get('input_source')})
        else:
            self.blockers.add('OFFLINE_REBUILD_REQUIRED_METADATA_BUDGET')

    def _seal(self,outcome_bytes):
        self._save('sources/outcome.json',outcome_bytes)
        outcome=json.loads(outcome_bytes)
        if outcome.get('recording_id')!=self.active:
            outcome={}
        context=self.context; reference=deepcopy(self.initial_page.get('canonical_reference') or {})
        adapters=[]
        for sample in self.samples:
            source=(sample.get('source_intent_binding') or {}).get('source') or {}
            adapter=source.get('input_adapter')
            wire=sample.get('native_wire_input_source')
            if isinstance(adapter,dict):
                adapters.append(deepcopy(adapter))
            elif wire in ('keyboard','gamepad'):
                adapters.append({'id':'native_'+wire,'version':'explorer-manual-v2','input_mode':wire})
            if self.context.get('input_mode')=='so101_leader' and (
                    source.get('source_evidence',{}).get('state_source')!='measured_leader' or
                    not isinstance(source.get('source_evidence',{}).get('calibration_sha256'),str) or
                    len(source['source_evidence']['calibration_sha256'])!=64):
                self.blockers.add('MEASURED_LEADER_SOURCE_BINDING_MISSING')
        adapter=adapters[0] if adapters and all(item==adapters[0] for item in adapters) else {}
        adapter['client_declared_input_mode']=context.get('input_mode')
        complete_signal=bool(self.samples) and bool(reference) and all(
            sample['action'].get('mask')==[True]*9 and
            sample['channels']['camera_rgb']['available'] is True and
            sample['channels']['preceding_command9']['available'] is True for sample in self.samples)
        if self.dropped or self.blockers or not complete_signal:
            quality=False
        else:
            quality=True
        skill={'id':context.get('skill_id'),'version':context.get('skill_version'),
               'variant_id':context.get('variant_id'),'session_id':context.get('session_id')}
        facts={'schema':SCHEMA,'episode_id':self.active,'run_id':self.active,'project_id':context.get('project_id'),
               'source_kind':'physical','skill':skill,'episode_scope':context.get('episode_scope'),
               'action_contract':{'id':ACTION_CONTRACT_ID,'names':list(ACTION_NAMES),'units':list(ACTION_UNITS),'simultaneous':True},
               'admission_profile':{'id':COMMAND_ESTIMATE_PROFILE,'required_channels':list(COMMAND_ESTIMATE_REQUIRED)},
               'state_estimate_reference':reference,
               'clock_domains':{self.domain:{'clock_type':'monotonic','boot_id':self.boot}},
               'causal_protocol':{'max_observation_age_ns':int(self.camera.stale_s*1e9) if self.camera else 0},
               'recording':{'status':'completed','source_quality_usable':quality,
                            'operator_outcome':outcome.get('operator_outcome'),'capture_blockers':sorted(self.blockers),
                            'source_packets_dropped':self.dropped},'samples':self.samples,'phases':[],
               'outcomes':deepcopy(outcome.get('outcomes',{})),
               'split':deepcopy(context.get('split',{})),
               'model_input':{'implementation_id':'official_ACT_RGB_command_estimate9_contract',
                   'channels':['camera_rgb','preceding_command9'],'action_contract_id':ACTION_CONTRACT_ID,
                   'output_names':list(ACTION_NAMES),'usage':'declared_plan'},
               'input_adapter':adapter}
        facts['split']['episode_id']=self.active
        facts['identity']={key:deepcopy(facts[key]) for key in
            ('episode_id','run_id','project_id','source_kind','skill','episode_scope','action_contract')}
        events=deepcopy(self.phase_events)
        self._save('sources/phase-events.json',_bytes(events))
        for index,sample in enumerate(self.samples):
            tick=sample['action']['stamp'].get('monotonic_ns')
            eligible=[event for event in events if type(tick) is int and event['stamp']['monotonic_ns']<=tick]
            if eligible:
                event=eligible[-1]
                if not facts['phases'] or facts['phases'][-1]['id']!=event['occurrence_id']:
                    facts['phases'].append({'id':event['occurrence_id'],'semantic_phase_id':event['phase_id'],
                        'start_sample':index,'end_sample':index,
                        'transition_reason':event['transition_reason'],'recovery_of':event['recovery_of'],
                        'annotation_stamp':event['stamp']})
                else:
                    facts['phases'][-1]['end_sample']=index
        raw_facts=_bytes(facts);self._save('sources/canonical-facts.json',raw_facts)
        digest=_sha(raw_facts)
        envelope=deepcopy(facts)
        def bind(record,pointer):
            record['provenance']={'source_file':'sources/canonical-facts.json','sha256':digest,'json_pointer':pointer}
        for key in ('identity','admission_profile','state_estimate_reference','causal_protocol','recording','split','model_input','input_adapter'):
            bind(envelope[key],'/'+key)
        bind(envelope['clock_domains'][self.domain],'/clock_domains/'+self.domain.replace('~','~0').replace('/','~1'))
        for index,sample in enumerate(envelope['samples']):
            prefix='/samples/'+str(index)
            bind(sample['action'],prefix+'/action');bind(sample['action']['delivery'],prefix+'/action/delivery')
            for name,record in sample['channels'].items():
                bind(record,prefix+'/channels/'+name)
        for index,phase in enumerate(envelope['phases']):
            bind(phase,'/phases/'+str(index))
        outcomes=envelope['outcomes']
        if isinstance(outcomes.get('end_to_end'),dict):
            bind(outcomes['end_to_end'],'/outcomes/end_to_end')
        for name,record in outcomes.get('per_phase',{}).items():
            bind(record,'/outcomes/per_phase/'+name.replace('~','~0').replace('/','~1'))
        envelope['source_files_sha256']={name:info['sha256'] for name,info in self.payloads.items()}
        if sum(info['bytes'] for info in self.payloads.values())<=self.admission_byte_budget:
            source_payloads={name:(self.folder/name).read_bytes() for name in self.payloads}
            admission=inspect_embodied_episode(envelope,source_payloads)
        else:
            admission=inspect_embodied_episode(None)
            admission['missing_facts'].append('OFFLINE_ADMISSION_REQUIRED_PAYLOAD_BYTE_BUDGET')
        manifest={'schema':'explorer_native_embodied_capture_manifest_v1','recording_id':self.active,
                  'project_id':context.get('project_id'),'state':'completed','source_episode_metadata':{'id':self.active},
                  'skill_context':context,'embodied_episode':envelope,
                  'embodied_source_files_sha256':deepcopy(envelope['source_files_sha256']),
                  'admission':admission,'analysis_only':not admission['admitted_for_dataset'],
                  'source_quality_usable':quality,'physical_success_inferred':False,
                  'motor_commands':0,'controller_settings_written':False}
        _write(self.folder/'manifest.json',_bytes(manifest))
        if self.emit:
            try:
                self.emit({'kind':'native.embodied_recording.completed','recording_id':self.active,
                           'embodied_manifest':str(self.folder/'manifest.json'),
                           'manifest_sha256':_sha((self.folder/'manifest.json').read_bytes()),
                           'admitted_for_dataset':admission['admitted_for_dataset']})
            except Exception:
                self.error='EVENT_DELIVERY_FAILED_SOURCE_MANIFEST_RETAINED'
        with self.lock:
            self.state='SEALED';self.pending_finish=None;self.done.set()

    def _work(self):
        while not self.closed.is_set():
            self.wake.wait(.2);self.wake.clear()
            try:
                while not self.items.empty():
                    ident,raw,frames=self.items.get_nowait()
                    if ident==self.active:
                        self._consume(raw,frames)
                with self.lock:
                    finish=self.pending_finish if self.state=='SEALING' and self.items.empty() else None
                if finish is not None:
                    self._seal(finish)
            except Exception as error:
                with self.lock:
                    self.error=type(error).__name__;self.blockers.add('WRITER_ERROR');self.state='ERROR'
                    self.pending_finish=None
                    try:
                        _write(self.folder/'manifest.json',_bytes({**self.status(),'analysis_only':True,
                            'block_reasons':sorted(self.blockers)}))
                    except OSError:
                        pass
                    self.done.set()


def _relative_file(root,name):
    if not isinstance(name,str) or not name or '\\' in name or any(part in ('','.','..') for part in name.split('/')):
        raise ValueError('Unsafe source member name')
    path=(root/name).resolve()
    if not path.is_relative_to(root.resolve()) or not path.is_file() or (root/name).is_symlink():
        raise ValueError('Source file escaped the original recording')
    return path


def archive_native_capture(folder):
    """Uncompressed verified raw archive, after sealing and outside control."""
    folder=Path(folder)
    manifest=json.loads((folder/'manifest.json').read_bytes())
    if manifest.get('schema')!='explorer_native_embodied_capture_manifest_v1' or manifest.get('state')!='completed':
        raise ValueError('Only a sealed original native capture can be exported')
    sources=manifest.get('embodied_source_files_sha256',{})
    files=[]
    for name,digest in sources.items():
        path=_relative_file(folder,name)
        with path.open('rb') as stream:
            if hashlib.file_digest(stream,'sha256').hexdigest()!=digest:
                raise ValueError('Original capture source changed before archive')
        files.append((name,path))
    target=folder/'recording.tar';temporary=folder/'recording.tar.tmp'
    with temporary.open('wb') as output:
        with tarfile.open(fileobj=output,mode='w') as archive:
            archive.add(folder/'manifest.json',arcname='manifest.json',recursive=False)
            for name,path in sorted(files):
                archive.add(path,arcname=name,recursive=False)
        output.flush();os.fsync(output.fileno())
    os.replace(temporary,target)
    with target.open('rb') as source:
        digest=hashlib.file_digest(source,'sha256').hexdigest()
    _write(folder/'archive-info.json',_bytes({'archive_sha256':digest,'bytes':target.stat().st_size,
        'recording_id':manifest['recording_id'],'source_originals_unchanged':True}))
    return target


def import_native_capture(archive_path,expected_sha256,destination,*,admission_byte_budget=256*1024**2):
    """Verified NX import; retains exact original manifest/payloads, no motors.

    A canonical source manifest is not an invented legacy expert NPZ. Existing
    raw retarget archives use their original independent analysis-only importer.
    """
    archive_path,destination=Path(archive_path),Path(destination)
    with archive_path.open('rb') as source:
        if hashlib.file_digest(source,'sha256').hexdigest()!=expected_sha256:
            raise ValueError('Native source archive SHA mismatch')
    destination.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.native-import-',dir=destination) as temporary:
        staging=Path(temporary)
        seen=set();total=0
        with tarfile.open(archive_path,'r:') as archive:
            for member in archive:
                name=member.name
                if (not member.isfile() or name.startswith('/') or '\\' in name or
                        any(part in ('','.','..') for part in name.split('/')) or name in seen or
                        member.size>64*1024**2):
                    raise ValueError('Unsafe, repeated or oversized source archive member')
                total+=member.size;seen.add(name)
                if total>8*1024**3 or len(seen)>200000:
                    raise ValueError('Source archive exceeds explicit import bounds')
                target=staging/name;target.parent.mkdir(parents=True,exist_ok=True)
                source=archive.extractfile(member)
                if source is None:
                    raise ValueError('Missing original archive member bytes')
                with target.open('wb') as output:
                    shutil.copyfileobj(source,output,length=1024**2)
                    output.flush();os.fsync(output.fileno())
        manifest_path=staging/'manifest.json';manifest_raw=manifest_path.read_bytes()
        manifest=json.loads(manifest_raw);ident=manifest.get('recording_id')
        if (manifest.get('schema')!='explorer_native_embodied_capture_manifest_v1' or
                not isinstance(ident,str) or not re.fullmatch('[0-9a-f]{32}',ident) or
                manifest.get('embodied_episode',{}).get('episode_id')!=ident):
            raise ValueError('Wrong native source schema or original episode identity')
        sources=manifest.get('embodied_source_files_sha256',{});payloads={};budget=0
        for name,digest in sources.items():
            path=_relative_file(staging,name)
            with path.open('rb') as source:
                if hashlib.file_digest(source,'sha256').hexdigest()!=digest:
                    raise ValueError('Native original source payload SHA mismatch')
            budget+=path.stat().st_size
            if budget<=admission_byte_budget:
                payloads[name]=path.read_bytes()
        admission=inspect_embodied_episode(manifest['embodied_episode'],payloads)
        if budget>admission_byte_budget:
            admission['admitted_for_training']=False;admission['admitted_for_dataset']=False
            admission['analysis_only']=True;admission['missing_facts'].append('NX_ADMISSION_BYTE_BUDGET')
        final=destination/ident
        if final.exists():
            if (final/'manifest.json').read_bytes()!=manifest_raw:
                raise ValueError('Original recording ID collision; existing source preserved')
            for name,digest in sources.items():
                with _relative_file(final,name).open('rb') as source:
                    if hashlib.file_digest(source,'sha256').hexdigest()!=digest:
                        raise ValueError('Existing imported original payload changed; source preserved for review')
        else:
            with archive_path.open('rb') as source,(staging/'original-source-transport.tar').open('wb') as output:
                shutil.copyfileobj(source,output,length=1024**2);output.flush();os.fsync(output.fileno())
            os.rename(staging,final)
        return {'schema':'explorer_native_capture_import_v1','recording_id':ident,
                'source_recording_id':ident,'source_episode_metadata':{'id':ident},
                'embodied_manifest':str(final/'manifest.json'),'dataset_manifest':None,
                'source_manifest_sha256':_sha(manifest_raw),'archive_sha256':expected_sha256,
                'admission':admission,'observation_only':not admission['admitted_for_dataset'],
                'raw_originals_preserved':True,'physical_success_inferred':False}
