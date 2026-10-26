"""ENGINEERING_GLUE: authenticated local Explorer control and evidence API."""
import asyncio
import csv
import hashlib
import hmac
import json
import secrets
import shutil
import time
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlparse

import httpx
import psutil
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from .config import ROOT, load
from .store import Store

CONFIG = load()
STORE = Store(CONFIG['data_dir'])
STATIC = Path(__file__).parent / 'static'
STATIC.mkdir(exist_ok=True)
CLIENT = httpx.AsyncClient(timeout=4, trust_env=False)
LOGIN_ATTEMPTS = {}


async def poll_nodes():
    async def poll(node):
        try:
            response = await CLIENT.get('http://' + node['host'] + ':' + str(node['port']) + '/inventory',
                headers={'Authorization': 'Bearer ' + node['token']})
            response.raise_for_status()
            payload=response.json()
            STORE.update_node(node['id'],payload)
            capture=payload.get('mobile_observation') or {}
            imported={job['config'].get('episode_id') for job in STORE.jobs(10000) if job['kind']=='mobile_import'}
            for recording_id in capture.get('ready_recording_ids',[]):
                if recording_id not in imported:
                    STORE.create_import_job('mobile_import','02',recording_id)
        except Exception:
            pass
    while True:
        await asyncio.gather(*(poll(node) for node in CONFIG['nodes']))
        await asyncio.sleep(5)


@asynccontextmanager
async def lifespan(app):
    task = asyncio.create_task(poll_nodes())
    yield
    task.cancel()
    await CLIENT.aclose()


app = FastAPI(title='Explorer', docs_url=None, redoc_url=None, lifespan=lifespan)
app.mount('/static', StaticFiles(directory=STATIC), name='static')


@app.middleware('http')
async def authenticate(request, call_next):
    path = request.url.path
    if request.method not in ('GET', 'HEAD', 'OPTIONS'):
        origin = request.headers.get('origin')
        if origin and urlparse(origin).netloc != request.headers.get('host'):
            return JSONResponse({'detail': 'Origin rejected'}, 403)
    if path in ('/api/health', '/api/login') or path.startswith('/static/'):
        response = await call_next(request)
    elif path == '/api/events':
        if not hmac.compare_digest(request.headers.get('authorization', ''), 'Bearer ' + CONFIG['ingest_token']):
            return JSONResponse({'detail': 'Node authentication required'}, 401)
        response = await call_next(request)
    else:
        token = request.cookies.get('explorer_session', '')
        with STORE.connect() as db:
            session = db.execute('SELECT * FROM sessions WHERE token_hash=? AND expires>?',
                (hashlib.sha256(token.encode()).hexdigest(), time.time())).fetchone()
        if session is None:
            return JSONResponse({'detail': 'Authentication required'}, 401) if path.startswith('/api/') else RedirectResponse('/api/login', 303)
        if request.method not in ('GET', 'HEAD', 'OPTIONS') and not hmac.compare_digest(request.headers.get('x-explorer-csrf', ''), session['csrf']):
            return JSONResponse({'detail': 'Request confirmation token missing'}, 403)
        response = await call_next(request)
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Referrer-Policy'] = 'no-referrer'
    response.headers['Cache-Control'] = 'no-store'
    return response


def sign_in(response):
    token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(24)
    with STORE.connect() as db:
        db.execute('DELETE FROM sessions WHERE expires<?', (time.time(),))
        db.execute('INSERT INTO sessions VALUES (?,?,?)', (hashlib.sha256(token.encode()).hexdigest(), time.time() + 86400 * 7, csrf))
    response.set_cookie('explorer_session', token, httponly=True, samesite='strict', max_age=86400 * 7)
    response.set_cookie('explorer_csrf', csrf, httponly=False, samesite='strict', max_age=86400 * 7)
    return response


@app.get('/api/health')
def health():
    return {'ok': True, 'version': '0.1.0', 'commit': (ROOT / 'RELEASE_COMMIT').read_text().strip() if (ROOT / 'RELEASE_COMMIT').exists() else 'working-tree'}


@app.get('/api/login')
def login_page(ticket: str = ''):
    if ticket:
        with STORE.connect() as db:
            key = hashlib.sha256(ticket.encode()).hexdigest()
            row = db.execute('SELECT expires FROM tickets WHERE token_hash=?', (key,)).fetchone()
            db.execute('DELETE FROM tickets WHERE token_hash=?', (key,))
        if row and row['expires'] > time.time():
            return sign_in(RedirectResponse('/', 303))
        raise HTTPException(401, 'Login link expired; generate a new link with explorerctl login-url')
    return FileResponse(STATIC / 'login.html')


