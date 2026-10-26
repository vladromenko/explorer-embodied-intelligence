"""ENGINEERING_GLUE: one admitted CPU job owns two warmed native predictors.

Model imports occur only in this worker entry, never in request preparation.
Source access is authenticated GET-only; all actuator/native lease APIs are absent.
"""
from copy import deepcopy
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import signal
import sys
import threading
import time

import httpx

from explorer_shared.contracts import canonical,digest
from explorer_shared.cooperative_contract05 import ROLE_CONTRACTS
from explorer_shared.cooperative_policy_actor import ScheduledNativeActor,read_lease,lease_path
from explorer_shared.learning.checkpoint_control import atomic_json,worker_identity
from .cooperative_policy_adapters import QueuedNativeShadows
from .rosmaster_shadow_job import run_rosmaster_shadow
from .so101_shadow_job import run_so101_shadow,_get

FIELDS=('schema','session_id','cooperative_epoch','cooperative_process_session','cooperative_spec_sha256',
    'clock_domain','compute_ttl_s','warmup_timeout_s','device','native_plans','physical_execution_authorized')


def _sha(path):
    with Path(path).open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()


def binding_from_config(config):
    result={key:deepcopy(config[key]) for key in FIELDS if key in config}
    if (set(result)!=set(FIELDS) or result['schema']!='explorer_cooperative_warm_request_v1' or
            result['device']!='cpu' or result['physical_execution_authorized'] is not False or
            type(result['compute_ttl_s']) is not int or not 30<=result['compute_ttl_s']<=600 or result['warmup_timeout_s']!=120):
        raise ValueError('Typed two-head CPU-only compute session request required')
    return result


def current_source(store,settings):
    identity=settings['session_id']
    import re
    if not isinstance(identity,str) or not re.fullmatch('[0-9a-f]{32}',identity):raise ValueError('Exact cooperative source session ID required')
    folder=store.root/'cooperative05/workflow';path=folder/'sessions'/identity/'session.json'
    if path.is_symlink() or not path.resolve().is_relative_to(folder.resolve()) or path.stat().st_size>1024**2:
        raise ValueError('Managed original cooperative session required')
    row=json.loads(path.read_bytes());boot=Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    if (row.get('schema')!='explorer_cooperative_session_v1' or row.get('session_id')!=identity or
            row.get('epoch')!=settings['cooperative_epoch'] or row.get('process_session')!=settings['cooperative_process_session'] or
            row.get('spec_sha256')!=settings['cooperative_spec_sha256'] or
            row.get('clock_domain')!=settings['clock_domain'] or settings['clock_domain']!='nx:'+boot or
            row.get('status') in ('STOPPED','INTERRUPTED','COMPLETED')):
        raise ValueError('Current original cooperative boot/process/session/epoch required; no authority recovery')
    import uuid
    skill=str(uuid.UUID(str(row['skill_id'])));version=row['skill_version']
    if type(version) is not int or version<1:raise ValueError('Pinned original cooperative version required')
    path=folder/'skills'/skill/('v'+str(version).zfill(4)+'.json')
    if path.is_symlink() or not path.resolve().is_relative_to(folder.resolve()) or path.stat().st_size>1024**2:
        raise ValueError('Managed pinned cooperative specification required')
    selected=json.loads(path.read_bytes());spec=selected['spec']
    if (selected.get('confirmed') is not True or spec.get('spec_sha256')!=settings['cooperative_spec_sha256'] or
            digest({key:value for key,value in spec.items() if key!='spec_sha256'})!=settings['cooperative_spec_sha256']):
        raise ValueError('Exact confirmed original cooperative specification required')
    return row,spec


def warm_request(store,session,spec,*,compute_ttl_s=120):
    settings={'schema':'explorer_cooperative_warm_request_v1','session_id':session['session_id'],
        'cooperative_epoch':session['epoch'],'cooperative_process_session':session['process_session'],
        'cooperative_spec_sha256':spec['spec_sha256'],'clock_domain':session['clock_domain'],
        'compute_ttl_s':compute_ttl_s,'warmup_timeout_s':120,'device':'cpu','physical_execution_authorized':False,
        'native_plans':{}}
    settings=binding_from_config(settings);current_source(store,settings)
    queue=QueuedNativeShadows(store,store.root/'cooperative05/queued-shadows',current_session=lambda _id:session)
    settings['native_plans']=queue._plan(session,spec)
    return settings


def _node_client(config,node_id,client_factory):
    node=next((item for item in config['nodes'] if item['id']==node_id),None)
    if node is None:raise ValueError('Configured original source node required: '+node_id)
    host=node.get('host');port=node.get('port')
    if not isinstance(host,str) or any(value in host for value in '/@?#') or type(port) is not int or not 1<=port<=65535:
        raise ValueError('Fixed trusted source node origin required')
    return client_factory(base_url='http://'+host+':'+str(port),headers={'Authorization':'Bearer '+node['token']},
        timeout=2,trust_env=False,follow_redirects=False)


