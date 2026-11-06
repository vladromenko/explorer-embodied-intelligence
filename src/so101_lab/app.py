import asyncio,json,time,os,threading
from pathlib import Path
from contextlib import asynccontextmanager
from fastapi import FastAPI,HTTPException,Request,WebSocket,WebSocketDisconnect
from fastapi.responses import FileResponse,Response
from fastapi.staticfiles import StaticFiles
from .resources import ROOT,snapshot
from . import jobs

manager=None
camera_preview=None
teleoperation=None
hardware_control_lock=threading.RLock()
hardware_start_guard=threading.Lock()
hardware_stop_generation=0

def request_hardware_stop():
    global hardware_stop_generation
    with hardware_start_guard:
        hardware_stop_generation+=1
        if teleoperation:teleoperation.stop()
        if manager:manager.command('stop')
    for job in jobs.list_jobs():
        if job['kind']=='policy' and job['state'] in jobs.ACTIVE:jobs.stop(job['id'])
    return teleoperation.status() if teleoperation else {'active':False}

def policy_is_active():
    return any(job['kind']=='policy' and job['state'] in jobs.ACTIVE for job in jobs.list_jobs())
@asynccontextmanager
async def lifespan(app):
    global manager,camera_preview,teleoperation
    from .recording import RecordingManager
    manager=RecordingManager(root=ROOT)
    from .preview import CameraPreview
    camera_preview=CameraPreview()
    from .teleoperation import TeleoperationManager
    teleoperation=TeleoperationManager(root=ROOT)
    yield
    if teleoperation:
        from starlette.concurrency import run_in_threadpool
        teleoperation.stop()
        await run_in_threadpool(teleoperation.wait,15)
    try:
        manager.command('stop')
        from starlette.concurrency import run_in_threadpool
        await run_in_threadpool(manager.wait,15)
        if manager.status().get('active'):(ROOT/'logs/shutdown-warning.log').write_text('Recorder did not finish shutdown within15s; operator hardware STOP check required')
    except Exception as exc:(ROOT/'logs/shutdown-warning.log').write_text(str(exc))
    if camera_preview:
        try:camera_preview.stop()
        except Exception:pass
app=FastAPI(title='SO-101 Learning Lab',lifespan=lifespan)
@app.middleware('http')
async def origin_guard(request,call_next):
    if request.method in ('POST','PUT','DELETE'):
        from urllib.parse import urlparse
        origin=request.headers.get('origin')
        if origin and urlparse(origin).netloc!=request.headers.get('host'):return Response('Cross origin write rejected',403)
    return await call_next(request)
def status():
    checkpoints={}
    for manifest_path in (ROOT/'models').rglob('checkpoint.json'):
        folder=manifest_path.parent;config=folder/'pretrained_model/config.json'
        if config.is_file():
            try:
                manifest=json.loads(manifest_path.read_text())
                if manifest.get('status')=='complete':checkpoints[str(config.relative_to(ROOT))]={'technical_test':manifest.get('technical_test',True),'step':manifest.get('step',0),'policy_type':manifest.get('policy_type')}
            except (OSError,ValueError):pass
    return {'resources':snapshot(),'recording':manager.status() if manager else {},'teleoperation':teleoperation.status() if teleoperation else {'active':False},'jobs':jobs.list_jobs(),'datasets':manager.list_datasets() if manager else [],'checkpoints':list(checkpoints),'checkpoint_details':checkpoints,'policy_ready':any(not m['technical_test'] and m['step']>0 for m in checkpoints.values()),'preview':camera_preview.status() if camera_preview else {'active':False}}
@app.get('/api/preflight')
async def preflight_check():
    from .preflight import run_preflight
    from starlette.concurrency import run_in_threadpool
    result=await run_in_threadpool(run_preflight,manager.status() if manager else {},camera_preview.status() if camera_preview else {},ROOT,teleoperation.status() if teleoperation else {})
    from .jobs import atomic
    atomic(ROOT/'runtime/preflight.json',result)
    return result

@app.get('/api/status')
def api_status():return status()
@app.post('/api/datasets')
async def create(request:Request):
    try:return manager.create_dataset(**await request.json())
    except Exception as e:raise HTTPException(400,str(e))
@app.post('/api/record/start')
async def start(request:Request):
    data=await request.json()
    try:
        did=data.pop('dataset_id')
        generation=hardware_stop_generation
        def begin():
            with hardware_control_lock:
                if policy_is_active():raise ValueError('Сначала остановите политику кнопкой STOP рук перед записью')
                hardware=manager.dataset_info(did)['source']=='hardware'
                if teleoperation and (hardware or teleoperation.status().get('active')):
                    teleoperation.stop_for_recording()
                if hardware and camera_preview:camera_preview.stop()
                with hardware_start_guard:
                    if generation!=hardware_stop_generation:raise ValueError('Запуск отменён кнопкой STOP. Запись не началась')
                    return manager.start(did,explicit_user_start=True,**{k:v for k,v in data.items() if k!='explicit_user_start'})
        from starlette.concurrency import run_in_threadpool
        return await run_in_threadpool(begin)
    except Exception as e:raise HTTPException(409,str(e))