@app.post('/api/login')
async def login(request: Request):
    key = request.client.host
    times = [stamp for stamp in LOGIN_ATTEMPTS.get(key, []) if stamp > time.time() - 300]
    LOGIN_ATTEMPTS[key] = times + [time.time()]
    if len(times) > 15:
        raise HTTPException(429, 'Try again later')
    body = await request.json()
    supplied = hashlib.pbkdf2_hmac('sha256', str(body.get('password', '')).encode(), CONFIG['password_salt'].encode(), 310000).hex()
    if not hmac.compare_digest(supplied, CONFIG['password_hash']):
        raise HTTPException(401, 'Incorrect access code')
    return sign_in(JSONResponse({'ok': True, 'redirect': '/'}))


@app.post('/api/logout')
def logout(request: Request):
    with STORE.connect() as db:
        db.execute('DELETE FROM sessions WHERE token_hash=?', (hashlib.sha256(request.cookies.get('explorer_session', '').encode()).hexdigest(),))
    response = JSONResponse({'ok': True})
    response.delete_cookie('explorer_session')
    response.delete_cookie('explorer_csrf')
    return response


TITLES = {'01': 'Автономное обучение', '02': 'Показы на ROSMASTER', '03': 'SO-101 → ROSMASTER', '04': 'Перенос навыка', '05': 'Совместная передача'}


def research_status():
    base = ROOT / 'research/literature'
    metrics = base / 'metrics.json'
    result = {'candidates': 0, 'primary_verified': 0, 'full_text_read': 0, 'deep_reviewed': 0, 'code_inspected': 0, 'code_executed': 0,
              'scientific_status': 'UNTESTED_HYPOTHESIS', 'venue': 'NOT_CONFIRMED'}
    if metrics.exists():
        result.update(json.loads(metrics.read_text()))
    review_status = base / 'REVIEW_STATUS.json'
    if review_status.exists():
        result.update(json.loads(review_status.read_text()).get('counts', {}))
        result['deep_reviewed'] = result.get('deep_reviews', 0)
    registry = base / 'literature_registry.csv'
    if registry.exists():
        with registry.open() as stream:
            rows = list(csv.DictReader(stream))
        result['candidates'] = len(rows)
    venue=ROOT/'research/paper/VENUE.json'
    if venue.is_file():
        target=json.loads(venue.read_text())
        result.update(venue=target.get('venue'),venue_status=target.get('target_status'),deadline=target.get('stage1',{}).get('deadline_moscow'))
    return result


def actions_required():
    station=STORE.nodes().get('pi',{}).get('station') or {}
    calibrated=len(station.get('roles',{}))==2
    return [
        {'id':'calibration', 'title':'Роли и исходные калибровки рук', 'status':'COMPLETED' if calibrated else 'CHECKING',
         'detail':'Роли подтверждены по исходным калибровкам и регистрам подключённых двигателей.' if calibrated else 'Станция проверяет подключённые руки.'},
        {'id':'demonstrations', 'title':'Записать полные показы выбранной задачи', 'status':'NEEDS_DEMONSTRATIONS',
         'detail':'Видео без действий не считается экспертной демонстрацией. Мастер начинает с 3–5 технических эпизодов.'},
        {'id':'venue', 'title':'Рабочая цель статьи: RSS 2027', 'status':'WORKING_TARGET',
         'detail':'Первый срок 5 декабря в 14:59 по Москве. Название конференции пока принято как рабочая гипотеза.'},
        {'id':'disk', 'title':'Проверить содержимое внешнего APFS-диска перед подготовкой хранилища', 'status':'PENDING_IDENTIFICATION',
         'detail':'Пока используются свободные 180+ ГБ внутреннего NVMe. Внешний Samsung T5 не форматировался.'}]


