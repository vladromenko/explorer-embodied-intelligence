"""Durable subprocess jobs; no browser owns a recording/training process."""
import copy,fcntl,json,os,re,subprocess,sys,time,traceback,uuid
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2];DIR=ROOT/'runtime/jobs';DIR.mkdir(parents=True,exist_ok=True)
ACTIVE={'running','waiting_resources','queued'}
def atomic(path,obj):
    path=Path(path);tmp=path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
    with tmp.open('w') as f:
        json.dump(obj,f,ensure_ascii=False,indent=2,default=str,allow_nan=False);f.flush();os.fsync(f.fileno())
    os.replace(tmp,path)
def job_dir(jid):
    if not re.fullmatch('[a-f0-9]{32}',jid):raise ValueError('Некорректный job ID')
    d=DIR/jid
    if not (d/'job.json').is_file():raise ValueError('Задача не найдена')
    return d
def alive(jid,pid):
    try:
        p=Path('/proc')/str(pid)
        return jid in (p/'cmdline').read_text() and (p/'stat').read_text().split()[2]!='Z'
    except (OSError,IndexError):return False

def submit(kind,dataset,options=None):
    if kind not in ('train','annotate','scene','sam_watch','policy','policy_load','validate','export','finalize'):raise ValueError('Неизвестный тип задачи')
    dataset=str(dataset);options=copy.deepcopy(options or {})
    # A slow public preflight can make a repeated click look unanswered. Serialize submission and
    # return the existing identical training job instead of queueing another multi-hour run.
    with (DIR/'.submit.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        if kind=='train':
            for path in DIR.glob('*/job.json'):
                try:current=json.loads(path.read_text())
                except (OSError,ValueError):continue
                if current.get('kind')==kind and current.get('state') in ACTIVE and current.get('dataset')==dataset and current.get('options')==options:
                    pid=path.parent/'pid'
                    if pid.exists():current['pid']=int(pid.read_text())
                    return current
        jid=uuid.uuid4().hex;d=DIR/jid;d.mkdir();obj={'id':jid,'kind':kind,'dataset':dataset,'options':options,'state':'queued','created_at':time.time()};atomic(d/'job.json',obj)
        try:
            with (ROOT/'logs'/('job-'+jid+'.log')).open('a') as log:
                proc=subprocess.Popen([sys.executable,'-m','so101_lab.worker',jid],cwd=ROOT,stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
            (d/'pid').write_text(str(proc.pid));obj['pid']=proc.pid
        except Exception as e:
            obj.update(state='failed',error=str(e),traceback=traceback.format_exc());atomic(d/'job.json',obj);raise
        return obj

def list_jobs():
    result=[]
    for p in DIR.glob('*/job.json'):
        try:
            obj=json.loads(p.read_text());pid=p.parent/'pid'
            if pid.exists():obj['pid']=int(pid.read_text())
            if obj['state'] in ACTIVE and obj.get('pid') and not alive(obj['id'],obj['pid']):obj.update(state='interrupted',error='Worker остановлен. Данные/checkpoints сохранены; физические движения автоматически не возобновляются.')
            result.append(obj)
        except (OSError,ValueError,KeyError):
            result.append({'id':p.parent.name,'kind':'unknown','state':'failed','created_at':p.stat().st_mtime,'error':'Повреждён job.json; остальные задачи доступны'})
    return sorted(result,key=lambda x:x['created_at'],reverse=True)
def stop(jid):
    d=job_dir(jid);(d/'stop').touch()
def resume(jid):
    d=job_dir(jid);obj=json.loads((d/'job.json').read_text())
    if obj['kind']=='policy':raise ValueError('Физическую политику запускайте явной кнопкой Start; автоматический resume запрещён')
    if (d/'pid').exists() and alive(jid,int((d/'pid').read_text())):raise ValueError('Задача ещё выполняется; сначала дождитесь остановки')
    opts=copy.deepcopy(obj['options']);checkpoint=obj.get('result',{}).get('checkpoint')
    if not checkpoint:
        base=Path(opts['output_dir'])/jid if opts.get('output_dir') else ROOT/'models'/jid
        candidates=sorted((base/'checkpoints').glob('step_*/checkpoint.json'))
        if candidates:checkpoint=str(candidates[-1].parent)
    if checkpoint:
        manifest=json.loads((Path(checkpoint)/'checkpoint.json').read_text())
        if int(manifest['step'])>=int(opts['steps']):raise ValueError('Обучение завершило заданные steps. Выберите checkpoint в форме Resume и увеличьте итоговый steps')
        opts['resume_from']=checkpoint
    return submit(obj['kind'],obj['dataset'],opts)
