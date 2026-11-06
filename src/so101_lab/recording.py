"""Durable raw episode recorder with a bounded asynchronous image writer.

All recording runs on the server. Closing a browser never sends a stop command.
"""
from __future__ import annotations

import copy
import hashlib
import io
import json
import os
import queue
import re
import shutil
import threading
import time
import traceback
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .hardware import JOINTS, ROOT, HardwareSource, calibration_snapshot, hardware_paths, hardware_settings
from .resources import ResourceLease, assert_disk_space, assert_hardware_available

SCHEMA = 'so101-raw-v1'


def utcnow(): return datetime.now(timezone.utc).isoformat()


def atomic_json(path: Path, value):
    path.parent.mkdir(parents=True,exist_ok=True)
    tmp = path.with_name(path.name+'.tmp-'+uuid.uuid4().hex[:8])
    with tmp.open('w') as out:
        json.dump(value,out,ensure_ascii=False,indent=2,allow_nan=False)
        out.write('\n'); out.flush(); os.fsync(out.fileno())
    os.replace(tmp,path)


def fsync_dir(path):
    fd = os.open(path,os.O_DIRECTORY)
    try: os.fsync(fd)
    finally: os.close(fd)


def integer(value, name, minimum=1, maximum=10000):
    # A numeric string "51" stays 51; floats such as 5.1 are rejected, never truncated.
    if isinstance(value,bool) or not re.fullmatch(r'[0-9]+',str(value)):
        raise ValueError(f'{name}: требуется целое число')
    number = int(value)
    if not minimum<=number<=maximum: raise ValueError(f'{name}: допустимо {minimum}–{maximum}')
    return number


