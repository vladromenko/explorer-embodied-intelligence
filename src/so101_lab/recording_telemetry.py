"""Recording metrics sampled off the control thread; durable delivery to MLflow."""
import json,os,shutil,threading,time,subprocess,sys,hashlib,marshal
from pathlib import Path
from .jobs import atomic
from .telemetry import DurableMLflow


class RecordingTelemetry:
    def __init__(self,root,manager,path,dataset,settings,session_id,tracking_uri='http://127.0.0.1:5020'):
        self.root=Path(root);self.manager=manager;self.dataset_path=Path(path);self.dataset=dataset
        self.settings=settings;self.session_id=session_id;self.uri=tracking_uri;self.done=threading.Event();self.status='FINISHED'
        self.thread=threading.Thread(target=self._run,name='so101-recording-metrics',daemon=True);self.thread.start()

    def finish(self,status):
        self.status=status;self.final_snapshot=self.manager.status();self.done.set()

    def _run(self):
        folder=self.dataset_path/'sessions'/self.session_id;folder.mkdir(parents=True,exist_ok=True)
        meta={'session_id':self.session_id,'owner_pid':os.getpid(),'owner_start_ticks':Path('/proc/self/stat').read_text().split()[21],'status':'RUNNING'}
        try:
            try:revision=subprocess.check_output(['git','rev-parse','HEAD'],cwd=self.root,text=True).strip()
            except Exception:revision='unknown'
            params={'purpose':'recording','session_id':self.session_id,'dataset_id':self.dataset['dataset_id'],
                'dataset_name':self.dataset['name'],'task':self.dataset['task'],'source':self.dataset['source'],
                'technical_test':self.dataset['source']!='hardware','code_commit':revision,
                'dataset_version_start':self.dataset.get('version',0),'fps_requested':self.settings['fps'],
                'preparation_seconds':self.settings['preparation_seconds'],'image_encoding':'lossless PNG0, two parallel camera writers'}
            params['recorder_runtime_code_sha256']=hashlib.sha256(marshal.dumps(self.manager._run.__func__.__code__)).hexdigest()
            for role,entry in self.dataset.get('calibration',{}).get('files',{}).items():params[role+'_calibration_sha256']=entry['sha256']
            telemetry=DurableMLflow(self.root,'Запись · '+self.dataset['name']+' · '+self.session_id,params=params,tracking_uri=self.uri,delivery_thread=False)
            atomic(telemetry.path/'recording.owner',{'pid':os.getpid(),'start_ticks':meta['owner_start_ticks']})
            logs=self.root/'logs';logs.mkdir(parents=True,exist_ok=True)
            with (logs/('recording-mlflow-'+self.session_id+'.log')).open('a') as log:
                delivery=subprocess.Popen([sys.executable,'-m','so101_lab.recording_delivery',str(telemetry.path)],stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
            meta['delivery_pid']=delivery.pid
            meta['spool']=str(telemetry.path);atomic(folder/'telemetry.json',meta)
            last_count=0;last_episode=None;last_time=time.monotonic();step=0
            while True:
                state=self.manager.status()
                if state.get('session_id')!=self.session_id:break
                now=time.monotonic();episode=state.get('episode_id')
                count=state.get('frames_enqueued',0)
                delta=max(0,count-(last_count if last_episode==episode else 0))
                writer=state.get('writer_performance') or {}
                mem={line.split(':')[0]:int(line.split()[1])*1024 for line in Path('/proc/meminfo').read_text().splitlines()}
                metrics={'recording/fps_observed':delta/max(.001,now-last_time),'recording/frames_written':state.get('frames_written',0),
                    'recording/frames_enqueued':count,'recording/writer_queue':state.get('writer_queue',0),
                    'recording/writer_queue_peak':writer.get('peak_queue',0),'recording/bytes_written':writer.get('bytes_written',0),
                    'recording/is_recording':int(state.get('state')=='recording'),'recording/episodes_saved':state.get('completed_in_session',0),
                    'recording/preparation_remaining_s':state.get('preparation_remaining_s',0),
                    'resources/memory_available_bytes':mem['MemAvailable'],'resources/disk_free_bytes':shutil.disk_usage(self.dataset_path).free}
                observer=state.get('sam_observer') or {}
                if observer.get('id'):
                    try:
                        from .jobs import DIR
                        job=json.loads((DIR/observer['id']/'job.json').read_text())
                        preview=job.get('result') or {}
                        if preview.get('sample_count') is not None:metrics['sam/preview_snapshots_completed']=preview['sample_count']
                        if preview.get('detected_objects') is not None:metrics['sam/objects_in_last_preview']=preview['detected_objects']
                    except (OSError,ValueError):pass
                source=self.manager.source
                for camera,capture in getattr(source,'cameras',{}).items():
                    fps=capture.info.get('fps_observed_recent')
                    if fps is not None:metrics['camera/'+camera+'/fps_observed']=fps
                    try:
                        frame=capture.latest_before(time.monotonic_ns())
                        if frame:metrics['camera/'+camera+'/frame_age_ms']=(time.monotonic_ns()-frame['timestamp_ns'])/1e6
                    except RuntimeError:metrics['camera/'+camera+'/failed']=1
                telemetry.log_metrics(metrics,step=step)
                if (telemetry.path/'run_info').exists():
                    info=json.loads((telemetry.path/'run_info').read_text());telemetry.run_id=info['run_id'];telemetry.experiment_id=info['experiment_id']
                record={'spool':str(telemetry.path),'run_id':telemetry.run_id,'experiment_id':telemetry.experiment_id,'tracking_uri':self.uri,'state':'connected' if telemetry.run_id else 'buffering'}
                if self.manager.status().get('session_id')==self.session_id:self.manager._update(mlflow=record)
                last_time=now;last_count=count;last_episode=episode;step+=1
                if self.done.wait(1):break
            episodes=[]
            for parent in ('episodes','quarantine'):
                for file in (self.dataset_path/parent).glob('*/episode.json'):
                    info=json.loads(file.read_text())
                    if info.get('session_id')==self.session_id:episodes.append({k:info.get(k) for k in ('episode_id','status','frames','frames_sha256','reason','writer_performance')})
            summary={'session_id':self.session_id,'status':self.status,'source':self.dataset['source'],
                'dataset_metadata':json.loads((self.dataset_path/'dataset.json').read_text()),'episodes':episodes,
                'final_recording_state':getattr(self,'final_snapshot',state),'raw_images_uploaded':False}
            atomic(folder/'summary.json',summary);telemetry.log_artifact(folder/'summary.json')
            telemetry.log_metrics({'recording/final_frames_saved':sum(e.get('frames',0) for e in episodes),
                'recording/final_complete_episodes':sum(e['status']=='complete' for e in episodes)},step=step)
            telemetry.close(self.status,flush=False);meta.update(status=self.status,run_id=telemetry.run_id);atomic(folder/'telemetry.json',meta)
            (telemetry.path/'recording.finished').touch()
        except Exception as exc:
            atomic(folder/'telemetry-error.json',{'error':str(exc),'data_preserved':True})
            if 'telemetry' in locals():
                telemetry.close('FAILED',flush=False)
                (telemetry.path/'recording.finished').touch()
                meta.update(status='FAILED',error=str(exc),spool=str(telemetry.path));atomic(folder/'telemetry.json',meta)
            if self.manager.status().get('session_id')==self.session_id:self.manager._update(mlflow={'state':'error','error':str(exc),'data_preserved':True})


def recover_recording_telemetry(root):
    def recover():
        for file in (Path(root)/'data').glob('*/sessions/*/telemetry.json'):
            try:
                meta=json.loads(file.read_text())
                if meta.get('status')=='RUNNING':
                    proc=Path('/proc')/str(meta.get('owner_pid'))
                    alive=proc.exists() and (proc/'stat').read_text().split()[21]==meta.get('owner_start_ticks')
                    if not alive:
                        telemetry=DurableMLflow.recover(meta['spool'])
                        summary=file.parent/'interrupted.json';atomic(summary,{'status':'interrupted','motion_resumed':False,'data_recovered_to_quarantine':True})
                        telemetry.log_artifact(summary);telemetry.close('FAILED',flush=False);(telemetry.path/'recording.finished').touch();meta['status']='FAILED';atomic(file,meta)
                        logs=Path(root)/'logs';logs.mkdir(parents=True,exist_ok=True)
                        with (logs/('recovered-recording-mlflow-'+file.parent.name+'.log')).open('a') as log:
                            subprocess.Popen([sys.executable,'-m','so101_lab.recording_delivery',str(telemetry.path)],stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
            except Exception:pass  # Spool remains for the next recovery/replay attempt.
    threading.Thread(target=recover,name='so101-recording-metrics-recovery',daemon=True).start()
