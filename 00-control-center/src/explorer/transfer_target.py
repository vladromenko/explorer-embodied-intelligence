"""Thin06 managed target dataset/queue adapter over admitted04 and shared ACT9."""
from copy import deepcopy
import json
from pathlib import Path
import time
import uuid

from explorer_shared.contracts import canonical,digest
from explorer_shared.learning.artifacts import sha,write_json
from explorer_shared.learning.rosmaster_skill_features import PipelineBlocked
from explorer_shared.learning.transfer_target_corpus import TransferTargetCorpus
from . import transfer_geometry,transfer_calibration

JOB_KIND='transfer_target_train'
DATASET_KIND='transfer_target_dataset'


def source_for_wizard(store,wizard_id):
    wizard=transfer_calibration.session(store,wizard_id)
    binding=wizard.get('source_model_binding') or {}
    model=transfer_geometry.admitted_model(store,binding.get('model_job_id'))
    expected=dict(model_job_id=model['job']['id'],skill_id=model['manifest']['skill_id'],
        skill_version=model['manifest']['skill_version'],spec_sha256=model['manifest']['spec_sha256'],
        aggregate_manifest_sha256=model['result']['aggregate_manifest_sha256'],
        training_receipt_sha256=model['training_receipt_sha256'],checkpoint_content_sha256=model['checkpoint_content_sha256'],
        source_kind=model['manifest']['source_kind'],source_domain=model['source_domain'],
        source_calibration_sha256=model['source_calibration_sha256'])
    if (any(binding.get(key)!=value for key,value in expected.items()) or
            wizard['skill_id']!=binding['skill_id'] or wizard['source_version']!=binding['skill_version']):
        raise PipelineBlocked('CURRENT_SOURCE_WIZARD_BINDING_CHANGED')
    return wizard,model


def target_dataset(store,wizard_id,dataset_job_id):
    wizard,model=source_for_wizard(store,wizard_id)
    job=store.job(dataset_job_id)
    if not job or job['kind']!=DATASET_KIND or job['project_id']!='06' or job['status']!='completed':
        raise PipelineBlocked('COMPLETED_MANAGED_TARGET06_DATASET_REQUIRED')
    result=job.get('result') or {};name=result.get('dataset_manifest')
    if not isinstance(name,str):raise PipelineBlocked('MANAGED_TARGET_DATASET_PATH_REQUIRED')
    path=Path(name).resolve();root=(store.root/'datasets').resolve()
    if not path.is_relative_to(root) or not path.is_file() or sha(path)!=result.get('dataset_manifest_sha256'):
        raise PipelineBlocked('MANAGED_TARGET_DATASET_CHANGED_OR_ESCAPED')
    adapter=TransferTargetCorpus(path,source_model_binding=wizard['source_model_binding'],
        target_version=wizard['target_version'],target_domain=wizard['domain'])
    from .native_capture_cohort import validate_current_approvals
    validate_current_approvals(store,adapter.corpus.manifest)
    source_spec=model['manifest']['skill_spec'];target_spec=adapter.corpus.spec
    if ([row['id'] for row in source_spec['stages']] != [row['id'] for row in target_spec['stages']] or
            transfer_geometry.transfer_source.source_task_type(source_spec)!=
            transfer_geometry.transfer_source.source_task_type(target_spec)):
        raise PipelineBlocked('COMPLETE_SOURCE_TARGET_TASK_PHASE_CONTRACT_REQUIRED')
    return wizard,model,adapter


def dataset_choices(store,wizard_id):
    wizard,_=source_for_wizard(store,wizard_id);rows=[]
    for job in store.jobs():
        if job['kind']==DATASET_KIND and job['project_id']=='06' and job['status']=='completed':
            try:
                _,_,adapter=target_dataset(store,wizard_id,job['id'])
                rows.append(dict(dataset_job_id=job['id'],ready=True,**adapter.readiness()))
            except (ValueError,KeyError,TypeError,OSError) as error:
                rows.append(dict(dataset_job_id=job['id'],ready=False,reason=str(error)))
    return dict(wizard_id=wizard_id,target_version=wizard['target_version'],source_model_binding=wizard['source_model_binding'],datasets=rows,physical_execution_authorized=False)


