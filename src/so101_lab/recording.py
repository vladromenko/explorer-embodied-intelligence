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
        self.thread = threading.Thread(target=self._run,name='so101-ai-raw-writer',daemon=True)
        self.thread.start()
        self.closed = False

    def put(self, sample):
        if self.error: raise RuntimeError('Raw writer failed: '+self.error)
        try: self.queue.put_nowait(sample)
        except queue.Full: raise RuntimeError('Raw writer queue overflow: запись остановлена, motion не повторяется')
        self.enqueued += 1

    def _run(self):
        from PIL import Image
        try:
            while True:
                sample = self.queue.get()
                if sample is None: break
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

    def finish(self, *, status='complete', reason=None):
        if self.closed: return self.metadata
        self.closed = True
        # Bounded queue: finish drains raw writes, control has already stopped producing.
        while self.thread.is_alive():
            try: self.queue.put(None,timeout=.1); break
            except queue.Full: pass
        self.thread.join(timeout=30)
        if self.thread.is_alive():
            status,reason = 'incomplete','raw writer did not drain within 30s'
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
        episode_id = 'ep_'+time.strftime('%Y%m%dT%H%M%S')+'_'+uuid.uuid4().hex[:10]
        metadata = {'episode_id':episode_id,'dataset_id':dataset['dataset_id'],'session_id':session_id,
                    'source':dataset['source'],'task':dataset['task'],'test_only':dataset['source']=='synthetic_test',
                    'fps_requested':cfg['fps'],'camera_mode':'two_camera_raw','provenance':provenance,
                    'calibration':dataset['calibration'],'operator_labels':{},'scan_history_available':False}
        writer = EpisodeWriter(path,metadata,on_preview=self._preview)
        self.writer = writer
        self._update(state='recording',episode_id=episode_id,frames_written=0,frames_enqueued=0)
        return writer

    def _preview(self,camera,data):
        with self.lock: self.preview[camera] = data

    def _version(self,path,session_id,episode=None,provenance=None):
        # Called only by the recording worker. Dataset updates are atomic.
        meta = json.loads((path/'dataset.json').read_text())
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
        recording_lease = ResourceLease('recording')
        lease_acquired = False
        try:
            recording_lease.__enter__(); lease_acquired=True
            provenance = self.source.start(explicit_user_start=bool(cfg.get('explicit_user_start')))
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
                    if writer:
                        result = writer.finish(status='incomplete' if action=='abort' else 'complete',
                                               reason='operator_abort' if action=='abort' else 'operator_stop')
                        if result['status']=='complete': completed+=1
                        self._version(path,session_id,result)
                        self._update(last_episode=result)
                        writer = None; self.writer = None
                    break
                if action=='rerecord':
                    if writer:
                        result = writer.finish(status='rerecorded',reason='operator_rerecord')
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
                    writer = self._new_writer(path,dataset,session_id,cfg,provenance)
                    frame_id=0; episode_start=time.monotonic(); idle_start=None; deadline=time.monotonic()
                elif action=='next' and not writer:
                    writer = self._new_writer(path,dataset,session_id,cfg,provenance)
                    frame_id=0; episode_start=time.monotonic(); idle_start=None; deadline=time.monotonic()
                elif action=='end' and writer:
                    result = writer.finish(status='complete',reason='operator_end')
                    if result['status']=='complete': completed+=1; last_complete=result
                    else: raise RuntimeError('Эпизод не завершён: '+str(result.get('reason')))
                    self._version(path,session_id,result)
                    writer=None; self.writer=None
                    self._update(state='ready',completed_in_session=completed,last_episode=result)
                    if completed>=cfg['episode_count']: break
                    idle_start=time.monotonic()
                if not writer:
                    if idle_start and time.monotonic()-idle_start>cfg['idle_timeout_seconds']:
                        self._update(message='Пауза превысила таймаут; hardware закрыт, нажмите Start для продолжения')
                        break
                    self.stop_event.wait(.02)
                    continue
                # A deadline overrun never results in rapid catch-up commands.
                current = time.monotonic()
                if current<deadline:
                    self.stop_event.wait(min(.05,deadline-current)); continue
                frame_id+=1
                sample = self.source.sample(frame_id)
                sample['control_lateness_ms'] = max(0.,(current-deadline)*1000)
                writer.put(sample)
                deadline = max(deadline+1/cfg['fps'],time.monotonic())
                if frame_id%max(1,int(cfg['fps']))==0:
                    assert_disk_space(int(cfg['minimum_free_gb']*1024**3),path)
                    self._update(frames_written=writer.frames,frames_enqueued=writer.enqueued,
                                 episode_elapsed_s=time.monotonic()-episode_start)
                if time.monotonic()-episode_start>=cfg['episode_seconds']:
                    self.commands.put('end')
        except Exception as exc:
            final_state='failed'
            error=traceback.format_exc()
            log=self.root/'logs'/('recording-'+session_id+'.log'); log.parent.mkdir(exist_ok=True)
            log.write_text(error)
            self._update(error=f'{type(exc).__name__}: {exc}',traceback_path=str(log))
            if writer:
                try:
                    result=writer.finish(status='incomplete',reason=str(exc)); self._version(path,session_id,result)
                    self._update(last_episode=result)
                except Exception:
                    with log.open('a') as out: out.write('\n'+traceback.format_exc())
        finally:
            try:
                stop_errors = self.source.close() if self.source else []
            except Exception as exc:
                stop_errors = [f'source close failed: {type(exc).__name__}: {exc}']
                final_state = 'failed'
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
                if owner and meta.get('writer_process_start_ticks'):
                    try:
                        if Path(f'/proc/{int(owner)}/stat').read_text().split()[21] == meta['writer_process_start_ticks']:
                            continue  # Never quarantine a live writer from another server process.
                    except (OSError,ValueError,IndexError): pass
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