def load_native_models(store,settings,config,*,client_factory=httpx.Client):
    """Runs only inside admitted worker; hashes actual loaded implementations."""
    from explorer_shared.learning.rosmaster_skill_pipeline import RosmasterSkillPolicy
    from .so101_shadow import load_shadow_policy
    import psutil
    plans=settings['native_plans'];giver=store.job(plans['giver']['settings']['model_job_id'])
    path=Path(giver['result']['model_manifest']);saved=json.loads(path.read_bytes())
    implementation=saved.get('implementation_sha256') or {}
    learning=Path(__file__).parents[3]/'shared/explorer_shared/learning'
    for name in ('rosmaster_skill_pipeline.py','rosmaster_skill_features.py','rosmaster_skill_hybrid.py'):
        if implementation.get(name)!=_sha(learning/name):raise ValueError('Native106D saved model/implementation binding differs')
    native9=RosmasterSkillPolicy(path,candidate='ACT9')
    with _node_client(config,'pi',client_factory) as client:
        raw,_=_get(client,'/station/observation',128*1024);source=json.loads(raw)['observation']
    native6=load_shadow_policy(store,plans['receiver']['settings']['model_job_id'],
        expected_camera_id=source['camera_id'],expected_calibration_sha256=source['follower_calibration_sha256'])
    versions={name:importlib.metadata.version(name) for name in ('torch','torchvision','lerobot')}
    if versions['lerobot']!='0.6.1':raise ValueError('Verified official LeRobot0.6.1 required')
    for role,policy in (('giver',native9),('receiver',native6)):
        if any(parameter.device.type!='cpu' for parameter in policy.policy.parameters()):raise ValueError('CPU-only native predictors required')
        training=store.job(plans[role]['settings']['model_job_id']);expected=training['result'].get('framework_versions')
        if not isinstance(expected,dict) or not expected or any(versions.get(key)!=value for key,value in expected.items()):
            raise ValueError('Actual saved training/runtime framework versions differ')
    files={}
    for name,module in list(sys.modules.items()):
        source_file=getattr(module,'__file__',None)
        if source_file and (name.startswith('lerobot.') or name in ('torch.nn.modules.transformer','torchvision.models.resnet')):
            path=Path(source_file)
            if path.is_file():files[name]={'path':str(path.resolve()),'sha256':_sha(path)}
    for name in ('act_policy.py','act_backend.py','rosmaster_skill_pipeline.py','rosmaster_skill_features.py','rosmaster_skill_hybrid.py'):
        files['explorer_shared.learning.'+name]={'sha256':_sha(learning/name)}
    return {'giver':native9,'receiver':native6},{'schema':'explorer_cooperative_loaded_native_models_v1',
        'native_model_bindings':deepcopy(plans),'framework_versions':versions,'loaded_implementation_sha256':files,
        'loaded_pair_rss_bytes':psutil.Process().memory_info().rss,'device':'cpu','action_dimensions':{'giver':9,'receiver':6},
        'physical_execution_authorized':False,'model_pair_verified':True,'software_model_fixture':False}


class _InferenceStore:
    def __init__(self,store,parent,artifact_id):self.store,self.parent,self.artifact=store,parent,artifact_id;self.root=store.root
    def job(self,identity):return self.store.job(self.parent if identity==self.artifact else identity)


