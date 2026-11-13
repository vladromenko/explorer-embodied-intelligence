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
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from .hardware import JOINTS, ROOT, HardwareSource, calibration_snapshot, hardware_paths, hardware_settings,configure_cv
from .resources import ResourceLease, assert_disk_space, assert_hardware_available

SCHEMA = 'so101-raw-v1'
CAMERA_MODES = {'two_camera':('top','wrist'),'wrist_only':('wrist',)}


def dataset_cameras(metadata):
    mode=metadata.get('camera_mode',metadata.get('settings',{}).get('camera_mode','two_camera'))
    if mode not in CAMERA_MODES:raise ValueError('Неизвестный режим камер датасета')
    return CAMERA_MODES[mode]


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


def annotation_collection_progress(path, target_total):
    completed=len([p for p in (Path(path)/'episodes').glob('*/episode.json') if json.loads(p.read_text()).get('status')=='complete'])
    return completed,int(target_total),completed>=int(target_total)


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
    cameras=dataset_cameras(dataset)
    contract = FeatureContract(mode='rgb_state',camera_mode='two_camera' if cameras==('top','wrist') else 'wrist_only_experimental')
    count,first_time,last_time,last_id = 0,None,None,None
    last_action_time = None
    previous_sequences = {}
    dimensions = {}
    reused = {camera:0 for camera in cameras}
    dropped = {camera:0 for camera in cameras}
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
            if tuple(row.get('cameras',{}))!=cameras: raise ValueError('Raw не соответствует режиму камер датасета')
            camera_times=[]
            for camera in cameras:
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
            max_pair_skew=max(max_pair_skew,(max(camera_times)-min(camera_times))/1e6 if len(camera_times)>1 else 0.)
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
        self.cameras=CAMERA_MODES.get(self.cfg.get('camera_mode','two_camera'))
        if self.cameras is None:raise ValueError('Неизвестный режим камер датасета')
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
        for camera in self.cameras:
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
    """PNG encoding and disk writes are isolated from control; overflow stops recording."""
    def __init__(self, dataset_path, metadata, *, queue_size=600, on_preview=None):
        configure_cv()
        self.dataset_path = Path(dataset_path)
        self.metadata = dict(metadata)
        self.cameras=tuple(metadata.get('cameras') or CAMERA_MODES.get(metadata.get('camera_mode','two_camera'),()))
        if not self.cameras:raise ValueError('Эпизод не содержит допустимый режим камер')
        self.episode_id = metadata['episode_id']
        self.partial = self.dataset_path/'.partial'/(self.episode_id+'.partial')
        self.partial.mkdir(parents=True,exist_ok=False)
        for camera in self.cameras: (self.partial/'images'/camera).mkdir(parents=True)
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
        self.reused = {camera:0 for camera in self.cameras}
        self.dropped = {camera:0 for camera in self.cameras}
        self.age_max_ms = {camera:0. for camera in self.cameras}
        self.previous_action = None
        self.previous_action_ns = None
        self.max_joint_action_delta = 0.
        self.max_joint_action_speed = 0.
        self.max_gripper_action_delta = 0.
        self._finish_requested = threading.Event()
        self.performance={'bytes_written':0,'encode_write_seconds':0.,'peak_queue':0,'encoder':'opencv_png1_lossless','cameras':list(self.cameras)}
        self.thread = threading.Thread(target=self._run,name='so101-ai-raw-writer',daemon=True)
        self.thread.start()
        self.closed = False

    def put(self, sample):
        if self.error: raise RuntimeError('Raw writer failed: '+self.error)
        if self._finish_requested.is_set(): raise RuntimeError('Raw writer is finishing')
        try: self.queue.put_nowait(sample)
        except queue.Full: raise RuntimeError('Raw writer queue overflow: запись остановлена, motion не повторяется')
        self.enqueued += 1
        self.performance['peak_queue']=max(self.performance['peak_queue'],self.queue.qsize())

    def check_ready(self):
        if self.error:raise RuntimeError('Raw writer failed: '+self.error)
        if self.queue.full():raise RuntimeError('Накопитель не успевает сохранять raw. Запись остановлена до следующей команды; данные сохранены в архив')

    def _encode_image(self,camera,frame,frame_id):
        import cv2
        before=time.monotonic()
        # Level 0 produced ~0.92 MB per 640x480 frame. During a long capture
        # burst the kernel eventually throttled dirty-page writes for several
        # seconds and the robot session was aborted even though the NVMe and
        # cameras were healthy. Level 1 remains byte-exact lossless RGB while
        # reducing the measured wrist stream from ~26 to ~10 MB/s.
        ok,png=cv2.imencode('.png',cv2.cvtColor(frame['image'],cv2.COLOR_RGB2BGR),[cv2.IMWRITE_PNG_COMPRESSION,1])
        if not ok:raise RuntimeError('PNG encoder failed: '+camera)
        data=png.tobytes();path=Path('images')/camera/(f'{frame_id:08d}.png')
        with (self.partial/path).open('wb') as image_file:
            # A synchronous flush for every ~1 MB image can stall NVMe for
            # seconds and must never hold up teleoperation.  Closing the file
            # submits the complete PNG; episode metadata and directories are
            # fsynced once the writer has drained.
            image_file.write(data)
        return path,data,time.monotonic()-before

    def _run(self):
        try:
            with ThreadPoolExecutor(max_workers=2,thread_name_prefix='so101-png') as encoders:
              while not self._finish_requested.is_set() or not self.queue.empty():
                try: sample = self.queue.get(timeout=.1)
                except queue.Empty: sample = None
                if sample is not None:
                    record = {k:v for k,v in sample.items() if k!='images'}
                    record['schema_version'] = SCHEMA
                    record['cameras'] = {}
                    pending={camera:encoders.submit(self._encode_image,camera,frame,sample['frame_id']) for camera,frame in sample['images'].items()}
                    for camera,frame in sample['images'].items():
                        path,data,duration=pending[camera].result()
                        self.performance['bytes_written']+=len(data)
                        self.performance['encode_write_seconds']+=duration
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
                    if self.previous_action is not None:
                        dt=(record['action_timestamp_ns']-self.previous_action_ns)/1e9
                        if dt>0:
                            deltas=[abs(float(a)-float(b)) for a,b in zip(record['action'],self.previous_action)]
                            self.max_joint_action_delta=max(self.max_joint_action_delta,*deltas[:5])
                            self.max_joint_action_speed=max(self.max_joint_action_speed,*(value/dt for value in deltas[:5]))
                            self.max_gripper_action_delta=max(self.max_gripper_action_delta,deltas[5])
                    self.previous_action=list(record['action']);self.previous_action_ns=record['action_timestamp_ns']
                    record['latency_ms'] = {'state_read':(record['state_read_end_ns']-record['state_read_start_ns'])/1e6,
                                           'state_to_action':(record['action_timestamp_ns']-record['state_timestamp_ns'])/1e6,
                                           'action_send':(record['action_send_end_ns']-record['action_send_start_ns'])/1e6,
                                           'writer_queue':(time.monotonic_ns()-record['action_timestamp_ns'])/1e6}
                    self.file.write(json.dumps(record,ensure_ascii=False,allow_nan=False,separators=(',',':'))+'\n')
                    self.frames += 1
                    self.first_ns = self.first_ns or record['timestamp_ns']
                    self.last_ns = record['timestamp_ns']
                    if self.frames%15==0:
                        # Do not force an NVMe cache flush while the robot is
                        # following the leader.  A single multi-second fsync
                        # used to fill the bounded image queue and abort an
                        # otherwise healthy hardware session.  The manifest,
                        # images and directories are synchronised once the
                        # episode has stopped and the writer has drained.
                        self.file.flush()
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
        self.metadata['writer_performance']=dict(self.performance)
        self.metadata['motion_quality']={'max_joint_action_delta_degrees':self.max_joint_action_delta,
            'max_joint_action_speed_degrees_s':self.max_joint_action_speed,
            'max_gripper_action_delta_percent':self.max_gripper_action_delta}
        if not self.thread.is_alive():
            self.metadata['frames_sha256'] = hashlib.sha256((self.partial/'frames.jsonl').read_bytes()).hexdigest()
        atomic_json(self.partial/'episode.json',self.metadata)
        for camera in self.cameras: fsync_dir(self.partial/'images'/camera)
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
    def __init__(self, root=ROOT, *, on_session_complete=None, telemetry_factory=None, on_session_start=None):
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
        self.on_session_complete=on_session_complete
        self.telemetry_factory=telemetry_factory
        self.on_session_start=on_session_start
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
        camera_mode=settings.get('camera_mode','two_camera')
        if camera_mode not in CAMERA_MODES:raise ValueError('Режим камер должен быть two_camera или wrist_only')
        settings['camera_mode']=camera_mode
        target_episodes=integer(settings.pop('target_episodes',1),'Целевое число эпизодов',1,10000)
        dataset_id = 'ds_'+uuid.uuid4().hex[:16]
        path = self.data_root/dataset_id
        path.mkdir(exist_ok=False)
        calibration = calibration_snapshot() if source=='hardware' else {'valid':False,'source':'synthetic_test'}
        meta = {'dataset_id':dataset_id,'name':name,'task':task,'source':source,'schema_version':SCHEMA,
                'created_at':utcnow(),'sessions':[],'joints':list(JOINTS),'units':['degrees']*5+['percent_calibrated_range'],
                'calibration':calibration,'camera_mode':camera_mode,'cameras':list(CAMERA_MODES[camera_mode]),
                'target_episodes':target_episodes,'settings':settings,'version':0,'episodes':0,'test_only':source=='synthetic_test'}
        atomic_json(path/'dataset.json',meta)
        (path/'episodes').mkdir(); (path/'quarantine').mkdir()
        return self.dataset_info(dataset_id)

    def dataset_info(self,dataset_id):
        path = self._dataset_path(dataset_id)
        meta = json.loads((path/'dataset.json').read_text())
        meta.setdefault('camera_mode',meta.get('settings',{}).get('camera_mode','two_camera'))
        meta.setdefault('cameras',list(CAMERA_MODES.get(meta['camera_mode'],())))
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
            try:
                info=self.dataset_info(path.parent.name);info.pop('episode_list',None);datasets.append(info)
            except (OSError,ValueError,KeyError): pass
        return sorted(datasets,key=lambda d:d['created_at'],reverse=True)

    def delete_dataset(self,dataset_id):
        if self.status().get('active') and self.status().get('dataset_id')==dataset_id:
            raise RuntimeError('Сначала завершите запись этого датасета')
        path=self._dataset_path(dataset_id)
        from .resources import dataset_lease
        with dataset_lease(path):
            trash=self.data_root/'.trash';trash.mkdir(exist_ok=True)
            target=trash/(dataset_id+'_'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'_'+uuid.uuid4().hex[:8])
            os.replace(path,target);fsync_dir(trash);fsync_dir(self.data_root)
        return {'status':'deleted','dataset_id':dataset_id,'recoverable':True,'trash_path':str(target)}

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
                result['writer_performance']=dict(self.writer.performance)
            if result.get('state')=='recording' and result.get('episode_start_monotonic_ns'):
                result['episode_elapsed_s']=(time.monotonic_ns()-result['episode_start_monotonic_ns'])/1e9
            if result.get('state')=='preparing':result['preparation_remaining_s']=max(0.,(result.get('preparation_deadline_ns',0)-time.monotonic_ns())/1e9)
            mlflow=result.get('mlflow')
            if mlflow and mlflow.get('spool'):
                try:
                    info=json.loads((Path(mlflow['spool'])/'run_info').read_text())
                    result['mlflow']={**mlflow,**info,'state':'connected'}
                except (OSError,ValueError):pass
                spool=Path(mlflow['spool'])
                result['mlflow']['pending_events']=len(list(spool.glob('*.json')))
                try:
                    result['mlflow']['delivery_error']=(spool/'last_error.txt').read_text()[-1200:]
                    result['mlflow']['state']='buffering'
                except OSError:pass
            return result

    def latest_frame(self,camera):
        with self.lock: return self.preview.get(camera)

    def start(self,dataset_id,*,camera_provider=None,**settings):
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
            cfg['camera_mode']=dataset.get('camera_mode',dataset.get('settings',{}).get('camera_mode','two_camera'))
            cfg['episode_count'] = integer(cfg.get('episode_count',1),'Число эпизодов',1,10000)
            completed_before_session=len([p for p in (path/'episodes').glob('*/episode.json') if json.loads(p.read_text()).get('status')=='complete'])
            if 'target_episodes_total' in settings:
                target_episodes_total=integer(settings['target_episodes_total'],'Общее целевое число эпизодов',1,10000)
                if target_episodes_total<=completed_before_session:raise ValueError('В датасете уже достигнуто выбранное число эпизодов')
                cfg['episode_count']=target_episodes_total-completed_before_session
            elif 'episode_count' not in settings and dataset.get('target_episodes',0)>completed_before_session:
                target_episodes_total=integer(dataset['target_episodes'],'Общее целевое число эпизодов',1,10000)
                cfg['episode_count']=target_episodes_total-completed_before_session
            else:target_episodes_total=completed_before_session+cfg['episode_count']
            cfg['target_episodes_total']=target_episodes_total
            if dataset.get('target_episodes')!=target_episodes_total:
                dataset['target_episodes']=target_episodes_total;atomic_json(path/'dataset.json',dataset)
            cfg['fps'] = float(cfg.get('fps',30))
            if not 1<=cfg['fps']<=30: raise ValueError('FPS: 1–30')
            cfg['episode_seconds'] = float(cfg.get('episode_seconds',30))
            if not 0<cfg['episode_seconds']<=600: raise ValueError('Длительность: 0–600 секунд')
            cfg['idle_timeout_seconds'] = float(cfg.get('idle_timeout_seconds',120))
            if not 1<=cfg['idle_timeout_seconds']<=600: raise ValueError('Таймаут паузы: 1–600 секунд')
            cfg['minimum_free_gb'] = max(1.,float(cfg.get('minimum_free_gb',2.)))
            cfg['preparation_seconds']=float(cfg.get('preparation_seconds',0))
            if not 0<=cfg['preparation_seconds']<=120:raise ValueError('Подготовка сцены: 0–120 секунд')
            cfg['auto_annotate']=cfg.get('auto_annotate',False)
            if not isinstance(cfg['auto_annotate'],bool):raise ValueError('auto_annotate должен быть bool')
            cfg['sam_live_preview']=cfg.get('sam_live_preview',False)
            if not isinstance(cfg['sam_live_preview'],bool):raise ValueError('sam_live_preview должен быть bool')
            cfg['auto_advance']=cfg.get('auto_advance',False)
            if not isinstance(cfg['auto_advance'],bool):raise ValueError('auto_advance должен быть bool')
            if not isinstance(cfg.get('keep_open',False),bool):raise ValueError('keep_open должен быть bool')
            assert_disk_space(int(cfg['minimum_free_gb']*1024**3),path)
            if dataset['source']=='hardware':
                if not bool(cfg.get('explicit_user_start')):
                    raise PermissionError('Для реального робота нужен явный Start в UI')
                # Preflight before any device open, model import, or source thread creation.
                hardware_settings(cfg)
                assert_hardware_available(hardware_paths(cfg))
                source = HardwareSource(cfg,camera_provider=camera_provider) if camera_provider is not None else HardwareSource(cfg)
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
                            'hardware_open':False,'completed_before_session':completed_before_session,
                            'completed_in_session':0,'target_episodes':cfg['episode_count'],'target_episodes_total':target_episodes_total,
                            'started_at':utcnow(),'updated_at':utcnow(),'settings':cfg}
            self._persist()
            self.thread = threading.Thread(target=self._run,args=(path,dataset,cfg,session_id),
                                           name='so101-ai-recorder',daemon=True)
            self.thread.start()
            return self.status()

    def command(self,action):
        if action not in ('end','rerecord','stop','next','begin','abort'): raise ValueError('Неизвестная команда записи')
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
        cameras=dataset_cameras(dataset)
        metadata = {'episode_id':episode_id,'dataset_id':dataset['dataset_id'],'session_id':session_id,
                    'source':dataset['source'],'task':dataset['task'],'test_only':dataset['source']=='synthetic_test',
                    'fps_requested':cfg['fps'],'camera_mode':dataset.get('camera_mode','two_camera'),'cameras':list(cameras),'provenance':provenance,
                    'calibration':dataset['calibration'],'operator_labels':{},'scan_history_available':False,
                    'recording_protocol':dataset.get('settings',{}).get('recording_protocol','standard'),
                    'smooth_recording_actions':bool(cfg.get('smooth_recording_actions',False)),
                    'max_speed_degrees_s':float(cfg.get('max_speed_degrees_s',45.))}
        writer = EpisodeWriter(path,metadata,on_preview=self._preview)
        self.writer = writer
        self._update(state='recording',episode_id=episode_id,frames_written=0,frames_enqueued=0,episode_start_monotonic_ns=time.monotonic_ns())
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
        writer=None;last_complete=None;completed=0;final_state='stopped';telemetry=None
        from .resources import dataset_lease
        data_lease=dataset_lease(path);data_acquired=False
        recording_lease=ResourceLease('recording');lease_acquired=False
        frame_id=0;episode_start=None;idle_start=None;preparation_deadline=None
        def prepare():
            nonlocal preparation_deadline,idle_start
            preparation_deadline=time.monotonic()+cfg['preparation_seconds'];idle_start=None
            self._update(state='preparing',preparation_deadline_ns=int(preparation_deadline*1e9),
                         preparation_seconds=cfg['preparation_seconds'],message='Подготовьте сцену. Пробел — начать сейчас')
        def begin():
            nonlocal writer,frame_id,episode_start,preparation_deadline,idle_start
            writer=self._new_writer(path,dataset,session_id,cfg,provenance)
            frame_id=0;episode_start=time.monotonic();preparation_deadline=None;idle_start=None
        try:
            recording_lease.__enter__();lease_acquired=True
            data_lease.__enter__();data_acquired=True
            provenance=self.source.start(explicit_user_start=bool(cfg.get('explicit_user_start')),stop=self.stop_event.is_set)
            self._version(path,session_id,provenance=provenance)
            self._update(hardware_open=dataset['source']=='hardware',provenance=provenance)
            if self.telemetry_factory:
                telemetry=self.telemetry_factory(self,path,dataset,cfg,session_id);self.telemetry=telemetry
            if self.on_session_start and cfg['sam_live_preview'] and dataset['source']=='hardware':
                try:self._update(sam_observer=self.on_session_start(path,session_id))
                except Exception as exc:self._update(annotation_error=str(exc))
            if cfg['preparation_seconds']:prepare()
            else:begin()
            deadline=time.monotonic()
            while True:
                action=None
                try:action=self.commands.get_nowait()
                except queue.Empty:pass
                if action in ('stop','abort') or self.stop_event.is_set():
                    action=self.requested_stop_action or action
                    self._update(state='stopping');self._stop_source()
                    if writer:
                        result=self._finish_writer(writer,status='incomplete' if action=='abort' else 'complete',
                            reason='operator_abort' if action=='abort' else 'operator_stop')
                        if result['status']=='complete':completed+=1
                        self._version(path,session_id,result);self._update(last_episode=result,frames_written=result['frames'],frames_enqueued=result.get('writer_enqueued',0),writer_queue=0,writer_performance=result.get('writer_performance',{}))
                        writer=None;self.writer=None
                    break
                if action=='rerecord':
                    if writer:
                        result=self._finish_writer(writer,status='rerecorded',reason='operator_rerecord')
                        self._version(path,session_id,result);writer=None;self.writer=None
                    elif last_complete:
                        old=Path(last_complete['path'])
                        if old.exists():
                            info=json.loads((old/'episode.json').read_text())
                            info.update(status='superseded',reason='operator_rerecord',superseded_at=utcnow())
                            atomic_json(old/'episode.json',info);os.replace(old,path/'quarantine'/old.name)
                            completed=max(0,completed-1);self._version(path,session_id,info)
                            self._update(completed_in_session=completed)
                        last_complete=None
                    if not self.stop_event.is_set():
                        if cfg['preparation_seconds']:prepare()
                        else:begin()
                elif action in ('next','begin') and writer is None:
                    if completed>=cfg['episode_count']:
                        cfg['episode_count']=completed+1;self._update(target_episodes=cfg['episode_count'])
                    if action=='begin' or preparation_deadline is not None or not cfg['preparation_seconds']:begin()
                    else:prepare()
                elif action=='end' and writer:
                    self._update(state='saving')
                    result=self._finish_writer(writer,status='complete',reason='operator_end')
                    if result['status']!='complete':raise RuntimeError('Эпизод не завершён: '+str(result.get('reason')))
                    completed+=1;last_complete=result;self._version(path,session_id,result)
                    writer=None;self.writer=None;idle_start=time.monotonic()
                    self._update(state='ready',completed_in_session=completed,last_episode=result,
                                 frames_written=result['frames'],frames_enqueued=result.get('writer_enqueued',0),writer_queue=0,writer_performance=result.get('writer_performance',{}),episode_elapsed_s=result.get('duration_s',0),
                                 message='Эпизод сохранён. Пробел — следующий; R — перезаписать; Esc — завершить сессию')
                    if completed>=cfg['episode_count'] and not cfg.get('keep_open',False):break
                    if completed<cfg['episode_count'] and cfg['auto_advance']:prepare()
                if self.stop_event.is_set():pass
                elif writer is None and preparation_deadline is not None and time.monotonic()>=preparation_deadline:begin()
                elif writer is None and idle_start and time.monotonic()-idle_start>cfg['idle_timeout_seconds']:
                    self._update(message='Сессия завершена по таймауту паузы');break
                else:
                    current=time.monotonic()
                    if current<deadline:self.stop_event.wait(min(.05,deadline-current))
                    else:
                        if writer:
                            writer.check_ready()
                            frame_id+=1;sample=self.source.sample(frame_id)
                            sample['control_lateness_ms']=max(0.,(current-deadline)*1000);writer.put(sample)
                            if frame_id%max(1,int(cfg['fps']))==0:
                                assert_disk_space(int(cfg['minimum_free_gb']*1024**3),path)
                                # status() reads these counters directly from
                                # the live writer.  Persisting runtime JSON on
                                # the control thread used to issue an fsync
                                # once per second and could freeze commands
                                # behind a busy image writer.
                            if time.monotonic()-episode_start>=cfg['episode_seconds']:self.commands.put('end')
                        else:
                            if hasattr(self.source,'control_sample'):self.source.control_sample()
                            else:self.source.sample(0)
                        deadline=current+1/cfg['fps']
        except Exception as exc:
            stopping=isinstance(exc,InterruptedError) and self.stop_event.is_set()
            final_state='stopped' if stopping else 'failed';self._stop_source()
            if not stopping:
                log=self.root/'logs'/('recording-'+session_id+'.log');log.parent.mkdir(exist_ok=True);log.write_text(traceback.format_exc())
                self._update(error=f'{type(exc).__name__}: {exc}',traceback_path=str(log))
            if writer:
                try:
                    result=writer.finish(status='complete' if stopping else 'incomplete',reason='operator_stop' if stopping else str(exc),timeout=0 if writer.metadata.get('writer_pending') else 30)
                    if not result.get('writer_pending'):self._version(path,session_id,result)
                    if result['status']=='complete':completed+=1
                    self._update(last_episode=result)
                except Exception:
                    self._update(writer_error=traceback.format_exc())
        finally:
            stop_errors=self._stop_source()
            if stop_errors:final_state='failed'
            if data_acquired:data_lease.__exit__(None,None,None)
            if lease_acquired:recording_lease.__exit__(None,None,None)
            self.writer=None
            self._update(state=final_state,active=False,hardware_open=False,completed_in_session=completed,
                stopped_at=utcnow(),stop_errors=stop_errors,
                hardware_stop_state='torque_disable_unconfirmed_operator_check_required' if stop_errors else 'devices_closed')
            if telemetry:telemetry.finish('FAILED' if final_state=='failed' else 'FINISHED')
            if self.on_session_complete and cfg['auto_annotate'] and dataset['source']=='hardware':
                target_total=cfg.get('target_episodes_total',cfg['episode_count'])
                completed_total,target_total,annotation_due=annotation_collection_progress(path,target_total)
                if annotation_due:
                    try:self._update(auto_annotation=self.on_session_complete(path,session_id),annotation_deferred=None)
                    except Exception as exc:self._update(annotation_error=str(exc))
                else:self._update(annotation_deferred={'completed_episodes':completed_total,'target_episodes':target_total,'reason':'collection_in_progress'})

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
                    frames=path/'frames.jsonl';rows=[];invalid_lines=0
                    if frames.exists():
                        for line in frames.read_text().splitlines():
                            try:rows.append(json.loads(line))
                            except ValueError:invalid_lines+=1
                        meta.update(frames=len(rows),frames_sha256=_hash_file(frames),invalid_json_lines=invalid_lines)
                        if rows:meta.update(first_timestamp_ns=rows[0].get('timestamp_ns'),last_timestamp_ns=rows[-1].get('timestamp_ns'))
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
