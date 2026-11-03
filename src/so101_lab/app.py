import asyncio,json,time,os
from pathlib import Path
from contextlib import asynccontextmanager
from fastapi import FastAPI,HTTPException,Request,WebSocket,WebSocketDisconnect
from fastapi.responses import FileResponse,Response
from fastapi.staticfiles import StaticFiles
from .resources import ROOT,snapshot
from . import jobs

manager=None
@asynccontextmanager
async def lifespan(app):
    global manager
    from .recording import RecordingManager
    manager=RecordingManager(root=ROOT)
    yield
    try: manager.command('stop')
    except Exception: pass
app=FastAPI(title='SO-101 Learning Lab',lifespan=lifespan)
@app.middleware('http')
async def origin_guard(request,call_next):
    if request.method in ('POST','PUT','DELETE'):
        from urllib.parse import urlparse
        origin=request.headers.get('origin')
        if origin and urlparse(origin).netloc!=request.headers.get('host'):return Response('Cross origin write rejected',403)
    return await call_next(request)
def status():
    return {'resources':snapshot(),'recording':manager.status() if manager else {},'jobs':jobs.list_jobs(),'datasets':manager.list_datasets() if manager else [],'checkpoints':[str((p.parent/'pretrained_model/config.json').relative_to(ROOT)) for p in (ROOT/'models').rglob('checkpoint.json') if (p.parent/'pretrained_model/config.json').is_file()],'policy_ready':False}
@app.get('/api/status')
def api_status():return status()
@app.post('/api/datasets')
async def create(request:Request):
    try:return manager.create_dataset(**await request.json())
    except Exception as e:raise HTTPException(400,str(e))
@app.post('/api/record/start')
async def start(request:Request):
    data=await request.json()
    try:return manager.start(data.pop('dataset_id'),explicit_user_start=True,**{k:v for k,v in data.items() if k!='explicit_user_start'})
    except Exception as e:raise HTTPException(409,str(e))
@app.post('/api/record/{action}')
def record_action(action:str):
    try:return manager.command(action)
    except Exception as e:raise HTTPException(409,str(e))
@app.get('/api/frame/{camera}')
def frame(camera:str):
    data=manager.latest_frame(camera)
    if data is None:raise HTTPException(404,'Камера не открыта; предпросмотр только во время запущенной сессии')
    return Response(data,media_type='image/png',headers={'Cache-Control':'no-store'})
@app.get('/api/files/{path:path}')
def data_file(path:str):
    p=(ROOT/'data'/path).resolve()
    if not p.is_relative_to((ROOT/'data').resolve()) or not p.is_file():raise HTTPException(404)
    return FileResponse(p)
@app.get('/api/episodes/{dataset_id}')
def episodes(dataset_id:str):
    p=(ROOT/'data'/dataset_id).resolve()
    if not p.is_relative_to((ROOT/'data').resolve()):raise HTTPException(400)
    return [{'id':f.parent.name,'metadata':json.loads(f.read_text()),'frames':[json.loads(x) for x in (f.parent/'frames.jsonl').read_text().splitlines()]} for f in p.glob('episodes/*/episode.json')]
def dataset_path(dataset_id):
    return manager._dataset_path(dataset_id)

@app.get('/api/training/catalog/{dataset_id}')
def training_catalog(dataset_id:str):
    from .training_options import catalog
    try:
        meta,episodes=catalog(dataset_path(dataset_id))
        return {'source':meta.get('source'),'episodes':episodes}
    except (ValueError,FileNotFoundError) as e:raise HTTPException(422,str(e))

@app.post('/api/training/preview')
async def training_preview(request:Request):
    from .training_options import preview
    from starlette.concurrency import run_in_threadpool
    try:
        d=await request.json()
        return await run_in_threadpool(preview,dataset_path(d['dataset_id']),d.get('options',{}))
    except (ValueError,FileNotFoundError,KeyError,TypeError) as e:raise HTTPException(422,str(e))

@app.post('/api/jobs')
async def submit(request:Request):
    from .training_options import preview
    from starlette.concurrency import run_in_threadpool
    try:
        d=await request.json();p=dataset_path(d['dataset_id']);kind=d['kind'];opts=d.get('options',{})
        if kind not in ('train','annotate'):raise ValueError('Неизвестный тип задачи')
        if kind=='train':
            if manager.status().get('active') and manager.status().get('dataset_id')==d['dataset_id']:raise ValueError('Сначала завершите запись выбранного датасета')
            check=await run_in_threadpool(preview,p,opts)
            opts=check['options'];opts['expected_split_sha256']=check['split']['sha256']
            return jobs.submit(kind,p,opts)
        # No train-only/technical options reach annotation model constructors.
        from .annotations import SAM3Segmenter
        allowed={'device','prompts','confidence_threshold'}
        if set(opts)-allowed:raise ValueError('Неизвестные параметры SAM')
        if opts.get('device','cuda') not in ('cpu','cuda'):raise ValueError('Device должен быть cpu или cuda')
        if 'prompts' in opts and not isinstance(opts['prompts'],dict):raise ValueError('Подсказки SAM должны быть словарём cube/box')
        import math
        if not math.isfinite(float(opts.get('confidence_threshold',.5))):raise ValueError('SAM threshold должен быть конечным')
        SAM3Segmenter(opts)  # validates only; no model load
        return jobs.submit(kind,p,opts)
    except (ValueError,FileNotFoundError,KeyError,TypeError) as e:raise HTTPException(422,str(e))
@app.post('/api/jobs/{jid}/{action}')
def job_command(jid:str,action:str):
    if len(jid)!=32 or not jid.isalnum():raise HTTPException(400)
    try:
        if action=='stop':jobs.stop(jid);return {'requested':'stop'}
        if action=='resume':return jobs.resume(jid)
    except (ValueError,FileNotFoundError) as e:raise HTTPException(422,str(e))
    raise HTTPException(400)
@app.post('/api/policy/{action}')
async def policy_action(action:str,request:Request):
    data=await request.json()
    if action=='stop':
        for j in jobs.list_jobs():
            if j['kind']=='policy' and j['state'] in ('running','queued'): jobs.stop(j['id'])
        return {'requested':'stop'}
    checkpoint=(ROOT/data.get('checkpoint','')).resolve()
    if checkpoint.name=='config.json': checkpoint=checkpoint.parent
    if not checkpoint.is_relative_to(ROOT/'models') or not (checkpoint/'config.json').exists():raise HTTPException(400,'Выберите сохранённый checkpoint')
    if action not in ('load','start'):raise HTTPException(400)
    opts={'device':'cuda','task':data.get('task',''),'explicit_user_start':action=='start'}
    if action=='start':
        from .resources import assert_hardware_available,assert_compute_available
        from .hardware import hardware_paths
        try: assert_hardware_available(hardware_paths());assert_compute_available('cuda')
        except Exception as e: raise HTTPException(409,str(e))
    return jobs.submit('policy' if action=='start' else 'policy_load',checkpoint,opts)
@app.websocket('/ws')
async def websocket(ws:WebSocket):
    await ws.accept()
    try:
        while True:await ws.send_json(status());await asyncio.sleep(2)
    except (WebSocketDisconnect,RuntimeError):pass
@app.get('/')
def index():return FileResponse(ROOT/'static/index.html')
app.mount('/static',StaticFiles(directory=ROOT/'static'),name='static')