def run_cooperative_policy_session(store,job,config,*,model_loader=load_native_models,client_factory=httpx.Client,
                                   actor_class=ScheduledNativeActor,clock=time.monotonic_ns,socket_root=None):
    settings=binding_from_config(job['config']);row,spec=current_source(store,settings)
    expected=warm_request(store,row,spec,compute_ttl_s=settings['compute_ttl_s'])
    if expected!=settings:raise ValueError('Queued native model/source/implementation request changed')
    identity=worker_identity();runtime=store.schedule_status(job['id'])['runtime'];nonce=runtime.get('launch_nonce')
    if runtime.get('worker')!=identity or runtime.get('bootstrap_admitted') is not True or not nonce:
        raise ValueError('Durable scheduler PID/start/boot/nonce admission required before model imports')
    root=store.root/'jobs'/job['id'];root.mkdir(parents=True,exist_ok=True);warmup=root/'cooperative-warmup.json'
    initial={'schema':'explorer_cooperative_warmup_deadline_v1','clock_domain':settings['clock_domain'],
        'binding_sha256':digest(settings),'first_started_monotonic_ns':clock(),'warmup_timeout_s':120}
    if warmup.exists():initial=json.loads(warmup.read_bytes())
    else:atomic_json(warmup,initial)
    if initial.get('clock_domain')!=settings['clock_domain'] or initial.get('binding_sha256')!=digest(settings):
        raise ValueError('Original compute warmup boot/binding changed')
    source_fault=[None]
    def valid():
        current=store.job(job['id']);runtime=store.schedule_status(job['id'])['runtime']
        try:current_source(store,settings)
        except (ValueError,OSError,KeyError):return False
        return bool(not source_fault[0] and current and current['status']=='running' and runtime.get('worker')==identity and runtime.get('launch_nonce')==nonce)
    def check_warmup():
        if not valid():raise ValueError('Scheduled compute session cancelled/revoked before warmup')
        if not 0<=clock()-initial['first_started_monotonic_ns']<=120_000_000_000:raise ValueError('Original bounded120s model warmup deadline expired')
    old_signals={}
    def interrupted(signum,frame):raise RuntimeError('Scheduled CPU actor process interrupted')
    if threading.current_thread() is threading.main_thread():
        for signum in (signal.SIGTERM,signal.SIGINT):old_signals[signum]=signal.signal(signum,interrupted)
    try:
        check_warmup();store.update_schedule_runtime(job['id'],phase='cooperative_models_loading',warm_actor=None)
        models,receipts=model_loader(store,settings,config,client_factory=client_factory);check_warmup()
        if set(models)!={'giver','receiver'} or receipts.get('action_dimensions')!={'giver':9,'receiver':6}:
            raise ValueError('Separate actual native106→9 and measured6→6 CPU models required')
        sequence=max([int(path.name.split('-')[0]) for path in (root/'inferences').glob('[0-9]*-*')]+[0])
        def predict(role):
            nonlocal sequence
            if not valid():raise ValueError('Current source session revoked before inference')
            current,spec=current_source(store,settings)
            try:
                if warm_request(store,current,spec,compute_ttl_s=settings['compute_ttl_s'])!=settings:
                    raise ValueError('Original native model/source context changed after warmup')
            except (ValueError,OSError,KeyError) as error:
                source_fault[0]=type(error).__name__+':'+str(error);raise
            size=sum(path.stat().st_size for path in (root/'inferences').rglob('*') if path.is_file())
            if size+24*1024**2>128*1024**2:raise ValueError('Original inference evidence budget reached; explicit new compute session required')
            sequence+=1;plan=settings['native_plans'][role];artifact_id=job['id']+'/inferences/'+str(sequence).zfill(6)+'-'+role
            proxy=_InferenceStore(store,job['id'],artifact_id);call={'id':artifact_id,'config':deepcopy(plan['settings'])}
            if role=='giver':
                result=run_rosmaster_shadow(proxy,call,config,client_factory=client_factory,loader=lambda path:models['giver'],clock=clock)
            else:
                model=models['receiver']
                def loaded(store,model_id,*,expected_camera_id,expected_calibration_sha256):
                    if model.camera_id!=expected_camera_id or model.calibration_sha256!=expected_calibration_sha256:
                        raise ValueError('Current measured6 camera/calibration differs from warmed model')
                    return model
                result=run_so101_shadow(proxy,call,config,client_factory=client_factory,loader=loaded,clock=clock)
            if not valid():raise ValueError('Late native inference after cancellation/epoch change rejected')
            folder=store.root/'jobs'/artifact_id
            atomic_json(folder/'result.json',result)
            packet=json.loads((folder/'observation.json').read_bytes())
            reference=plan['reference'];model={key:reference[key] for key in ('model_id','checkpoint_sha256','spec_sha256')}
            return {'schema':'explorer_cooperative_warm_prediction_v1','native_role':role,'action_contract':deepcopy(ROLE_CONTRACTS[role]),
                'model':model,'source_sequence':sequence,'action9':result.get('action9') if role=='giver' else None,
                'predicted_native6':result.get('predicted_native6') if role=='receiver' else None,
                'source_observation':packet['observation']['native_observation'] if role=='giver' else packet['observation'],
                'source_evidence':{'original_artifacts':result['artifacts'],'artifact_id':artifact_id,
                    'result_sha256':_sha(folder/'result.json'),'worker_launch_nonce':nonce},
                'dispatch_performed':False,'physical_execution_authorized':False}
        def update(public):store.update_schedule_runtime(job['id'],phase='cooperative_actor_'+public['phase'].lower(),warm_actor=public)
        actor=actor_class(job['id'],root,settings,launch_nonce=nonce,compute_ttl_s=settings['compute_ttl_s'],
            predict=predict,valid=valid,runtime_update=update,model_receipts=receipts,clock=clock,socket_root=socket_root)
        result=actor.serve()
        return dict(result,model_receipts=receipts,compute_session_binding_sha256=digest(settings),
            hardware_commands_dispatched=False,physical_execution_authorized=False,hardware_lease_renewed=False)
    finally:
        # SIGTERM/exception during source/model loading cannot leave old READY.
        if lease_path(root).exists():
            value=read_lease(root)
            if value.get('worker')==identity:
                value.update(phase='REVOKED',token=None,closed_reason='SCHEDULED_WORKER_ENTRY_EXITED');atomic_json(lease_path(root),value)
                store.update_schedule_runtime(job['id'],phase='cooperative_actor_revoked',warm_actor={key:item for key,item in value.items() if key!='token'})
        for signum,handler in old_signals.items():signal.signal(signum,handler)