def node_states():
    observed = STORE.nodes()
    nodes = []
    for config in CONFIG['nodes']:
        item = dict(observed.get(config['id'], {}))
        item.update(id=config['id'], name=config['name'], host=config['host'])
        item['online'] = time.time() - item.get('last_seen', 0) < 20
        item.setdefault('health', 'online' if item['online'] else 'offline')
        item.setdefault('capabilities', {})
        item.setdefault('cameras', [])
        nodes.append(item)
    memory = psutil.virtual_memory()
    nodes.insert(0, dict(id='nx', name='Центральный сервер', host='homebrain.local', online=True,
        last_seen=time.time(), health='online', cameras=[], capabilities={'control_center':True},
        hardware={'memory_total':memory.total,'memory_available':memory.available,'cpu_percent':psutil.cpu_percent()}))
    return nodes


@app.get('/api/state')
def state():
    jobs = STORE.jobs()
    experiments = []
    for project_id, title in TITLES.items():
        project_jobs = [job for job in jobs if job['project_id'] == project_id]
        active = next((job for job in project_jobs if job['status'] in ('running', 'queued', 'paused')), None)
        experiments.append(dict(id=project_id, title=title,
            status=active['status'] if active else 'AWAITING_EVIDENCE',
            summary='Обучение и симуляция' if project_id == '01' else 'Готовится полный цикл опыта',
            blockers=['Физический результат пока не подтверждён'], latest_job=project_jobs[0] if project_jobs else None))
    disk = shutil.disk_usage(STORE.root)
    nodes=node_states()
    operational=any(node.get('capabilities',{}).get('physical_motion') for node in nodes)
    return dict(schema_version=1, nodes=nodes, experiments=experiments, jobs=jobs,
        skills=STORE.skills(), research=research_status(), storage={'free_bytes':disk.free,'total_bytes':disk.total,'root':str(STORE.root)},
        capabilities={'station_rollout':True},
        admission={'physical_enabled':operational, 'owner_authorization':'Разрешено владельцем при видимости на камере Pi либо подтверждённом присутствии',
            'reasons':['Доступность управления и результаты обучения показаны отдельно. Физическая проверка обученных навыков ещё не завершена.'], 'controller_settings_unchanged':True})


@app.get('/api/research')
def research():
    return dict(research_status(), runs=[job for job in STORE.jobs() if job['result']], hypotheses=[{'id':'H'+str(i),'status':'UNTESTED_HYPOTHESIS'} for i in range(1,6)])


@app.get('/api/skills')
def skills():
    return {'skills':STORE.skills()}


@app.get('/api/operator-actions')
def operator_actions():
    return {'actions':actions_required()}


@app.post('/api/events')
async def ingest(request: Request):
    try:
        body = await request.json()
        added = STORE.ingest(body)
        return {'ok':True, 'inserted':added, 'event_id':body['event_id']}
    except (ValueError, TypeError) as error:
        raise HTTPException(400, str(error))


@app.get('/api/jobs')
def jobs():
    return {'jobs':STORE.jobs()}


@app.get('/api/datasets')
def datasets():
    found={}
    roots=[(STORE.root/name).resolve() for name in ('datasets','jobs')]
    for job in STORE.jobs(1000):
        result=job.get('result') or {}
        candidate=result.get('dataset_manifest')
        if not candidate and job['kind']=='sim_pilot' and job['status']=='completed':
            candidate=str(STORE.root/'jobs'/job['id']/'dataset/manifest.json')
        if candidate and job['status']=='completed':
            path=Path(candidate).resolve()
            if path.is_file() and any(path.is_relative_to(root) for root in roots):
                try:
                    data=json.loads(path.read_text())
                    if data.get('format') in ('explorer_npz_episode_v1','explorer_robot_episode_v1'):
                        found[data['dataset_id']]=dict(id=data['dataset_id'],name='Симуляция достижения цели' if job['kind']=='sim_pilot' else data.get('task',data['dataset_id']),created=job['created'],
                            dataset_manifest=str(path),source=data.get('source'),state_basis=data.get('state_basis','command_estimate'),
                            action_basis=data.get('action_basis','simulated_command'),
                            episodes=[{key:row.get(key) for key in ('id','split','frames','outcome')} for row in data['episodes']])
                except (ValueError,KeyError,OSError):
                    pass
    return {'datasets':list(found.values())}