def _hash_file(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as source:
        for chunk in iter(lambda:source.read(1024*1024),b''): digest.update(chunk)
    return digest.hexdigest()


def _positive_timestamp(value,label):
    if isinstance(value,bool) or not isinstance(value,int) or value<=0:
        raise ValueError(f'{label}: требуется положительный integer timestamp')
    return value


def _validate_episode_raw(path, dataset, *, allow_test_data=False, stop=None):
    """Validate saved bytes and causal timestamps; this cannot verify physical accuracy."""
    from PIL import Image
    from .features import FeatureContract, safe_child, validate_raw_frame
    metadata = json.loads((path/'episode.json').read_text())
    if metadata.get('status')!='complete': raise ValueError('Эпизод не имеет статуса complete')
    if metadata.get('schema_version')!=SCHEMA: raise ValueError('Несовместимая raw schema эпизода')
    if metadata.get('episode_id')!=path.name: raise ValueError('Episode ID не совпадает с папкой')
    if metadata.get('dataset_id')!=dataset['dataset_id']: raise ValueError('Неверный dataset_id эпизода')
    if metadata.get('source')!=dataset['source']: raise ValueError('Смешаны источники hardware/test')
    sessions = {session['session_id'] for session in dataset.get('sessions',[])}
    if metadata.get('session_id') not in sessions: raise ValueError('Неизвестный session_id эпизода')
    if not str(metadata.get('task','')).strip(): raise ValueError('Нет текста задачи')
    manifest = path/'frames.jsonl'
    manifest_hash = _hash_file(manifest)
    if manifest_hash!=metadata.get('frames_sha256'): raise ValueError('Hash frames.jsonl не совпадает')
    if dataset['source']=='hardware':
        for role in ('leader','follower'):
            expected = dataset.get('calibration',{}).get('files',{}).get(role,{}).get('sha256')
            snapshot_hash = metadata.get('calibration',{}).get('files',{}).get(role,{}).get('sha256')
            live = metadata.get('provenance',{}).get('live_calibration',{}).get(role,{})
            if not expected or snapshot_hash!=expected or live.get('verified_live') is not True or live.get('snapshot_sha256')!=expected:
                raise ValueError(f'Нет согласованного calibration readback: {role}')
    contract = FeatureContract(mode='rgb_state')
    count,first_time,last_time,last_id = 0,None,None,None
    last_action_time = None
    previous_sequences = {}
    dimensions = {}
    reused = {'top':0,'wrist':0}
    dropped = {'top':0,'wrist':0}
    max_camera_age,max_pair_skew,max_period,max_state_to_action = 0.,0.,0.,0.
    with manifest.open() as source:
        for line in source:
            if stop and stop(): raise InterruptedError('Raw validation остановлена пользователем')
            row = json.loads(line)
            if row.get('schema_version')!=SCHEMA or row.get('source')!=dataset['source']:
                raise ValueError('Несовместимая schema/source raw строки')
            validate_raw_frame(row,contract,training=True,allow_test=allow_test_data)
            frame_id = row['frame_id']
            if isinstance(frame_id,bool) or not isinstance(frame_id,int) or frame_id<=0 or (last_id is not None and frame_id<=last_id):
                raise ValueError('Немонoтонный/неверный frame_id')
            timestamp = _positive_timestamp(row['timestamp_ns'],'timestamp_ns')
            observation_time = _positive_timestamp(row['observation_timestamp_ns'],'observation_timestamp_ns')
            state_start = _positive_timestamp(row['state_read_start_ns'],'state_read_start_ns')
            state_end = _positive_timestamp(row['state_read_end_ns'],'state_read_end_ns')
            state_time = _positive_timestamp(row['state_timestamp_ns'],'state_timestamp_ns')
            action_start = _positive_timestamp(row['action_send_start_ns'],'action_send_start_ns')
            action_end = _positive_timestamp(row['action_send_end_ns'],'action_send_end_ns')
            action_time = _positive_timestamp(row['action_timestamp_ns'],'action_timestamp_ns')
            if not (timestamp==observation_time and state_start<=state_time==state_end<=observation_time<=action_start<=action_time==action_end):
                raise ValueError('Нарушен причинный порядок state/observation/action timestamps')
            if last_time is not None and (timestamp<=last_time or action_time<=last_action_time):
                raise ValueError('Немонoтонные observation/action timestamps')
            if set(row.get('cameras',{}))!={'top','wrist'}: raise ValueError('Raw требует обе камеры')
            camera_times=[]
            for camera in ('top','wrist'):
                info = row['cameras'][camera]
                camera_time = _positive_timestamp(info['timestamp_ns'],camera+' timestamp')
                capture_start = _positive_timestamp(info['read_start_ns'],camera+' read_start')
                capture_end = _positive_timestamp(info['read_end_ns'],camera+' read_end')
                if not capture_start<=camera_time==capture_end<=observation_time:
                    raise ValueError(f'{camera}: timestamp из будущего/неверный read interval')
                sequence = info['sequence']
                if isinstance(sequence,bool) or not isinstance(sequence,int) or sequence<=0:
                    raise ValueError(f'{camera}: неверный sequence')
                previous = previous_sequences.get(camera)
                is_reused = previous==sequence
                skipped = max(0,sequence-previous-1) if previous is not None else 0
                if previous is not None and sequence<previous: raise ValueError(f'{camera}: sequence идёт назад')
                if info.get('reused') is not is_reused or info.get('dropped_since_previous')!=skipped:
                    raise ValueError(f'{camera}: неверные reuse/drop counters')
                previous_sequences[camera]=sequence
                reused[camera]+=int(is_reused); dropped[camera]+=skipped
                image_path = safe_child(path,info['path'])
                if _hash_file(image_path)!=info['sha256']: raise ValueError(f'{camera}: raw image hash mismatch')
                with Image.open(image_path) as image:
                    image.load()
                    if image.format!='PNG' or image.mode!='RGB': raise ValueError(f'{camera}: нужен lossless RGB PNG')
                    if camera in dimensions and dimensions[camera]!=list(image.size):
                        raise ValueError(f'{camera}: разрешение изменилось внутри эпизода')
                    dimensions[camera]=list(image.size)
                    if dataset['source']=='hardware' and image.size!=(640,480):
                        raise ValueError(f'{camera}: hardware schema требует 640×480')
                camera_times.append(camera_time)
                max_camera_age=max(max_camera_age,(observation_time-camera_time)/1e6)
            max_pair_skew=max(max_pair_skew,(max(camera_times)-min(camera_times))/1e6)
            max_state_to_action=max(max_state_to_action,(action_time-state_time)/1e6)
            if last_time is not None: max_period=max(max_period,(timestamp-last_time)/1e9)
            first_time=first_time or timestamp;last_time=timestamp;last_id=frame_id;last_action_time=action_time;count+=1
    if count==0 or metadata.get('frames')!=count or metadata.get('writer_enqueued')!=count:
        raise ValueError('Число raw frames/записей writer не совпало или эпизод пуст')
    if metadata.get('reused_camera_frames')!=reused or metadata.get('dropped_camera_frames')!=dropped:
        raise ValueError('Итоговые camera reuse/drop counters не совпали')
    if metadata.get('first_timestamp_ns')!=first_time or metadata.get('last_timestamp_ns')!=last_time:
        raise ValueError('Границы timestamps эпизода не совпали')
    duration=(last_time-first_time)/1e9
    return {'episode_id':path.name,'session_id':metadata['session_id'],'frames':count,'frames_sha256':manifest_hash,
            'duration_s':duration,'fps_requested':metadata.get('fps_requested'),
            'fps_observed':((count-1)/duration if duration>0 else None),'max_sample_period_s':max_period,
            'max_camera_age_ms':max_camera_age,'max_camera_pair_skew_ms':max_pair_skew,
            'max_state_to_action_ms':max_state_to_action,'camera_dimensions':dimensions,
            'reused_camera_frames':reused,'dropped_camera_frames':dropped}


def finalize_dataset_path(dataset_path, allow_test_data=False, progress=None, stop=None):
    """CPU-only saved-file acceptance, usable by a durable job without a recording manager.

    Invalid input returns finalized=false and errors. No hardware or model is opened.
    """
    from .resources import dataset_lease
    path=Path(dataset_path).resolve()
    with dataset_lease(path):
        dataset=json.loads((path/'dataset.json').read_text())
        report={'schema_version':'so101-raw-validation-v1','dataset_id':dataset.get('dataset_id'),
                'checked_at':utcnow(),'source':dataset.get('source'),'software_only':True,
                'hardware_verification':'NOT_TESTED_BY_FILE_VALIDATOR','errors':[],'warnings':[],'episodes':[]}
        if dataset.get('schema_version')!=SCHEMA: report['errors'].append('Несовместимая dataset schema')
        if dataset.get('dataset_id')!=path.name: report['errors'].append('Dataset ID не совпадает с папкой')
        if dataset.get('source') not in ('hardware','synthetic_test'): report['errors'].append('Неизвестный источник')
        if dataset.get('source')!='hardware' and not allow_test_data:
            report['errors'].append('Тестовый источник требует явного allow_test_data; он не является настоящей демонстрацией')
        if dataset.get('joints')!=list(JOINTS) or dataset.get('units')!=['degrees']*5+['percent_calibrated_range']:
            report['errors'].append('Несовместимые joints/units')
        if not str(dataset.get('task','')).strip(): report['errors'].append('Нет текста задачи датасета')
        if list((path/'.partial').glob('*.partial')): report['errors'].append('Есть незавершённые .partial эпизоды; требуется восстановление')
        episodes=sorted(p for p in (path/'episodes').iterdir() if p.is_dir()) if (path/'episodes').exists() else []
        if not episodes: report['errors'].append('Нет завершённых эпизодов')
        for index,episode in enumerate(episodes):
            if stop and stop(): raise InterruptedError('Raw validation остановлена пользователем')
            try:
                metrics=_validate_episode_raw(episode,dataset,allow_test_data=allow_test_data,stop=stop)
                report['episodes'].append(metrics)
            except InterruptedError: raise
            except (OSError,ValueError,KeyError,TypeError,IndexError) as exc:
                report['errors'].append(f'{episode.name}: {type(exc).__name__}: {exc}')
            if progress: progress({'stage':'raw_validation','completed_episodes':index+1,'total_episodes':len(episodes)})
        report['frames']=sum(item['frames'] for item in report['episodes'])
        report['quarantine_episodes']=len(list((path/'quarantine').glob('*/episode.json')))
        if report['quarantine_episodes']: report['warnings'].append('Quarantine сохранён и исключён из train')
        if dataset.get('source')=='synthetic_test': report['warnings'].append('Технические данные: аппаратная работоспособность и успех политики не проверены')
        if len(report['episodes'])<3: report['warnings'].append('Меньше трёх эпизодов: полноценные train/validation/test могут быть недоступны')
        members=sorted((ep['episode_id'],ep['frames_sha256']) for ep in report['episodes'])
        digest=hashlib.sha256(json.dumps(members,separators=(',',':')).encode()).hexdigest()
        if dataset.get('dataset_sha256')!=digest: report['errors'].append('Dataset membership hash не совпадает с metadata')
        report.update(status='invalid' if report['errors'] else 'valid',finalized=not report['errors'],
                      training_eligible=not report['errors'] and dataset.get('source')=='hardware',
                      dataset_sha256=digest,dataset_version=dataset.get('version'))
        report_path=path/'validation/raw-validation.json'
        atomic_json(report_path,report)
        dataset['finalization']={'status':report['status'],'checked_at':report['checked_at'],
                                 'dataset_version':dataset.get('version'),'dataset_sha256':digest,
                                 'report_path':'validation/raw-validation.json','software_only':True,
                                 'training_eligible':report['training_eligible']}
        dataset['lifecycle_status']='finalized' if report['finalized'] else 'validation_failed'
        atomic_json(path/'dataset.json',dataset)
        return report


class SyntheticSource:
    """Explicit technical fixture. Sensor/joint/action values are never labelled measured."""
    source = 'synthetic_test'
    def __init__(self, settings=None):
        self.cfg = {'width':640,'height':480,**(settings or {})}
        self.started = False
    def start(self, **kwargs):
        self.started = True
        return {'source':self.source,'warning':'ТЕСТОВЫЙ источник: нет настоящих датчиков или команд моторам',
                'camera_geometry_valid':False,'joints':list(JOINTS),
                'units':['degrees']*5+['percent_calibrated_range'],'synthetic':True}
    def sample(self, frame_id):
        import numpy as np
        from PIL import Image,ImageDraw
        if not self.started: raise RuntimeError('Synthetic source is not started')
        timestamp = time.monotonic_ns()
        images = {}
        for camera in ('top','wrist'):
            im = Image.new('RGB',(int(self.cfg['width']),int(self.cfg['height'])),(28,35,44))
            draw = ImageDraw.Draw(im)
            offset = (frame_id*4)%max(1,self.cfg['width']-120)
            draw.rectangle((offset,120,offset+65,185),fill=(220,55,65))
            draw.rectangle((self.cfg['width']-160,240,self.cfg['width']-40,340),outline=(200,170,80),width=8)
            draw.text((15,15),f'SYNTHETIC TEST | {camera} | frame {frame_id}',fill='white')
            images[camera] = {'image':np.asarray(im),'timestamp_ns':timestamp,
                              'read_start_ns':timestamp,'read_end_ns':timestamp,
                              'sequence':frame_id,'reused':False,'dropped_since_previous':0}
        state = [float(((frame_id+i)%20)-10) for i in range(5)]+[50.]
        return {'frame_id':frame_id,'timestamp_ns':timestamp,'observation_timestamp_ns':timestamp,
                'state_timestamp_ns':timestamp,'state_read_start_ns':timestamp,'state_read_end_ns':timestamp,
                'observation.state':state,'action':[v+.1 for v in state],
                'action_timestamp_ns':timestamp+1,'action_send_start_ns':timestamp+1,'action_send_end_ns':timestamp+1,
                'state_valid':False,'action_valid':False,'action_acknowledged':False,
                'state_provenance':'synthetic_test_fixture','action_provenance':'synthetic_test_fixture_no_motor_command',
                'source':self.source,'images':images,'limits_applied':[]}
    def close(self): self.started = False; return []


class EpisodeWriter:
    """PNG encoding and fsync are isolated from the control thread; overflow stops recording."""
    def __init__(self, dataset_path, metadata, *, queue_size=32, on_preview=None):
        self.dataset_path = Path(dataset_path)
        self.metadata = dict(metadata)
        self.episode_id = metadata['episode_id']
        self.partial = self.dataset_path/'.partial'/(self.episode_id+'.partial')
        self.partial.mkdir(parents=True,exist_ok=False)
        for camera in ('top','wrist'): (self.partial/'images'/camera).mkdir(parents=True)
        self.metadata.update(status='recording',frames=0,started_at=utcnow(),schema_version=SCHEMA,
                             writer_pid=os.getpid(),writer_process_start_ticks=Path('/proc/self/stat').read_text().split()[21])
        atomic_json(self.partial/'episode.json',self.metadata)
        self.file = (self.partial/'frames.jsonl').open('w')
        self.queue = queue.Queue(maxsize=queue_size)
        self.error = None
        self.frames = 0
        self.enqueued = 0
        self.first_ns = None
        self.last_ns = None
        self.on_preview = on_preview
        self.reused = {'top':0,'wrist':0}
        self.dropped = {'top':0,'wrist':0}
        self.age_max_ms = {'top':0.,'wrist':0.}
        self._finish_requested = threading.Event()
        self.thread = threading.Thread(target=self._run,name='so101-ai-raw-writer',daemon=True)
        self.thread.start()
        self.closed = False

    def put(self, sample):
        if self.error: raise RuntimeError('Raw writer failed: '+self.error)
        if self._finish_requested.is_set(): raise RuntimeError('Raw writer is finishing')
        try: self.queue.put_nowait(sample)
        except queue.Full: raise RuntimeError('Raw writer queue overflow: запись остановлена, motion не повторяется')
        self.enqueued += 1

    def _run(self):
        try:
            from PIL import Image
            while not self._finish_requested.is_set() or not self.queue.empty():
                try: sample = self.queue.get(timeout=.1)
                except queue.Empty: sample = None
                if sample is not None:
                    record = {k:v for k,v in sample.items() if k!='images'}
                    record['schema_version'] = SCHEMA
                    record['cameras'] = {}
                    for camera,frame in sample['images'].items():
                        path = Path('images')/camera/(f'{sample["frame_id"]:08d}.png')
                        out = io.BytesIO()
                        Image.fromarray(frame['image']).save(out,format='PNG',compress_level=1)
                        data = out.getvalue()
                        # This is decoded camera RGB, saved losslessly. Camera MJPEG transport is not raw Bayer.
                        with (self.partial/path).open('wb') as image_file:
                            image_file.write(data)
                            image_file.flush(); os.fsync(image_file.fileno())
                        camera_meta = {k:v for k,v in frame.items() if k!='image'}
                        camera_meta.update(path=path.as_posix(),valid=True,sha256=hashlib.sha256(data).hexdigest(),
                                           encoding='png_rgb8_lossless',exposure_timestamp_valid=False)
                        record['cameras'][camera] = camera_meta
                        self.reused[camera] += int(frame.get('reused',False))
                        self.dropped[camera] += int(frame.get('dropped_since_previous',0))
                        self.age_max_ms[camera] = max(self.age_max_ms[camera],(record['timestamp_ns']-frame['timestamp_ns'])/1e6)
                        if self.on_preview: self.on_preview(camera,data)
                    record['feedback'] = {
                        'joint_position':{'valid':bool(record.get('state_valid')),'units':['degrees']*5+['percent_calibrated_range'],
                                          'timestamp_ns':record['state_timestamp_ns'],'provenance':record.get('state_provenance','follower_present_position')},
                        'gripper_position':{'valid':bool(record.get('state_valid')),'value':record['observation.state'][5],
                                            'unit':'percent_calibrated_range','provenance':record.get('state_provenance','follower_present_position')},
                        'velocity':{'valid':False,'value':None,'reason':'not_sampled_to_preserve_control_frequency'},
                        'current':{'valid':False,'value':None,'reason':'not_sampled_to_preserve_control_frequency'},
                        'load':{'valid':False,'value':None,'reason':'not_sampled_to_preserve_control_frequency'},
                        'force':{'valid':False,'value':None,'reason':'no_force_sensor'},
                    }
                    record['labels'] = {'phase':{'valid':False,'value':None},'success':{'valid':False,'value':None},
                                        'done':{'valid':False,'value':None},'grasp':{'valid':False,'value':None}}
                    record['latency_ms'] = {'state_read':(record['state_read_end_ns']-record['state_read_start_ns'])/1e6,
                                           'state_to_action':(record['action_timestamp_ns']-record['state_timestamp_ns'])/1e6,
                                           'action_send':(record['action_send_end_ns']-record['action_send_start_ns'])/1e6,
                                           'writer_queue':(time.monotonic_ns()-record['action_timestamp_ns'])/1e6}
                    self.file.write(json.dumps(record,ensure_ascii=False,allow_nan=False,separators=(',',':'))+'\n')
                    self.frames += 1
                    self.first_ns = self.first_ns or record['timestamp_ns']
                    self.last_ns = record['timestamp_ns']
                    if self.frames%15==0:
                        self.file.flush(); os.fsync(self.file.fileno())
        except Exception:
            self.error = traceback.format_exc()
            with (self.partial/'writer-traceback.log').open('w') as out: out.write(self.error)
        finally:
            self.file.flush(); os.fsync(self.file.fileno()); self.file.close()

    def finish(self, *, status='complete', reason=None, timeout=30, on_wait=None):
        if self.closed: return self.metadata
        # Both enqueue and drain have one deadline. Never rename a live writer's directory.
        self._finish_requested.set()
        deadline = time.monotonic()+max(0.,float(timeout))
        while self.thread.is_alive() and time.monotonic()<deadline:
            if on_wait: on_wait()
            self.thread.join(timeout=min(.05,max(0.,deadline-time.monotonic())))
        if self.thread.is_alive():
            self.metadata.update(status='incomplete',frames=self.frames,reason='raw writer drain timeout; directory retained in .partial',
                                 writer_pending=True,path=str(self.partial))
            atomic_json(self.partial/'episode.json',self.metadata)
            return dict(self.metadata)
        self.closed = True
        self.metadata.pop('writer_pending',None)
        if self.error: status,reason = 'incomplete',self.error
        if self.frames==0: status,reason = 'incomplete',reason or 'no frames'
        self.metadata.update(status=status,frames=self.frames,finished_at=utcnow(),reason=reason,
                             first_timestamp_ns=self.first_ns,last_timestamp_ns=self.last_ns,
                             duration_s=((self.last_ns-self.first_ns)/1e9 if self.frames>1 else 0),
                             reused_camera_frames=self.reused,dropped_camera_frames=self.dropped,
                             max_camera_age_ms=self.age_max_ms,writer_enqueued=self.enqueued)
        if not self.thread.is_alive():
            self.metadata['frames_sha256'] = hashlib.sha256((self.partial/'frames.jsonl').read_bytes()).hexdigest()
        atomic_json(self.partial/'episode.json',self.metadata)
        for camera in ('top','wrist'): fsync_dir(self.partial/'images'/camera)
        fsync_dir(self.partial/'images')
        fsync_dir(self.partial)
        parent = self.dataset_path/('episodes' if status=='complete' else 'quarantine')
        parent.mkdir(exist_ok=True)
        target = parent/self.episode_id
        if target.exists(): target = parent/(self.episode_id+'-'+uuid.uuid4().hex[:8])
        os.replace(self.partial,target)
        fsync_dir(parent)
        self.metadata['path'] = str(target)
        return dict(self.metadata)


class RecordingManager:
    def __init__(self, root=ROOT):
        self.root = Path(root).resolve()
        self.data_root = self.root/'data'
        self.data_root.mkdir(parents=True,exist_ok=True)
        self.lock = threading.RLock()
        self.commands = queue.Queue()
        self.stop_event = threading.Event()
        self.requested_stop_action = None
        self._source_closed = False
        self._source_stop_errors = []
        self.thread = None
        self.writer = None
        self.source = None
        self.preview = {}
        self.current = {'state':'idle','active':False,'hardware_open':False,'updated_at':utcnow()}
        recovered = self.recover_incomplete()
        if recovered:
            self.current.update(recovered_episodes=recovered,message='Неполные эпизоды после обрыва сохранены в quarantine; перед аппаратным Start проверьте робот и физический STOP')
        self._persist()

    def _persist(self):
        atomic_json(self.root/'runtime/recording.json',self.current)

    def _update(self, **values):
        with self.lock:
            self.current.update(values,updated_at=utcnow())
            self._persist()

    def _dataset_path(self,dataset_id):
        if not re.fullmatch(r'ds_[a-f0-9]{16}',str(dataset_id)): raise ValueError('Некорректный dataset ID')
        path = self.data_root/dataset_id
        if not (path/'dataset.json').is_file(): raise FileNotFoundError('Датасет не найден')
        return path

    def create_dataset(self,name,task,source='synthetic_test',**settings):
        name,task = str(name).strip(),str(task).strip()
        if not name or len(name)>200: raise ValueError('Имя датасета: 1–200 символов')
        if not task or len(task)>2000: raise ValueError('Текст задачи: 1–2000 символов')
        if source not in ('hardware','synthetic_test'): raise ValueError('Неизвестный источник записи')
        dataset_id = 'ds_'+uuid.uuid4().hex[:16]
        path = self.data_root/dataset_id
        path.mkdir(exist_ok=False)
        calibration = calibration_snapshot() if source=='hardware' else {'valid':False,'source':'synthetic_test'}
        meta = {'dataset_id':dataset_id,'name':name,'task':task,'source':source,'schema_version':SCHEMA,
                'created_at':utcnow(),'sessions':[],'joints':list(JOINTS),'units':['degrees']*5+['percent_calibrated_range'],
                'calibration':calibration,'settings':settings,'version':0,'episodes':0,'test_only':source=='synthetic_test'}
        atomic_json(path/'dataset.json',meta)
        (path/'episodes').mkdir(); (path/'quarantine').mkdir()
        return self.dataset_info(dataset_id)

    def dataset_info(self,dataset_id):
        path = self._dataset_path(dataset_id)
        meta = json.loads((path/'dataset.json').read_text())
        episodes = []
        for item in sorted((path/'episodes').glob('*/episode.json')):
            try: episodes.append(json.loads(item.read_text()))
            except (OSError,ValueError): pass
        meta['episodes'] = len([e for e in episodes if e.get('status')=='complete'])
        meta['episode_list'] = episodes
        meta['quarantine_count'] = len(list((path/'quarantine').glob('*/episode.json')))
        meta['path'] = str(path)
        return meta

    def list_datasets(self):
        datasets = []
        for path in self.data_root.glob('ds_*/dataset.json'):
            try: datasets.append(self.dataset_info(path.parent.name))
            except (OSError,ValueError,KeyError): pass
        return sorted(datasets,key=lambda d:d['created_at'],reverse=True)

    def finalize_dataset(self,dataset_id,allow_test_data=False,progress=None,stop=None):
        if self.status().get('active') and self.status().get('dataset_id')==dataset_id:
            raise RuntimeError('Сначала остановите запись датасета')
        return finalize_dataset_path(self._dataset_path(dataset_id),allow_test_data,progress,stop)

    def status(self):
        with self.lock:
            result = copy.deepcopy(self.current)
            if self.writer:
                result['frames_written'] = self.writer.frames
                result['writer_queue'] = self.writer.queue.qsize()
                result['frames_enqueued'] = self.writer.enqueued
            return result

    def latest_frame(self,camera):
        with self.lock: return self.preview.get(camera)

    def start(self,dataset_id,**settings):
        with self.lock:
            if self.thread and self.thread.is_alive():
                if self.current.get('dataset_id')==dataset_id and self.current.get('state')=='ready':
                    return self.command('next')
                raise RuntimeError('Уже есть активная запись; сначала Stop')
            path = self._dataset_path(dataset_id)
            from .resources import dataset_lease
            with dataset_lease(path):pass  # fail before opening hardware if training holds the data
            dataset = json.loads((path/'dataset.json').read_text())
            cfg = {**dataset.get('settings',{}),**settings}
            cfg['episode_count'] = integer(cfg.get('episode_count',1),'Число эпизодов',1,10000)
            cfg['fps'] = float(cfg.get('fps',30))
            if not 1<=cfg['fps']<=30: raise ValueError('FPS: 1–30')
            cfg['episode_seconds'] = float(cfg.get('episode_seconds',30))
            if not 0<cfg['episode_seconds']<=600: raise ValueError('Длительность: 0–600 секунд')
            cfg['idle_timeout_seconds'] = float(cfg.get('idle_timeout_seconds',120))
            if not 1<=cfg['idle_timeout_seconds']<=600: raise ValueError('Таймаут паузы: 1–600 секунд')
            cfg['minimum_free_gb'] = max(1.,float(cfg.get('minimum_free_gb',2.)))
            assert_disk_space(int(cfg['minimum_free_gb']*1024**3),path)
            if dataset['source']=='hardware':
                if not bool(cfg.get('explicit_user_start')):
                    raise PermissionError('Для реального робота нужен явный Start в UI')
                # Preflight before any device open, model import, or source thread creation.
                hardware_settings(cfg)
                assert_hardware_available(hardware_paths(cfg))
                source = HardwareSource(cfg)
            else: source = SyntheticSource(cfg)
            self.source = source
            self._source_closed = False
            self._source_stop_errors = []
            self.commands = queue.Queue()
            self.stop_event.clear()
            self.requested_stop_action = None
            session_id = 'session_'+uuid.uuid4().hex[:16]
            self.current = {'state':'starting','active':True,'dataset_id':dataset_id,'name':dataset['name'],
                            'session_id':session_id,'source':dataset['source'],'task':dataset['task'],
                            'hardware_open':False,'completed_in_session':0,'target_episodes':cfg['episode_count'],
                            'started_at':utcnow(),'updated_at':utcnow(),'settings':cfg}
            self._persist()
            self.thread = threading.Thread(target=self._run,args=(path,dataset,cfg,session_id),
                                           name='so101-ai-recorder',daemon=True)
            self.thread.start()
            return self.status()

    def command(self,action):
        if action not in ('end','rerecord','stop','next','abort'): raise ValueError('Неизвестная команда записи')
        with self.lock:
            if not self.thread or not self.thread.is_alive():
                if action=='stop': return self.status()
                raise RuntimeError('Нет активной записи')
            if action=='stop' or action=='abort':
                self.requested_stop_action = action
                self.stop_event.set()
            self.commands.put(action)
            return {**self.status(),'command_queued':action}

    def wait(self,timeout=10):
        thread = self.thread
        if thread: thread.join(timeout)
        return self.status()

    def _new_writer(self,path,dataset,session_id,cfg,provenance):
        if hasattr(self.source,'begin_episode'): self.source.begin_episode()
        episode_id = 'ep_'+time.strftime('%Y%m%dT%H%M%S')+'_'+uuid.uuid4().hex[:10]
        metadata = {'episode_id':episode_id,'dataset_id':dataset['dataset_id'],'session_id':session_id,
                    'source':dataset['source'],'task':dataset['task'],'test_only':dataset['source']=='synthetic_test',
                    'fps_requested':cfg['fps'],'camera_mode':'two_camera_raw','provenance':provenance,
                    'calibration':dataset['calibration'],'operator_labels':{},'scan_history_available':False}
        writer = EpisodeWriter(path,metadata,on_preview=self._preview)
        self.writer = writer
        self._update(state='recording',episode_id=episode_id,frames_written=0,frames_enqueued=0)
        return writer

    def _stop_source(self):
        """Called by the single recorder thread before potentially slow disk finalization."""
        if not self._source_closed:
            self._source_closed = True
            try: self._source_stop_errors = self.source.close() if self.source else []
            except Exception as exc:
                self._source_stop_errors = [f'source close failed: {type(exc).__name__}: {exc}']
            self._update(hardware_open=False,stop_errors=self._source_stop_errors,
                         hardware_stop_state='torque_disable_unconfirmed_operator_check_required' if self._source_stop_errors else 'devices_closed')
        return self._source_stop_errors

    def _finish_writer(self,writer,**options):
        def respond_to_stop():
            if self.stop_event.is_set(): self._stop_source()
        result = writer.finish(on_wait=respond_to_stop,**options)
        if result.get('writer_pending'):
            raise RuntimeError('Raw writer не завершился: данные оставлены в .partial; требуется восстановление')
        return result

    def _preview(self,camera,data):
        with self.lock: self.preview[camera] = data

    def _version(self,path,session_id,episode=None,provenance=None):
        # Called only by the recording worker. Dataset updates are atomic.
        meta = json.loads((path/'dataset.json').read_text())
        meta['lifecycle_status']='draft'
        if meta.get('finalization'):
            meta['finalization'].update(status='stale_after_recording',training_eligible=False)
        if session_id not in {s['session_id'] for s in meta['sessions']}:
            meta['sessions'].append({'session_id':session_id,'started_at':utcnow(),'provenance':provenance})
        if episode:
            meta['version'] += 1
            meta['last_episode_id'] = episode['episode_id']
            meta['updated_at'] = utcnow()
        complete = [p for p in (path/'episodes').glob('*/episode.json') if json.loads(p.read_text()).get('status')=='complete']
        meta['episodes'] = len(complete)
        # Stable version fingerprint changes only when finalized episode content/membership changes.
        member_hashes = sorted((p.parent.name,json.loads(p.read_text()).get('frames_sha256')) for p in complete)
        meta['dataset_sha256'] = hashlib.sha256(json.dumps(member_hashes,separators=(',',':')).encode()).hexdigest()
        atomic_json(path/'dataset.json',meta)

    def _run(self,path,dataset,cfg,session_id):
        writer = None
        last_complete = None
        completed = 0
        final_state = 'stopped'
        from .resources import dataset_lease
        data_lease=dataset_lease(path)
        data_acquired=False
        recording_lease = ResourceLease('recording')
        lease_acquired = False
        try:
            recording_lease.__enter__(); lease_acquired=True
            data_lease.__enter__();data_acquired=True
            provenance = self.source.start(explicit_user_start=bool(cfg.get('explicit_user_start')),stop=self.stop_event.is_set)
            self._version(path,session_id,provenance=provenance)
            self._update(hardware_open=dataset['source']=='hardware',provenance=provenance)
            writer = self._new_writer(path,dataset,session_id,cfg,provenance)
            frame_id = 0
            episode_start = time.monotonic()
            idle_start = None
            deadline = time.monotonic()
            while True:
                action = None
                try: action = self.commands.get_nowait()
                except queue.Empty: pass
                if action in ('stop','abort') or self.stop_event.is_set():
                    action = self.requested_stop_action or action
                    self._update(state='stopping')
                    self._stop_source()
                    if writer:
                        result = self._finish_writer(writer,status='incomplete' if action=='abort' else 'complete',
                                               reason='operator_abort' if action=='abort' else 'operator_stop')
                        if result['status']=='complete': completed+=1
                        self._version(path,session_id,result)
                        self._update(last_episode=result)
                        writer = None; self.writer = None
                    break
                if action=='rerecord':
                    if writer:
                        result = self._finish_writer(writer,status='rerecorded',reason='operator_rerecord')
                        self._version(path,session_id,result)
                    elif last_complete:
                        # Preserve rejected data, remove it from the default train membership.
                        old = Path(last_complete['path'])
                        if old.exists():
                            info = json.loads((old/'episode.json').read_text())
                            info.update(status='superseded',reason='operator_rerecord',superseded_at=utcnow())
                            atomic_json(old/'episode.json',info)
                            os.replace(old,path/'quarantine'/old.name)
                            completed=max(0,completed-1)
                            self._version(path,session_id,info)
                    if self.stop_event.is_set():
                        writer=None;self.writer=None
                    else:
                        writer = self._new_writer(path,dataset,session_id,cfg,provenance)
                        frame_id=0; episode_start=time.monotonic(); idle_start=None; deadline=time.monotonic()
                elif action=='next' and not writer:
                    writer = self._new_writer(path,dataset,session_id,cfg,provenance)
                    frame_id=0; episode_start=time.monotonic(); idle_start=None; deadline=time.monotonic()
                elif action=='end' and writer:
                    result = self._finish_writer(writer,status='complete',reason='operator_end')
                    if result['status']=='complete': completed+=1; last_complete=result
                    else: raise RuntimeError('Эпизод не завершён: '+str(result.get('reason')))
                    self._version(path,session_id,result)
                    writer=None; self.writer=None
                    self._update(state='ready',completed_in_session=completed,last_episode=result)
                    if completed>=cfg['episode_count']: break
                    idle_start=time.monotonic()
                if self.stop_event.is_set():
                    pass  # Return to the STOP branch before any further capture or bus command.
                elif writer is None:
                    if idle_start and time.monotonic()-idle_start>cfg['idle_timeout_seconds']:
                        self._update(message='Пауза превысила таймаут; hardware закрыт, нажмите Start для продолжения')
                        break
                    self.stop_event.wait(.02)
                else:
                    current = time.monotonic()
                    if current<deadline:
                        self.stop_event.wait(min(.05,deadline-current))
                    else:
                        frame_id+=1
                        sample = self.source.sample(frame_id)
                        sample['control_lateness_ms'] = max(0.,(current-deadline)*1000)
                        writer.put(sample)
                        period = 1/cfg['fps']
                        finished = time.monotonic()
                        # Slow samples never produce an immediate catch-up motion command.
                        deadline = current+period if finished<current+period else finished+period
                        if frame_id%max(1,int(cfg['fps']))==0:
                            assert_disk_space(int(cfg['minimum_free_gb']*1024**3),path)
                            self._update(frames_written=writer.frames,frames_enqueued=writer.enqueued,
                                         episode_elapsed_s=time.monotonic()-episode_start)
                        if time.monotonic()-episode_start>=cfg['episode_seconds']:
                            self.commands.put('end')
        except Exception as exc:
            final_state='failed'
            self._stop_source()
            error=traceback.format_exc()
            log=self.root/'logs'/('recording-'+session_id+'.log'); log.parent.mkdir(exist_ok=True)
            log.write_text(error)
            self._update(error=f'{type(exc).__name__}: {exc}',traceback_path=str(log))
            if writer:
                try:
                    result=writer.finish(status='incomplete',reason=str(exc),timeout=0 if writer.metadata.get('writer_pending') else 30)
                    if not result.get('writer_pending'): self._version(path,session_id,result)
                    self._update(last_episode=result)
                except Exception:
                    with log.open('a') as out: out.write('\n'+traceback.format_exc())
        finally:
            stop_errors = self._stop_source()
            if stop_errors: final_state = 'failed'
            if data_acquired:data_lease.__exit__(None,None,None)
            if lease_acquired: recording_lease.__exit__(None,None,None)
            self.writer = None
            self._update(state=final_state,active=False,hardware_open=False,completed_in_session=completed,
                         stopped_at=utcnow(),stop_errors=stop_errors,
                         hardware_stop_state='torque_disable_unconfirmed_operator_check_required' if stop_errors else 'devices_closed')

    def recover_incomplete(self):
        recovered=[]
        for path in self.data_root.glob('ds_*/.partial/*.partial'):
            try:
                meta_path=path/'episode.json'
                meta=json.loads(meta_path.read_text()) if meta_path.exists() else {'episode_id':path.name.removesuffix('.partial')}
                owner = meta.get('writer_pid')
                owner_active = False
                if owner and meta.get('writer_process_start_ticks'):
                    try:
                        if Path(f'/proc/{int(owner)}/stat').read_text().split()[21] == meta['writer_process_start_ticks']:
                            owner_active = True
                    except (OSError,ValueError,IndexError): pass
                if not owner_active:
                    meta.update(status='incomplete',reason='server_restart_before_atomic_finalization',recovered_at=utcnow())
                    atomic_json(meta_path,meta)
                    quarantine=path.parent.parent/'quarantine'; quarantine.mkdir(exist_ok=True)
                    target=quarantine/meta['episode_id']
                    if target.exists(): target=quarantine/(meta['episode_id']+'-'+uuid.uuid4().hex[:8])
                    os.replace(path,target)
                    recovered.append(str(target))
            except Exception:
                self.root.joinpath('logs').mkdir(exist_ok=True)
                with (self.root/'logs/recovery-errors.log').open('a') as out: out.write(traceback.format_exc())
        return recovered
