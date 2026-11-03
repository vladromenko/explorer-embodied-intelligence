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
@app.post('/api/jobs')
async def submit(request:Request):
    d=await request.json();p=(ROOT/'data'/d['dataset_id']).resolve()
    if not p.is_relative_to((ROOT/'data').resolve()) or not (p/'dataset.json').exists():raise HTTPException(400,'Unknown dataset')
    if d['kind'] not in ('train','annotate'):raise HTTPException(400)
    return jobs.submit(d['kind'],p,d.get('options',{}))
@app.post('/api/jobs/{jid}/{action}')
def job_command(jid:str,action:str):
    if len(jid)!=32 or not jid.isalnum():raise HTTPException(400)
    if action=='stop':jobs.stop(jid);return {'requested':'stop'}
    if action=='resume':return jobs.resume(jid)
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
