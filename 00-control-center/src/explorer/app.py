"""ENGINEERING_GLUE: authenticated local Explorer control and evidence API."""
import asyncio
import csv
import hashlib
import hmac
import json
import re
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
from .skill_workflow import WorkflowBlocked as SkillWorkflowBlocked

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
            if node['id']=='pi':
                direct=await CLIENT.get('http://'+node['host']+':'+str(node['port'])+'/arm-retarget/recordings',headers={'Authorization':'Bearer '+node['token']})
                if direct.status_code==200:
                    for recording in direct.json().get('recordings',[]):
                        if recording.get('archive_sha256'):
                            STORE.create_import_job('retarget_import','03',recording['recording_id'],recording['archive_sha256'])
            capture=payload.get('mobile_observation') or {}
            imported={job['config'].get('episode_id') for job in STORE.jobs(10000) if job['kind']=='mobile_import'}
            for recording_id in capture.get('ready_recording_ids',[]):
                if recording_id not in imported:
                    STORE.create_import_job('mobile_import','02',recording_id)
        except Exception:
            pass
    while True:
        await asyncio.gather(*(poll(node) for node in CONFIG['nodes']))
        from .skill_workflow import bind_import_from_capture
        for job in STORE.jobs(10000):
            if job['kind']=='mobile_import' and job['status']=='completed':
                try:
                    await asyncio.to_thread(bind_import_from_capture,STORE,job)
                except (ValueError,KeyError,OSError):
                    # The retained import remains available for explicit review.
                    pass
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


PROJECT_REGISTRY = json.loads((ROOT/'_registry/projects.json').read_text())
TITLES = {row['id']:row['title'] for row in PROJECT_REGISTRY['projects']}


def canonical_project(value):
    """Compatibility view only: retained source metadata and hashes never change."""
    return '06' if value == 'personal' else value


@app.get('/api/projects')
def projects():
    return PROJECT_REGISTRY


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
        {'id':'geometry', 'title':'Проверить межроботную регистрацию и текущий command reference', 'status':'NEEDS_PHYSICAL_MEASUREMENTS',
         'detail':'Калибровка leader уже известна. Сохраняются штатный 90° reference, command-estimate и Ruckig без servo readback. Проверяются только cross-map/FK/IK/захват, текущий boot/reference и streaming integration; firmware и feedback не меняются.'},
        {'id':'handover_scene', 'title':'Подготовить видимую общую зону двух роботов', 'status':'NEEDS_PHYSICAL_SCENE',
         'detail':'На текущем Pi-ракурсе нет ROSMASTER и общей зоны передачи. Нужны видимость обеих рук, лёгкий предмет и отдельная проверка удержания. Шасси не двигать.'},
        {'id':'fleet_access', 'title':'Восстановить служебный SSH-доступ для новых релизов', 'status':'COMPLETED',
         'detail':'Вход по отдельному ключу Homebrain проверен на Pi и Nano 2026-10-26. Готовность ключа не означает, что новый backend уже развёрнут.'},
        {'id':'venue', 'title':'Определить конференцию и официальный срок статьи', 'status':'UNCONFIRMED',
         'detail':'Название конференции и CFP не подтверждены. Начало декабря — ориентир пользователя, а не проверенный deadline.'},
        {'id':'disk', 'title':'Samsung T5: перенос рабочего состояния проверен', 'status':'COMPLETED',
         'detail':'По явному разрешению владельца T5 подготовлен как ext4. Все SHA файлов и целостность SQLite совпали; прежние пути и копия отката на NVMe сохранены. Полный reboot ещё не проверялся.'}]


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
        project_jobs = [job for job in jobs if canonical_project(job['project_id']) == project_id]
        active = next((job for job in project_jobs if job['status'] in ('running', 'queued', 'paused')), None)
        experiments.append(dict(id=project_id, title=title,
            status=active['status'] if active else 'AWAITING_EVIDENCE',
            summary='Обучение и симуляция' if project_id == '01' else 'Готовится полный цикл опыта',
            blockers=['Физический результат пока не подтверждён'], latest_job=project_jobs[0] if project_jobs else None))
    disk = shutil.disk_usage(STORE.root)
    nodes=node_states()
    operational=any(node.get('online') and node.get('capabilities',{}).get('physical_motion') for node in nodes)
    return dict(schema_version=1, nodes=nodes, experiments=experiments, jobs=jobs,
        acceptance=acceptance(),constraints={'chassis_motion':'PROHIBITED_BY_OWNER','controller_configuration':'PRESERVE_ORIGINAL','arm_motion':'SEPARATE_USER_OBSERVED_TEST_REQUIRED'},
        skills=STORE.skills(), research=research_status(), storage={'free_bytes':disk.free,'total_bytes':disk.total,'root':str(STORE.root)},
        capabilities={'station_rollout':True},
        project_aliases={'personal':'06'},
        admission={'physical_enabled':operational, 'owner_authorization':'Движение рук только в отдельной проверке под текущим наблюдением пользователя; шасси не двигать',
            'reasons':['Доступность управления и результаты обучения показаны отдельно. Физическая проверка обученных навыков ещё не завершена.'], 'controller_settings_unchanged':True})


