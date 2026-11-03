"""Disk-backed independent subprocess jobs; browser has no ownership of jobs."""
import json,os,subprocess,sys,time,uuid
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]; DIR=ROOT/'runtime/jobs'; DIR.mkdir(parents=True,exist_ok=True)
def atomic(path,obj):
    tmp=path.with_suffix('.tmp'); tmp.write_text(json.dumps(obj,ensure_ascii=False,indent=2,default=str)); os.replace(tmp,path)
def submit(kind,dataset,options=None):
    jid=uuid.uuid4().hex; d=DIR/jid;d.mkdir(); obj={'id':jid,'kind':kind,'dataset':str(dataset),'options':options or {},'state':'queued','created_at':time.time()}; atomic(d/'job.json',obj)
    log=(ROOT/'logs'/('job-'+jid+'.log')).open('a'); proc=subprocess.Popen([sys.executable,'-m','so101_lab.worker',jid],cwd=ROOT,stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,start_new_session=True);log.close();obj['pid']=proc.pid
    (d/'pid').write_text(str(proc.pid));return obj
def list_jobs():
    result=[]
    for p in DIR.glob('*/job.json'):
        obj=json.loads(p.read_text());pid=p.parent/'pid'
        if pid.exists(): obj['pid']=int(pid.read_text())
        if obj['state'] in ('running','waiting_resources','queued') and pid.exists() and not (Path('/proc')/pid.read_text()).exists(): obj['state']='interrupted';obj['error']='Worker stopped; use Resume. Hardware actions never automatically retried.'
        result.append(obj)
    return sorted(result,key=lambda x:x['created_at'],reverse=True)
def stop(jid):
    if not (DIR/jid/'job.json').exists(): raise ValueError('Unknown job')
    (DIR/jid/'stop').touch()
def resume(jid):
    obj=json.loads((DIR/jid/'job.json').read_text())
    if obj['kind']=='policy': raise ValueError('Физическую политику запускайте заново явной кнопкой Start; автоматический resume движения запрещён')
    opts=obj['options']; checkpoint=obj.get('result',{}).get('checkpoint')
    if not checkpoint:
        candidates=sorted((ROOT/'models'/jid/'checkpoints').glob('step_*/checkpoint.json'))
        if candidates: checkpoint=str(candidates[-1].parent)
    if checkpoint: opts['resume_from']=checkpoint
    return submit(obj['kind'],obj['dataset'],opts)
