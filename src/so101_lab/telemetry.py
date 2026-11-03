"""Durable append-first MLflow delivery, training never depends on server uptime."""
import json,time,uuid,threading,os
from pathlib import Path
class DurableMLflow:
    def __init__(self,root,run_name,params=None,tracking_uri='http://127.0.0.1:5020',**kwargs):
        self.path=Path(root)/'runtime/mlflow-spool'/str(uuid.uuid4()); self.path.mkdir(parents=True)
        os.environ.setdefault('MLFLOW_HTTP_REQUEST_TIMEOUT','3'); os.environ.setdefault('MLFLOW_HTTP_REQUEST_MAX_RETRIES','0')
        self.uri=tracking_uri; self.name=run_name; self.run_id=None; self.experiment='SO101 independent lab'; self.lock=threading.Lock(); self.done=threading.Event()
        self._put('params',params or {}); self.thread=threading.Thread(target=self._loop,daemon=True); self.thread.start()
    def _put(self,kind,data):
        p=self.path/(str(time.time_ns())+'-'+uuid.uuid4().hex+'.json'); tmp=p.with_suffix('.tmp'); tmp.write_text(json.dumps({'kind':kind,'data':data,'time':int(time.time()*1000)},default=str)); os.replace(tmp,p)
    def log_metrics(self,metrics,step=0): self._put('metrics',{'metrics':metrics,'step':step})
    def log_artifact(self,path,**kwargs): self._put('artifact',{'path':str(Path(path).resolve())})
    def flush(self):
        import mlflow
        mlflow.set_tracking_uri(self.uri)
        from mlflow.tracking import MlflowClient
        from mlflow.entities import Metric,Param
        with self.lock:
            client=MlflowClient(tracking_uri=self.uri)
            if not self.run_id:
                exp=client.get_experiment_by_name(self.experiment)
                eid=exp.experiment_id if exp else client.create_experiment(self.experiment)
                self.run_id=client.create_run(eid,tags={'mlflow.runName':self.name,'local_spool':str(self.path)}).info.run_id
                (self.path/'run_id').write_text(self.run_id)
            for p in sorted(self.path.glob('*.json')):
                e=json.loads(p.read_text()); d=e['data']
                if e['kind']=='params': client.log_batch(self.run_id,params=[Param(str(k),str(v)[:5900]) for k,v in d.items()])
                elif e['kind']=='metrics': client.log_batch(self.run_id,metrics=[Metric(str(k),float(v),e['time'],int(d['step'])) for k,v in d['metrics'].items()])
                elif e['kind']=='artifact':
                    q=Path(d['path'])
                    if q.is_dir(): client.log_artifacts(self.run_id,str(q))
                    else: client.log_artifact(self.run_id,str(q))
                elif e['kind']=='status': client.set_terminated(self.run_id,status=d)
                p.rename(p.with_suffix('.sent'))
    def _loop(self):
        while not self.done.is_set():
            try: self.flush()
            except Exception as e: (self.path/'last_error.txt').write_text(str(e))
            self.done.wait(5)
    def close(self,status='FINISHED'):
        self._put('status',status.upper()); self.done.set()
        try: self.flush()
        except Exception as e: (self.path/'last_error.txt').write_text(str(e))