@app.get('/api/acceptance')
def acceptance():
    path=ROOT/'_registry/acceptance.json'
    return json.loads(path.read_text()) if path.is_file() else {'overall_status':'IN_PROGRESS','software_integrated':False,'criteria':[]}


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
        payload=body['payload']
        if body['node_id']=='pi' and payload.get('kind')=='retarget.recording.finished':
            STORE.create_import_job('retarget_import','03',payload['recording_id'],payload['archive_sha256'])
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
    body['project_id'] = canonical_project(body.get('project_id'))
    if body.get('kind') not in ('sim_pilot', 'train_act', 'dagger','skill_cycle','program_trace','world_model','virtual_transfer', 'handover_test','handover_runtime_test','station_shadow','station_rollout','manipulation','manipulation_student','manipulation_benchmark','manipulation_curriculum','mobile_manipulation','manipulation_selective_repair','policy_transfer','fault_repair') or body.get('project_id') not in (*TITLES.keys(),'personal'):
        raise HTTPException(400, 'Unsupported job')
    config = body.get('config', {})
    if not isinstance(config, dict):
        raise HTTPException(400, 'Job configuration must be an object')
    if '_execution_commit' in config or '_gpu_binding_sha256' in config:
        raise HTTPException(400,'Execution provenance is assigned by the worker')
    if body['project_id']=='02':
        from .skill_workflow import validate_02_training_request
        try:validate_02_training_request(STORE,body)
        except SkillWorkflowBlocked as error:raise HTTPException(409,str(error)) from error
    if body['kind'] in ('world_model','dagger','skill_cycle','manipulation','manipulation_benchmark','manipulation_curriculum','mobile_manipulation','manipulation_selective_repair','policy_transfer','fault_repair') and config.get('device','cpu')!='cpu':
        raise HTTPException(400,'This engineering benchmark currently uses the verified CPU path')
    if body['kind']=='manipulation_student' and config.get('device','cpu') not in ('cpu','cuda'):
        raise HTTPException(400,'Unsupported manipulation learner device')
    if body['kind'] in ('manipulation','manipulation_student','manipulation_benchmark','manipulation_curriculum','mobile_manipulation','manipulation_selective_repair','fault_repair'):
        if body['project_id'] != '01':
            raise HTTPException(400,'Manipulation belongs to experiment 01')
        if body['kind']!='fault_repair' and config.get('task','candy_to_tray') not in ('candy_to_tray','sock_to_basket'):
            raise HTTPException(400,'Unknown full-object task')
    if body['kind']=='policy_transfer':
        from .transfer import prepare_policy_transfer
        if body['project_id'] != '04':
            raise HTTPException(400,'Policy transfer belongs to experiment 04')
        try:
            prepare_policy_transfer(STORE, config)
        except (ValueError,KeyError,OSError) as error:
            raise HTTPException(400,str(error)) from error
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
    if operation=='retry':
        try:return STORE.retry_import_job(job_id)
        except ValueError as error:raise HTTPException(409,str(error)) from error
    desired = {'pause':'pause_requested', 'resume':'resume_requested', 'cancel':'cancel_requested'}
    if operation not in desired or job['status'] in ('completed', 'failed', 'cancelled'):
        raise HTTPException(409, 'Operation is not valid for this job')
    if operation in ('pause','resume') and job['kind'] not in ('sim_pilot','train_act','world_model','dagger','skill_cycle','manipulation','manipulation_student','manipulation_benchmark','manipulation_curriculum','mobile_manipulation','manipulation_selective_repair','policy_transfer','fault_repair'):
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