@app.post('/api/jobs')
async def create_job(request: Request):
    body = await request.json()
    if body.get('kind') not in ('sim_pilot', 'train_act', 'dagger','skill_cycle','program_trace','world_model','virtual_transfer', 'handover_test','station_shadow','station_rollout') or body.get('project_id') not in (*TITLES.keys(),'personal'):
        raise HTTPException(400, 'Unsupported job')
    config = body.get('config', {})
    if not isinstance(config, dict):
        raise HTTPException(400, 'Job configuration must be an object')
    if '_execution_commit' in config:
        raise HTTPException(400,'Execution provenance is assigned by the worker')
    if body['kind'] in ('world_model','dagger','skill_cycle') and config.get('device','cpu')!='cpu':
        raise HTTPException(400,'This engineering benchmark currently uses the verified CPU path')
    if body['kind']=='station_shadow':
        config['shadow']=True
    if body['kind']=='station_rollout':
        config['shadow']=False
        if not config.get('observing') or not config.get('expected_control_epoch'):
            raise HTTPException(400,'An observed current station control session is required')
    return STORE.create_job(body['kind'], body['project_id'], config)


@app.post('/api/jobs/{job_id}/{operation}')
async def job_operation(job_id: str, operation: str):
    job = STORE.job(job_id)
    if not job:
        raise HTTPException(404)
    desired = {'pause':'pause_requested', 'resume':'resume_requested', 'cancel':'cancel_requested'}
    if operation not in desired or job['status'] in ('completed', 'failed', 'cancelled'):
        raise HTTPException(409, 'Operation is not valid for this job')
    if operation in ('pause','resume') and job['kind'] not in ('sim_pilot','train_act','world_model','dagger','skill_cycle'):
        raise HTTPException(409,'Pause and resume apply to training. Stop an interactive attempt and start a fresh one.')
    STORE.update_job(job_id, status=desired[operation])
    if operation=='cancel' and job['kind']=='station_rollout' and (job.get('result') or {}).get('control_epoch'):
        node=next(node for node in CONFIG['nodes'] if node['id']=='pi')
        try:
            await CLIENT.post('http://'+node['host']+':'+str(node['port'])+'/station/stop',
                headers={'Authorization':'Bearer '+node['token']},json={'expected_control_epoch':job['result']['control_epoch']})
        except httpx.HTTPError:
            pass
    return STORE.job(job_id)


def find_camera(camera_id):
    for node in CONFIG['nodes']:
        if camera_id.startswith(node['id'] + '-'):
            return node
    raise HTTPException(404, 'Unknown camera')


@app.get('/api/cameras/{camera_id}/snapshot')
async def snapshot(camera_id: str):
    node = find_camera(camera_id)
    response = await CLIENT.get('http://' + node['host'] + ':' + str(node['port']) + '/cameras/' + camera_id + '/snapshot',
        headers={'Authorization':'Bearer ' + node['token']})
    if response.status_code != 200:
        raise HTTPException(503, 'Camera is not ready')
    return StreamingResponse(iter([response.content]), media_type='image/jpeg', headers={key:value for key,value in response.headers.items() if key.startswith('x-frame') or key.startswith('x-clock')})


@app.get('/api/cameras/{camera_id}/stream')
async def camera_stream(camera_id: str):
    node = find_camera(camera_id)
    request = CLIENT.build_request('GET', 'http://' + node['host'] + ':' + str(node['port']) + '/cameras/' + camera_id + '/stream',
        headers={'Authorization':'Bearer ' + node['token']}, timeout=None)
    try:
        response = await CLIENT.send(request, stream=True)
    except httpx.HTTPError:
        raise HTTPException(503, 'Camera unavailable')
    if response.status_code != 200:
        await response.aclose()
        raise HTTPException(503, 'Camera unavailable')
    async def parts():
        try:
            async for chunk in response.aiter_bytes():
                yield chunk
        finally:
            await response.aclose()
    return StreamingResponse(parts(), media_type=response.headers.get('content-type', 'multipart/x-mixed-replace; boundary=frame'))


@app.get('/api/recordings')
def recordings():
    return {'recordings':[job for job in STORE.jobs() if job['kind'] == 'camera_recording']}


@app.post('/api/recordings')
async def record(request: Request):
    body = await request.json()
    find_camera(body.get('camera_id', ''))
    duration = float(body.get('duration_s', 10))
    if not 1 <= duration <= 300 or body.get('project_id') not in (*TITLES.keys(), 'personal'):
        raise HTTPException(400, 'Invalid recording duration/project')
    return STORE.create_job('camera_recording', body['project_id'], {'camera_id':body['camera_id'], 'duration_s':duration})


