"""Fsync-first MLflow spool with thread + process delivery exclusion."""
import fcntl,json,time,uuid,threading,os
from pathlib import Path

def _atomic(path,value):
    tmp=path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
    with tmp.open('w') as out:json.dump(value,out,ensure_ascii=False,default=str,allow_nan=False);out.flush();os.fsync(out.fileno())
    os.replace(tmp,path)

class DurableMLflow:
    def __init__(self,root,run_name,params=None,tracking_uri='http://127.0.0.1:5020',**kwargs):
        self.path=Path(root)/'runtime/mlflow-spool'/str(uuid.uuid4());self.path.mkdir(parents=True)
        self.uri=tracking_uri;self.name=run_name;self.run_id=None;self.experiment='SO101 independent lab';self.lock=threading.Lock();self.done=threading.Event()
        _atomic(self.path/'metadata',{'tracking_uri':self.uri,'name':self.name,'experiment':self.experiment})
        self._put('params',params or {});self.thread=threading.Thread(target=self._loop,daemon=True);self.thread.start()
    @classmethod
    def recover(cls,path):
        obj=cls.__new__(cls);obj.path=Path(path);m=json.loads((obj.path/'metadata').read_text()) if (obj.path/'metadata').exists() else {}
        obj.uri=m.get('tracking_uri','http://127.0.0.1:5020');obj.name=m.get('name','recovered-'+obj.path.name);obj.experiment=m.get('experiment','SO101 independent lab');obj.lock=threading.Lock();obj.done=threading.Event();obj.run_id=None
        if (obj.path/'run_id').exists():obj.run_id=(obj.path/'run_id').read_text()
        return obj
    def _put(self,kind,data):_atomic(self.path/(str(time.time_ns())+'-'+uuid.uuid4().hex+'.json'),{'kind':kind,'data':data,'time':int(time.time()*1000)})
    def log_metrics(self,metrics,step=0):self._put('metrics',{'metrics':metrics,'step':step})
    def log_artifact(self,path,**kwargs):self._put('artifact',{'path':str(Path(path).resolve())})
    def flush(self):
        os.environ.setdefault('MLFLOW_HTTP_REQUEST_TIMEOUT','3');os.environ.setdefault('MLFLOW_HTTP_REQUEST_MAX_RETRIES','0')
        with self.lock,(self.path/'delivery.lock').open('a+') as lease:
            try:fcntl.flock(lease,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:return
            import mlflow
            from mlflow.tracking import MlflowClient
            from mlflow.entities import Metric,Param
            mlflow.set_tracking_uri(self.uri);client=MlflowClient(tracking_uri=self.uri)
            if (self.path/'run_id').exists():self.run_id=(self.path/'run_id').read_text()
            if not self.run_id:
                exp=client.get_experiment_by_name(self.experiment);eid=exp.experiment_id if exp else client.create_experiment(self.experiment)
                self.run_id=client.create_run(eid,tags={'mlflow.runName':self.name,'local_spool':str(self.path)}).info.run_id
                temp=self.path/'run_id.tmp';temp.write_text(self.run_id);os.replace(temp,self.path/'run_id')
            for p in sorted(self.path.glob('*.json')):
                e=json.loads(p.read_text());d=e['data']
                if e['kind']=='params':client.log_batch(self.run_id,params=[Param(str(k),str(v)[:5900]) for k,v in d.items()])
                elif e['kind']=='metrics':client.log_batch(self.run_id,metrics=[Metric(str(k),float(v),e['time'],int(d['step'])) for k,v in d['metrics'].items()])
                elif e['kind']=='artifact':
                    q=Path(d['path'])
                    if q.is_dir():client.log_artifacts(self.run_id,str(q))
                    else:client.log_artifact(self.run_id,str(q))
                elif e['kind']=='status':client.set_terminated(self.run_id,status=d)
                else:raise ValueError('Неизвестный тип MLflow события')
                p.rename(p.with_suffix('.sent'))
    def _loop(self):
        while not self.done.is_set():
            try:self.flush()
            except Exception as e:(self.path/'last_error.txt').write_text(str(e))
            self.done.wait(5)
    def close(self,status='FINISHED'):
        self._put('status',status.upper());self.done.set()
        try:self.flush()
        except Exception as e:(self.path/'last_error.txt').write_text(str(e))