@app.get('/api/transfer/sources')
def transfer_sources():
    from .transfer import policy_transfer_sources
    return {'sources':policy_transfer_sources(STORE)}


@app.get('/api/handover/readiness')
def handover_readiness():
    from .transfer import handover_readiness as probe
    return probe(CONFIG)


@app.get('/api/demonstration-wizard')
def demonstration_plan(project_id: str,task_id: str='pick_and_place'):
    from .demonstration_wizard import plan
    try:
        return plan(STORE,project_id,task_id)
    except ValueError as error:
        raise HTTPException(400,str(error)) from error


@app.post('/api/demonstration-wizard/annotation')
async def demonstration_review(request: Request):
    from .demonstration_wizard import annotate
    body=await request.json()
    if not isinstance(body,dict):
        raise HTTPException(400,'Review must be an object')
    try:
        return await asyncio.to_thread(annotate,STORE,body.get('project_id'),body.get('task_id'),body)
    except (ValueError,TypeError) as error:
        raise HTTPException(409,str(error)) from error


@app.api_route('/api/retarget/{operation:path}',methods=['GET','POST'])
async def retarget_proxy(operation: str,request: Request):
    get_allowed=operation in ('status','recordings') or re.fullmatch(r'recordings/[0-9a-f]{32}/(manifest|download)',operation)
    if (request.method=='GET' and not get_allowed) or (request.method=='POST' and operation not in ('start','recenter','lease','stop','record/start','record/finish','import')):
        raise HTTPException(404)
    if operation=='import':
        body=await request.json()
        if not isinstance(body,dict) or not isinstance(body.get('recording_id'),str) or not re.fullmatch('[0-9a-f]{32}',body['recording_id']):
            raise HTTPException(400,'Invalid original direct recording identity')
        try:
            job=STORE.create_import_job('retarget_import','03',body['recording_id'],body.get('archive_sha256'))
            return STORE.retry_import_job(job['id']) if job['status'] in ('failed','cancelled') else job
        except ValueError as error:
            raise HTTPException(409,str(error)) from error
    node=next((node for node in CONFIG['nodes'] if node['id']=='pi'),None)
    if node is None:
        raise HTTPException(503,'Raspberry Pi is not configured')
    try:
        if operation.endswith('/download'):
            upstream=await CLIENT.send(CLIENT.build_request('GET','http://'+node['host']+':'+str(node['port'])+'/arm-retarget/'+operation,
                headers={'Authorization':'Bearer '+node['token']}),stream=True)
            if upstream.status_code!=200:
                await upstream.aclose()
                raise HTTPException(503,'Direct recording is not ready for transfer')
            async def data():
                try:
                    async for chunk in upstream.aiter_bytes():
                        yield chunk
                finally:
                    await upstream.aclose()
            return StreamingResponse(data(),media_type='application/x-tar',headers={'X-Content-SHA256':upstream.headers.get('X-Content-SHA256','')})
        response=await CLIENT.request(request.method,'http://'+node['host']+':'+str(node['port'])+'/arm-retarget/'+operation,
            headers={'Authorization':'Bearer '+node['token']},json=await request.json() if request.method=='POST' else None)
        return JSONResponse(response.json(),response.status_code)
    except (httpx.HTTPError,ValueError) as error:
        raise HTTPException(503,'Direct Pi→Nano route unavailable') from error


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


@app.get('/api/mobile/evidence')
def mobile_evidence():
    from .mobile_dataset_evidence import mobile_evidence_summary
    jobs = STORE.jobs(10000)
    return mobile_evidence_summary(jobs, managed_root=STORE.root, training_jobs=jobs)