@app.get('/api/simulations/{job_id}/frame')
def sim_frame(job_id: str):
    if not STORE.job(job_id):
        raise HTTPException(404)
    path = STORE.root / 'jobs' / job_id / 'frame.jpg'
    if not path.is_file():
        raise HTTPException(404, 'No rendered frame available')
    return FileResponse(path, media_type='image/jpeg')


@app.post('/api/stop')
async def stop():
    for job in STORE.jobs():
        if job['status'] in ('running', 'queued', 'paused'):
            STORE.update_job(job['id'], status='cancel_requested')
    async def deliver(node):
        path,payload=('/legacy/control',{'op':'stop'}) if node['id']=='nano' else ('/station/stop',{})
        try:
            response=await CLIENT.post('http://'+node['host']+':'+str(node['port'])+path,
                headers={'Authorization':'Bearer '+node['token']},json=payload)
            response.raise_for_status()
            return node['id'],{'confirmed':True,'response':response.json()}
        except httpx.HTTPError:
            return node['id'],{'confirmed':False}
    deliveries=dict(await asyncio.gather(*(deliver(node) for node in CONFIG['nodes'])))
    confirmed=all(item['confirmed'] for item in deliveries.values())
    return JSONResponse({'ok':confirmed,'scope':'ROSMASTER, SO-101 station and Explorer jobs','deliveries':deliveries},200 if confirmed else 503)


@app.api_route('/api/robot/{path:path}', methods=['GET','POST'])
async def robot_proxy(path: str, request: Request):
    from explorer_shared.legacy_bridge import GET_PATHS, POST_PATHS
    if path not in (GET_PATHS if request.method == 'GET' else POST_PATHS):
        raise HTTPException(404, 'Existing operation not available')
    node = next(node for node in CONFIG['nodes'] if node['id'] == 'nano')
    url = 'http://' + node['host'] + ':' + str(node['port']) + '/legacy/' + path
    try:
        finishing_id=None
        body=await request.json() if request.method=='POST' else None
        if request.method=='POST' and path in ('teaching/mobile/finish','teaching/mobile/stage'):
            before=await CLIENT.get('http://'+node['host']+':'+str(node['port'])+'/legacy/teaching/mobile',headers={'Authorization':'Bearer '+node['token']})
            before.raise_for_status()
            current=before.json()
            active=(current.get('active') or {}).get('id')
            expected=body.get('expected_id',body.get('session'))
            if active and expected is not None and expected!=active:
                return JSONResponse({'detail':'Recording session changed; no labels applied'},409)
            expected=expected or active or (current.get('last') or {}).get('id')
            body['expected_id']=expected
            if path=='teaching/mobile/finish':finishing_id=expected
        response = await CLIENT.request(request.method, url, headers={'Authorization':'Bearer ' + node['token']},
            **({'json':body} if request.method == 'POST' else {}),
            timeout=30 if path=='teaching/mobile/finish' else 4)
        result=response.json()
        if response.is_success and finishing_id and (result.get('last') or {}).get('id')==finishing_id:
            result['import_job']=STORE.create_import_job('mobile_import','02',finishing_id)
        return JSONResponse(result, response.status_code)
    except httpx.HTTPError:
        raise HTTPException(503, 'Onboard control interface is unavailable')


@app.api_route('/api/station/{path:path}', methods=['GET','POST'])
async def station_proxy(path: str, request: Request):
    node = next(node for node in CONFIG['nodes'] if node['id']=='pi')
    allowed_get = {'status','recordings'}
    allowed_post = {'teleop/start','teleop/recenter','policy/start','stop','record/start','record/finish','action'}
    if path not in (allowed_get if request.method=='GET' else allowed_post):
        raise HTTPException(404)
    try:
        payload = await request.json() if request.method=='POST' else None
        response = await CLIENT.request(request.method,'http://'+node['host']+':'+str(node['port'])+'/station/'+path,
            headers={'Authorization':'Bearer '+node['token']},**({'json':payload} if payload is not None else {}))
        result = response.json()
        if path=='record/finish' and response.is_success:
            result['import_job']=STORE.create_import_job('station_import',result.get('project_id','personal'),result['episode_id'])
        return JSONResponse(result,response.status_code)
    except httpx.HTTPError:
        raise HTTPException(503,'SO-101 station interface is unavailable')


@app.get('/{path:path}')
def shell(path: str):
    return FileResponse(STATIC / 'index.html')

