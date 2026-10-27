"""ENGINEERING_GLUE: typed native9 model versions and read-only compute requests.

No Torch/model construction, Source RPC, executable actor or motion authority in
the control plane. Every actual compute request revalidates immutable artifacts.
"""
import hashlib
import json
from pathlib import Path
import re
import time
import uuid

from explorer_shared.contracts import ACTION_NAMES,digest
from explorer_shared.embodied_episode import ACTION_CONTRACT_ID
from . import skill_workflow

EVAL_KIND='rosmaster_skill_eval'


def _sha(path):
    with Path(path).open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()


def _chosen(store,skill_id,version):
    if type(version) is not int or version<1:raise ValueError('Exact positive skill version required')
    with store.connect() as db:chosen=skill_workflow._get_spec(db,skill_id,version)
    if not chosen['confirmed']:raise ValueError('Confirmed exact skill version required')
    return chosen


def model_versions(store,skill_id,version):
    chosen=_chosen(store,skill_id,version);items=[]
    for job in store.jobs(10000):
        lineage=job.get('config',{}).get('skill_workflow') or {};result=job.get('result') or {}
        if job['kind']=='rosmaster_skill_train' and lineage.get('skill_id')==skill_id and lineage.get('skill_version')==version:
            compatible=(job['status']=='completed' and all(result.get(key) is True for key in ('trained','weights_changed','save_reload_passed')) and
                type(result.get('optimizer_steps')) is int and result['optimizer_steps']>0 and
                result.get('output_action_dimension')==9 and result.get('feature_dimension')==106 and lineage.get('spec_sha256')==chosen['spec_sha256'])
            items.append({'model_job_id':job['id'],'status':job['status'],'created':job['created'],'compatible_native9_106':compatible,
                'optimizer_steps':result.get('optimizer_steps'),'save_reload_passed':result.get('save_reload_passed'),
                'model_manifest_sha256':result.get('model_manifest_sha256'),'aggregate_sha256':lineage.get('aggregate_sha256'),'checkpoint_content_sha256':digest(result.get('checkpoint_sha256') or {}),
                'offline_evaluation':result.get('evaluation'),'revalidate_before_compute':True,'physical_execution_authorized':False})
    model_ids={item['model_job_id'] for item in items}
    checks=[{key:job.get(key) for key in ('id','kind','project_id','status','progress','created','error','result','config')}
        for job in store.jobs(10000) if job['kind'] in (EVAL_KIND,'rosmaster_skill_shadow') and job.get('config',{}).get('model_job_id') in model_ids]
    return {'skill_id':skill_id,'skill_version':version,'spec_sha256':chosen['spec_sha256'],'models':items,'checks':checks,
        'network_requests_sent':0,'model_loaded':False,'physical_execution_authorized':False}


def model_reference(store,skill_id,version,model_job_id):
    chosen=_chosen(store,skill_id,version)
    if not re.fullmatch('[0-9a-f]{32}',str(model_job_id)):raise ValueError('Server model job ID required')
    job=store.job(model_job_id);result=(job or {}).get('result') or {};lineage=((job or {}).get('config') or {}).get('skill_workflow') or {}
    if (not job or job['kind']!='rosmaster_skill_train' or job['status']!='completed' or job['project_id'] not in ('02','03') or
            lineage.get('skill_id')!=skill_id or lineage.get('skill_version')!=version or lineage.get('spec_sha256')!=chosen['spec_sha256'] or
            result.get('device')!='cpu' or result.get('feature_dimension')!=106 or result.get('output_action_dimension')!=9 or
            not all(result.get(key) is True for key in ('trained','weights_changed','save_reload_passed')) or
            type(result.get('optimizer_steps')) is not int or result['optimizer_steps']<1):
        raise ValueError('Actual completed native skill optimizer/reload version required')
    def managed(value):
        path=Path(value).resolve()
        if not path.is_relative_to(store.root.resolve()) or not path.is_file():raise ValueError('Managed immutable model/source artifact required')
        return path
    path=managed(result.get('model_manifest',''))
    if path.stat().st_size>16*1024**2 or _sha(path)!=result.get('model_manifest_sha256'):raise ValueError('Native model manifest changed')
    model=json.loads(path.read_bytes());weights=result.get('checkpoint_sha256') or {};folder=Path(result.get('checkpoint_dir','')).resolve()
    if (model.get('schema')!='explorer_rosmaster_skill_model_v1' or model.get('action_contract_id')!=ACTION_CONTRACT_ID or model.get('action_names')!=list(ACTION_NAMES) or
            (model.get('ACT9') or {}).get('state_dimension')!=106 or (model.get('ACT9') or {}).get('output_dimension')!=9 or
            model.get('skill_id')!=skill_id or model.get('skill_version')!=version or model.get('skill_spec_sha256')!=chosen['spec_sha256'] or
            not weights or model.get('weights_sha256')!=weights or not folder.is_relative_to(store.root.resolve())):
        raise ValueError('Exact saved ACT native9/state106 and checkpoint inventory required')
    for name,expected in weights.items():
        if Path(name).name!=name or _sha(managed(folder/name))!=expected:raise ValueError('Saved checkpoint changed')
    source=managed(result.get('source_manifest_path',''))
    if _sha(source)!=result.get('source_manifest_sha256') or model.get('aggregate_sha256')!=_sha(source) or lineage.get('aggregate_sha256')!=_sha(source):
        raise ValueError('Pinned model aggregate source changed')
    return {'model_job_id':model_job_id,'skill_id':skill_id,'skill_version':version,'spec_sha256':chosen['spec_sha256'],
        'model_manifest_sha256':result['model_manifest_sha256'],'checkpoint_content_sha256':digest(weights),
        'aggregate_sha256':_sha(source),'device':'cpu','state_dimension':106,'action_dimension':9,
        'hardware_commands_dispatched':False,'physical_execution_authorized':False}


def evaluation_request(store,skill_id,version,model_job_id):
    return {**model_reference(store,skill_id,version,model_job_id),'partition':'development',
        'evaluation_scope':'offline_action_prediction_diagnostics_only','sealed_test_used':False}


def enqueue_evaluation(store,skill_id,version,model_job_id):
    settings=evaluation_request(store,skill_id,version,model_job_id)
    return _enqueue(store,EVAL_KIND,settings)


def enqueue_shadow(store,skill_id,version,model_job_id):
    reference=model_reference(store,skill_id,version,model_job_id)
    from .rosmaster_shadow_job import shadow_request
    settings=shadow_request(store,model_job_id);settings['skill_reference']=reference
    return _enqueue(store,'rosmaster_skill_shadow',settings)


def _enqueue(store,kind,settings):
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        previous=db.execute("SELECT * FROM jobs WHERE kind=? AND status NOT IN ('completed','failed','cancelled') AND json_extract(config,'$.model_job_id')=? ORDER BY created DESC LIMIT 1",(kind,settings['model_job_id'])).fetchone()
        if previous:
            saved=store.decode_job(previous)
            if saved['config']!=settings:raise ValueError('Active compute request has a different immutable model binding')
            return saved
        project_id=store.job(settings['model_job_id'])['project_id']
        identity=uuid.uuid4().hex;now=time.time()
        db.execute('INSERT INTO jobs(id,kind,project_id,status,created,updated,config) VALUES(?,?,?,?,?,?,?)',
            (identity,kind,project_id,'queued',now,now,json.dumps(settings,allow_nan=False)))
        from .experiment_projection import record_runtime_identity
        record_runtime_identity(db,'job',identity,project_id)
    return store.job(identity)