@app.post('/api/recordings')
async def record(request: Request):
    body = await request.json()
    body['project_id'] = canonical_project(body.get('project_id'))
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
        capture_id=None
        capture_intent=None
        review={}
        if body is not None:
            if not isinstance(body,dict):raise HTTPException(400,'Native request must be an object')
            context=body.pop('skill_context',None)
            capture_id=body.pop('skill_capture_id',None)
            review=body.pop('skill_review',{})
            if path=='teaching/mobile/start':
                from . import skill_workflow as workflow_service
                if not isinstance(context,dict) or type(context.get('version')) is not int:
                    raise HTTPException(400,'Select and confirm a versioned ROSMASTER skill before recording')
                selected=await asyncio.to_thread(workflow_service.workflow,STORE,context.get('skill_id'),context['version'])
                if selected['spec_sha256']!=context.get('spec_sha256'):
                    raise HTTPException(409,'Skill definition changed; refresh before recording')
                capture_intent=await asyncio.to_thread(workflow_service.begin_capture,STORE,
                    context['skill_id'],context['version'],context.get('variant_id'),context.get('session_id'),
                    request_id=context.get('request_id'),episode_scope=context.get('episode_scope','whole_episode'),
                    target_phase_ids=context.get('target_phase_ids'),stationary_chassis_confirmed=context.get('stationary_chassis_confirmed') is True)
                capture_id=capture_intent['capture_id']
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
            if capture_id:
                from .skill_workflow import verify_capture_episode,capture_for_episode
                await asyncio.to_thread(verify_capture_episode,STORE,capture_id,expected)
                if path=='teaching/mobile/stage':
                    captured=await asyncio.to_thread(capture_for_episode,STORE,expected)
                    if not captured or body.get('stage') not in {phase['id'] for phase in captured['spec']['stages']}:
                        raise HTTPException(400,'Phase differs from the captured skill version')
            if path=='teaching/mobile/finish':finishing_id=expected
        response = await CLIENT.request(request.method, url, headers={'Authorization':'Bearer ' + node['token']},
            **({'json':body} if request.method == 'POST' else {}),
            timeout=30 if path=='teaching/mobile/finish' else 4)
        result=response.json()
        if response.is_success and capture_intent:
            from .skill_workflow import capture_started
            native_id=(result.get('active') or {}).get('id')
            if native_id:
                await asyncio.to_thread(capture_started,STORE,capture_id,native_id)
                result['skill_capture']=dict(capture_intent,original_episode_id=native_id)
            else:
                result['skill_workflow_error']='Native start returned no episode identity; retained recording requires explicit review'
        if response.is_success and capture_id and path=='teaching/mobile/stage':
            from .skill_workflow import capture_stage
            await asyncio.to_thread(capture_stage,STORE,capture_id,body.get('stage'))
        if response.is_success and finishing_id and (result.get('last') or {}).get('id')==finishing_id:
            result['import_job']=STORE.create_import_job('mobile_import','02',finishing_id)
            if capture_id:
                from .skill_workflow import capture_finished,bind_import_from_capture
                try:
                    if not isinstance(review,dict):raise ValueError('Recording review must be an object')
                    result['skill_review']=await asyncio.to_thread(capture_finished,STORE,capture_id,finishing_id,dict(review,outcome=body.get('outcome','unknown')))
                    await asyncio.to_thread(bind_import_from_capture,STORE,result['import_job'])
                except (ValueError,KeyError,OSError) as error:
                    result['skill_workflow_error']=str(error)
        return JSONResponse(result, response.status_code)
    except httpx.HTTPError:
        raise HTTPException(503, 'Onboard control interface is unavailable')
    except SkillWorkflowBlocked as error:
        return JSONResponse({'detail':str(error),'reasons':error.reasons},409)
    except (ValueError,KeyError,TypeError) as error:
        raise HTTPException(400,str(error)) from error


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
            result['import_job']=STORE.create_import_job('station_import',canonical_project(result.get('project_id','personal')),result['episode_id'])
        return JSONResponse(result,response.status_code)
    except httpx.HTTPError:
        raise HTTPException(503,'SO-101 station interface is unavailable')


from .skill_workflow_api import install_skill_workflow_routes
install_skill_workflow_routes(app,lambda:STORE)


@app.get('/learning/so101')
@app.get('/learning/so101/')
def legacy_so101_lab():
    return RedirectResponse('/experiments/06', status_code=307)


@app.get('/{path:path}')
def shell(path: str):
    return FileResponse(STATIC / 'index.html')

