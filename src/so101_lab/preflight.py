"""Read-only readiness check: never constructs a camera, serial bus or model."""
from pathlib import Path
import importlib.metadata as metadata
import json,os,stat,time,urllib.request
from .resources import ROOT,owners,snapshot
from .hardware import DEFAULT_CAMERAS,calibration_snapshot

def _health(url):
    try:
        with urllib.request.urlopen(url,timeout=1.5) as response:
            return {'status':'PASS' if response.status==200 else 'FAIL','http_status':response.status}
    except Exception as exc:return {'status':'FAIL','message':str(exc)}

def device_inventory():
    device_map={**DEFAULT_CAMERAS,'leader':'/dev/so101-leader','follower':'/dev/so101-follower'}
    inventory={}
    for name,path in device_map.items():
        p=Path(path);present=p.exists();busy=owners([path]);accessible=present and os.access(p,os.R_OK|os.W_OK)
        state='BUSY' if busy else 'AVAILABLE' if accessible else 'MISSING' if not present else 'PERMISSION_DENIED'
        inventory[name]={'path':path,'resolved':str(p.resolve()),'present':present,'accessible':accessible,'state':state,'owners':busy,'opened_by_check':False}
    return inventory

def run_preflight(recording=None,preview=None,root=ROOT):
    root=Path(root);resources=snapshot();checks=[]
    inventory=device_inventory()
    checks.append({'id':'devices','name':'Камеры и руки','status':'PASS' if all(d['state']=='AVAILABLE' for d in inventory.values()) else 'BLOCKED','message':'Проверка файлов устройств и владельцев без открытия USB'})
    cal={'status':'NOT TESTED','live_match':'NOT TESTED','camera_geometry':'UNKNOWN','wrist_hand_eye':'UNKNOWN'}
    try:
        c=calibration_snapshot();cal.update(status='PASS',snapshot_files=c['files'],message='Хэши снимков проверены. Live readback моторов выполняется только после явного Start')
    except Exception as exc:cal.update(status='FAIL',message=str(exc))
    checks.append({'id':'calibration','name':'Снимки моторной калибровки','status':cal['status'],'message':cal['message']})
    free=resources['disk_free_bytes'];disk_ok=free>=2*1024**3
    checks.append({'id':'disk','name':'Место для записи','status':'PASS' if disk_ok else 'FAIL','message':f'{free/1024**3:.1f} GB свободно; минимум2 GB'})
    versions={}
    for name in ('lerobot','torch','torchvision','transformers','sam3','mlflow','numpy','opencv-python-headless'):
        try:versions[name]=metadata.version(name)
        except metadata.PackageNotFoundError:versions[name]=None
    dependencies_ok=all(versions.values())
    checks.append({'id':'dependencies','name':'Зависимости своей среды','status':'PASS' if dependencies_ok else 'FAIL','message':'Проверены metadata пакетов; эта проверка не создаёт CUDA context'})
    model_root=Path.home()/'.cache/huggingface/hub'
    models={}
    for name,repo,weight in [('sam3','facebook--sam3','sam3.pt'),('smolvla','lerobot--smolvla_base','model.safetensors'),('smolvlm','HuggingFaceTB--SmolVLM2-500M-Video-Instruct','config.json')]:
        files=sorted((model_root/('models--'+repo)/'snapshots').glob('*/'+weight))
        models[name]={'cached':bool(files),'snapshots':[str(f.parent.name) for f in files],'status':'PASS' if files else 'FAIL','actual_forward':'NOT TESTED'}
    tracking=_health('http://127.0.0.1:5020/health');checks.append({'id':'mlflow','name':'MLflow','status':tracking['status'],'message':tracking.get('message','Локальный tracking server отвечает')})
    busy=any(d['owners'] for d in inventory.values());devices_ok=all(d['state']=='AVAILABLE' for d in inventory.values())
    rec=recording or {};pv=preview or {};active=bool(rec.get('active'));preview_ours=bool(pv.get('active'))
    backend_ok=not(active and rec.get('state')=='failed')
    can_start=devices_ok and disk_ok and cal['status']=='PASS' and backend_ok and not active
    compute_ready=not resources['compute_conflicts'] and resources['memory_available_bytes']>=8*1024**3
    training_ready=compute_ready and dependencies_ok and all(m['cached'] for m in models.values())
    if active:record_status='RECORDING' if rec.get('state')=='recording' else 'SESSION_ACTIVE'
    else:record_status='READY_FOR_OPERATOR_START' if can_start else 'WAITING_FOR_HARDWARE' if busy or not devices_ok else 'BLOCKED'
    stream_health={'status':'NOT TESTED','message':'Фактический FPS/чтение кадров проверяются только при явном подключении камер'}
    if preview_ours:
        stream_health={'status':'PASS' if all(c.get('connected') for c in pv.get('cameras',{}).values()) else 'FAIL','message':'Предпросмотр открыт пользователем; счётчики получены от реального reader','cameras':pv.get('cameras',{})}
    actions=[]
    for name,d in inventory.items():
        if d['owners']:
            description=', '.join(f"{o['name']} (PID {o['pid']})" for o in d['owners'])
            actions.append(f'{name}: {description}. В интерфейсе этого эксперимента завершите его сессию/предпросмотр; затем повторите Preflight. Эта лаборатория его не останавливает.')
        elif not d['present']:actions.append(f'{name}: проверьте USB-подключение {d["path"]}')
    if not compute_ready:actions.append('Обучение/SAM ожидают освобождения GPU/RAM владельцем. Raw-запись не требует свободного GPU.')
    evidence={}
    for key,relative in [('hardware_readonly','hardware-readonly.json'),('camera_simultaneous','cameras-simultaneous/report.json'),('sam_gpu','sam3/report.json'),('pretrained_gpu','pretrained/report.json')]:
        file=root/'validation/acceptance-20261106'/relative
        if file.is_file():
            try:evidence[key]={'report':str(file.relative_to(root)),'recorded_at':file.stat().st_mtime,'result':json.loads(file.read_text())}
            except (OSError,ValueError):evidence[key]={'status':'UNREADABLE'}
    return {'acceptance_evidence':evidence,'schema_version':'so101.preflight.v1','checked_at':time.time(),'record_status':record_status,'can_start_recording':can_start,'annotation_training_status':'READY_FOR_MODEL_LOAD_CHECK' if training_ready else 'WAITING_FOR_COMPUTE','ready_to_record':can_start,'ready_to_annotate_train':training_ready,'checks':checks,'devices':inventory,'calibration':cal,'stream_health':stream_health,'recording_backend':{'status':'PASS' if backend_ok else 'FAIL','state':rec.get('state','idle')},'mlflow':tracking,'models':models,'versions':versions,'resources':resources,'actions':actions,'physical_verification':'NOT TESTED','torque_commands_sent':False}