def enqueue_training(store,wizard_id,dataset_job_id,options,*,actor):
    allowed={'mode','demonstrations','seed','act_steps','batch_size','image_size','chunk_size','threads','request_id'}
    if not isinstance(options,dict) or set(options)-allowed or not actor:
        raise ValueError('Choose managed target dataset, comparison mode, budget and bounded CPU settings only')
    request_id=str(uuid.UUID(options.get('request_id')))
    mode=options.get('mode')
    if mode not in ('target_only','vision_adapt'):raise ValueError('Choose target_only or vision_adapt')
    settings=dict(seed=41,act_steps=8,batch_size=2,image_size=32,chunk_size=2,threads=1)
    settings.update({key:options[key] for key in settings if key in options})
    for key,low,high in (('seed',0,1000000),('act_steps',1,100000),('batch_size',1,32),('image_size',32,224),('chunk_size',1,100),('threads',1,4)):
        if type(settings[key]) is not int or not low<=settings[key]<=high:raise ValueError('Invalid target ACT setting: '+key)
    wizard,model,adapter=target_dataset(store,wizard_id,dataset_job_id)
    if mode=='vision_adapt' and model['backend']!='act':raise PipelineBlocked('SOURCE_BACKBONE_ADAPT_REQUIRES_ACT')
    selected=adapter.save_selection(options.get('demonstrations'),seed=settings['seed'])
    config=dict(settings,device='cpu',backend='act',wizard_id=wizard_id,dataset_job_id=dataset_job_id,
        request_id=request_id,requested_by_actor=actor,mode=mode,demonstrations=options['demonstrations'],
        dataset_manifest=selected['dataset_manifest'],dataset_manifest_sha256=selected['dataset_sha256'],
        source_model_binding=wizard['source_model_binding'],target_selection=selected['selection'])
    # Same durable queue table/identity projection; no new scheduler or DB schema.
    identity=uuid.uuid4().hex;now=time.time()
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        old=db.execute('SELECT id,config FROM jobs WHERE kind=? AND project_id=? AND json_extract(config,\'$.request_id\')=?',
                       (JOB_KIND,'06',request_id)).fetchone()
        if old:
            if json.loads(old['config'])!=config:raise ValueError('Conflicting immutable target training request')
            identity=old['id']
        else:
            db.execute('INSERT INTO jobs(id,kind,project_id,status,created,updated,config) VALUES (?,?,?,?,?,?,?)',
                (identity,JOB_KIND,'06','queued',now,now,json.dumps(config,allow_nan=False)))
            from .experiment_projection import record_runtime_identity
            record_runtime_identity(db,'job',identity,'06')
    return store.job(identity)


def run_training(store,job):
    config=job.get('config') or {}
    if (job.get('kind'),job.get('project_id'))!=(JOB_KIND,'06'):raise PipelineBlocked('EXACT_TARGET06_TRAINING_JOB_REQUIRED')
    wizard,model,adapter=target_dataset(store,config.get('wizard_id'),config.get('dataset_job_id'))
    expected=adapter.save_selection(config.get('demonstrations'),seed=config.get('seed'))
    if (config.get('source_model_binding')!=wizard['source_model_binding'] or
            config.get('dataset_manifest')!=expected['dataset_manifest'] or
            config.get('dataset_manifest_sha256')!=expected['dataset_sha256'] or
            config.get('target_selection')!=expected['selection'] or config.get('device')!='cpu'):
        raise PipelineBlocked('QUEUED_TARGET_TRAINING_LINEAGE_CHANGED')
    from explorer_shared.learning.transfer_target_training import TransferTrainingContext
    from explorer_shared.learning.rosmaster_skill_pipeline import train_pipeline
    context=TransferTrainingContext(source_model_binding=wizard['source_model_binding'],
        target_version=wizard['target_version'],target_domain=wizard['domain'],mode=config['mode'],
        **(dict(source_checkpoint=model['checkpoint_path'],source_checkpoint_sha256=model['checkpoint_sha256'])
           if config['mode']=='vision_adapt' else {}))
    def guard():
        current=store.job(job['id'])
        if current and current['status'] in ('cancel_requested','cancelled'):raise RuntimeError('Target06 training cancelled')
        source_for_wizard(store,wizard['wizard_id']);context.recheck();adapter.corpus.recheck()
        from .native_capture_cohort import validate_current_approvals
        validate_current_approvals(store,adapter.corpus.manifest)
    def progress(value,phase):guard();store.update_job(job['id'],progress=value,result=dict(phase=phase))
    folder=store.root/'jobs'/job['id']
    result=train_pipeline(expected['dataset_manifest'],folder,
        settings={key:config[key] for key in ('seed','act_steps','batch_size','image_size','chunk_size','threads','device')},
        guard=guard,progress=progress,transfer_context=context)
    guard();result['job_kind']=JOB_KIND;result['skill_card']['lineage']['job_id']=job['id']
    result['target_selection']=expected['selection'];result['comparison_mode']=config['mode']
    if not any(card['id']==result['skill_card']['id'] for card in store.skills()):store.add_skill(result['skill_card'])
    write_json(folder/'result.json',result)
    return result


def prepare_capture_context(store,wizard_id,metadata,*,actor):
    """Prepare DATA only; source, Nano owners and native commands are untouched."""
    allowed={'request_id','session_id','partition','scene_id','object_instance_id','group_id','variant_id','phase_id'}
    if not isinstance(metadata,dict) or set(metadata)!=allowed or not actor:
        raise ValueError('Choose explicit target session/split/scene/object/group and phase only')
    wizard,model=source_for_wizard(store,wizard_id)
    if wizard['domain']!='physical':raise ValueError('Native source packets describe the physical target; use a separate SIM producer for simulation')
    from explorer_shared.transfer_target_capture import build_target_capture_context,SPEC_SCHEMA,CONTRACT
    from explorer_shared.contracts import ACTION_NAMES,ACTION_UNITS
    source_spec=model['manifest']['skill_spec'];binding=wizard['source_model_binding']
    target_spec=dict(schema=SPEC_SCHEMA,skill_id=wizard['skill_id'],version=wizard['target_version'],
        title=source_spec.get('title'),recognized_type=transfer_geometry.transfer_source.source_task_type(source_spec),
        stages=deepcopy(source_spec['stages']),source_spec_sha256=binding['spec_sha256'],
        source_spec_snapshot=deepcopy(source_spec),source_model_job_id=binding['model_job_id'],
        action_contract_id=CONTRACT,action_names=list(ACTION_NAMES),action_units=list(ACTION_UNITS))
    return build_target_capture_context(binding,target_spec,wizard_id=wizard_id,capture_id=metadata['request_id'],
        actor_id=actor,**{key:value for key,value in metadata.items() if key!='request_id'})