@app.post('/api/teleoperation/{action}')
def teleoperation_action(action:str):
    if teleoperation is None:raise HTTPException(503,'Телеоперация ещё запускается')
    try:
        if action=='stop':return request_hardware_stop()
        if action!='start':raise ValueError('Неизвестная команда телеоперации')
        generation=hardware_stop_generation
        with hardware_control_lock:
            if manager.status().get('active'):raise ValueError('Сначала остановите запись. Она уже управляет руками')
            if policy_is_active():raise ValueError('Сначала остановите политику кнопкой STOP рук перед телеоперацией')
            if teleoperation.status().get('active'):return teleoperation.status()
            with hardware_start_guard:
                if generation!=hardware_stop_generation:raise ValueError('Запуск рук отменён кнопкой STOP')
                return teleoperation.start(explicit_user_start=True)
    except Exception as e:raise HTTPException(409,str(e))

@app.post('/api/preview/{action}')
def preview_action(action:str):
    if camera_preview is None:raise HTTPException(503,'Предпросмотр ещё запускается')
    try:
        if action=='stop':return camera_preview.stop()
        if action!='start':raise ValueError('Неизвестная команда камер')
        with hardware_control_lock:
            if manager.status().get('active'):raise ValueError('Камеры уже используются записью; предпросмотр идёт из неё')
            return camera_preview.start(explicit_user_start=True)
    except Exception as e:raise HTTPException(409,str(e))
@app.post('/api/record/{action}')
def record_action(action:str):
    try:return manager.command(action)
    except Exception as e:raise HTTPException(409,str(e))
@app.get('/api/frame/{camera}')
def frame(camera:str):
    if camera not in ('top','wrist'):raise HTTPException(404)
    try:
        # Never present an old synthetic recording frame as a real live camera.
        recording=manager.status()
        data=None
        if recording.get('active'):
            # During the inter-episode pause keep the actual live view, not the last saved frame.
            source=getattr(manager,'source',None)
            cameras=getattr(source,'cameras',{})
            cam=cameras.get(camera)
            if cam is not None:
                captured=cam.latest_before(time.monotonic_ns())
                if captured is not None and time.monotonic_ns()-captured['timestamp_ns']<1_000_000_000:
                    import io
                    from PIL import Image
                    encoded=io.BytesIO();Image.fromarray(captured['image']).save(encoded,format='JPEG',quality=78);data=encoded.getvalue()
            else:data=manager.latest_frame(camera)
        if data is None and teleoperation:data=teleoperation.latest_frame(camera)
        if data is None and camera_preview:data=camera_preview.latest_frame(camera)
        if data is None:raise HTTPException(404,'Камеры не подключены. Нажмите «Подключить камеры»; моторы не включаются')
        media='image/png' if data.startswith(b'\x89PNG') else 'image/jpeg'
        return Response(data,media_type=media,headers={'Cache-Control':'no-store'})
    except HTTPException:raise
    except Exception as e:raise HTTPException(503,str(e))
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
@app.post('/api/datasets/{dataset_id}/{action}')
async def dataset_action(dataset_id:str,action:str,request:Request):
    try:
        path=dataset_path(dataset_id);options=await request.json()
        if action not in ('validate','finalize','export'):raise ValueError('Неизвестная операция датасета')
        if manager.status().get('active') and manager.status().get('dataset_id')==dataset_id:raise ValueError('Сначала завершите сессию записи')
        if set(options)-{'allow_test_data'}:raise ValueError('Неизвестные параметры операции')
        if not isinstance(options.get('allow_test_data',False),bool):raise ValueError('allow_test_data должен быть bool')
        return jobs.submit(action,path,options)
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
            if j['kind']=='policy' and j['state'] in jobs.ACTIVE: jobs.stop(j['id'])
        return {'requested':'stop'}
    checkpoint=(ROOT/data.get('checkpoint','')).resolve()
    if checkpoint.name=='config.json': checkpoint=checkpoint.parent
    if not checkpoint.is_relative_to(ROOT/'models') or not (checkpoint/'config.json').exists():raise HTTPException(400,'Выберите сохранённый checkpoint')
    if action not in ('load','start'):raise HTTPException(400)
    opts={'device':'cuda','task':data.get('task',''),'explicit_user_start':action=='start'}
    if action=='start':
        from .resources import assert_hardware_available,assert_compute_available
        from .hardware import hardware_paths
        try:
            if (teleoperation and teleoperation.status().get('active')) or manager.status().get('active'):raise ValueError('Сначала остановите телеоперацию/запись перед запуском политики')
            from .features import sha256_file
            manifest=checkpoint.parent/'checkpoint.json'
            for job in jobs.list_jobs():
                result=job.get('result') or {}
                if job['kind']=='policy_load' and job['state']=='completed' and job.get('dataset')==str(checkpoint) and result.get('checkpoint_manifest_sha256')==sha256_file(manifest):
                    if result.get('latency_check',{}).get('latency_within_limit') is False:raise ValueError('Проверка SAM → политики превысила лимит 2 секунды. Движения не запущены. Проверьте ресурсы и повторите проверку либо обучите RGB+state baseline')
                    break
            assert_hardware_available(hardware_paths());assert_compute_available('cuda')
        except Exception as e: raise HTTPException(409,str(e))
        if camera_preview:camera_preview.stop()
    return jobs.submit('policy' if action=='start' else 'policy_load',checkpoint,opts)
@app.websocket('/ws')
async def websocket(ws:WebSocket):
    await ws.accept()
    try:
        while True:await ws.send_json(status());await asyncio.sleep(2)
    except (WebSocketDisconnect,RuntimeError):pass
@app.get('/')
def index():return FileResponse(ROOT/'static/index.html',headers={'Cache-Control':'no-store'})
app.mount('/static',StaticFiles(directory=ROOT/'static'),name='static')
