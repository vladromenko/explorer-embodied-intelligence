"""06 saved-model wrapper over shared native9 artifact checks and DEV evaluator.

No Source RPC/live shadow transport, optimizer, simulator or motor authority.
"""
import json
from pathlib import Path

from explorer_shared.learning.artifacts import sha
from . import transfer_target as target
from . import rosmaster_learning as native9

EVAL_KIND='transfer_target_eval'


def model_reference(store,wizard_id,model_job_id):
    wizard,_=target.source_for_wizard(store,wizard_id)
    job=store.job(model_job_id);result=(job or {}).get('result') or {};config=(job or {}).get('config') or {}
    if (not job or job['kind']!=target.JOB_KIND or job['project_id']!='06' or job['status']!='completed' or
            config.get('wizard_id')!=wizard_id or config.get('source_model_binding')!=wizard['source_model_binding'] or
            result.get('device')!='cpu' or result.get('feature_dimension')!=106 or result.get('output_action_dimension')!=9 or
            not all(result.get(key) is True for key in ('trained','weights_changed','save_reload_passed')) or
            type(result.get('optimizer_steps')) is not int or result['optimizer_steps']<1):
        raise ValueError('Actual completed target06 ACT9 optimizer/reload model required')
    _,_,adapter=target.target_dataset(store,wizard_id,config.get('dataset_job_id'))
    selected=adapter.selection_reference(config.get('demonstrations'),seed=config.get('seed'))
    if (config.get('dataset_manifest')!=selected['dataset_manifest'] or
            config.get('dataset_manifest_sha256')!=selected['dataset_sha256'] or
            config.get('target_selection')!=selected['selection'] or
            result.get('source_manifest_sha256')!=selected['dataset_sha256'] or
            result.get('source_manifest_path')!=selected['dataset_manifest'] or
            result.get('comparison_mode')!=config.get('mode')):
        raise ValueError('Current target model/source selection binding differs')
    reference=native9.saved_native9_reference(store,result,skill_id=wizard['skill_id'],version=wizard['target_version'],
        spec_sha256=adapter.corpus.manifest['skill_spec_sha256'],aggregate_sha256=selected['dataset_sha256'],model_job_id=model_job_id)
    original=store.root/'jobs'/model_job_id/'result.json'
    if not original.is_file() or original.stat().st_size>16*1024**2:
        raise ValueError('Original target training receipt required')
    saved=json.loads(original.read_bytes())
    for key in ('model_manifest_sha256','checkpoint_sha256','source_manifest_sha256','target_selection','comparison_mode',
                'trained','weights_changed','save_reload_passed','optimizer_steps'):
        if saved.get(key)!=result.get(key):raise ValueError('Original target training receipt changed')
    model=json.loads(Path(result['model_manifest']).read_bytes())
    if (model.get('job_kind')!=target.JOB_KIND or model.get('compatible_source_projects')!=['06'] or
            model.get('transfer_target_selection')!=selected['selection'] or
            (model.get('transfer_initialization') or {}).get('source_model_binding')!=wizard['source_model_binding'] or
            (model.get('transfer_initialization') or {}).get('mode')!=config.get('mode')):
        raise ValueError('Saved target representation/selection provenance differs')
    return dict(reference,wizard_id=wizard_id,source_model_binding=wizard['source_model_binding'],target_domain=wizard['domain'],backend='act',
        comparison_mode=config['mode'],demonstrations=config['demonstrations'],
        original_training_receipt_sha256=sha(original))


def model_versions(store,wizard_id):
    wizard,_=target.source_for_wizard(store,wizard_id);items=[]
    for job in store.jobs(10000):
        if job['kind']==target.JOB_KIND and job['project_id']=='06' and job.get('config',{}).get('wizard_id')==wizard_id:
            row=dict(model_job_id=job['id'],status=job['status'],created=job['created'],
                comparison_mode=job['config'].get('mode'),demonstrations=job['config'].get('demonstrations'),
                compatible=False,physical_execution_authorized=False)
            if job['status']=='completed':
                try:row.update(reference=model_reference(store,wizard_id,job['id']),compatible=True,
                               optimizer_steps=job['result']['optimizer_steps'],offline_evaluation=job['result'].get('evaluation'))
                except (ValueError,KeyError,TypeError,OSError) as error:row['reason']=str(error)
            items.append(row)
    ids={row['model_job_id'] for row in items}
    checks=[job for job in store.jobs(10000) if job['kind']==EVAL_KIND and job.get('config',{}).get('model_job_id') in ids]
    return dict(wizard_id=wizard_id,source_model_binding=wizard['source_model_binding'],models=items,checks=checks,model_loaded=False,source_network_requests_sent=0,
        physical_execution_authorized=False,live_shadow_available=False,
        evaluation_scope='recorded_target_DEV_action_predictions_only_not_independent_task_trials')


def evaluation_request(store,wizard_id,model_job_id):
    return dict(model_reference(store,wizard_id,model_job_id),partition='development',sealed_test_used=False,
                evaluation_scope='offline_action_prediction_diagnostics_only')


def enqueue_evaluation(store,wizard_id,model_job_id):
    return native9._enqueue(store,EVAL_KIND,evaluation_request(store,wizard_id,model_job_id))


def run_evaluation(store,job,*,loader=None,evaluator=None):
    from .rosmaster_eval_job import evaluate_saved_native9,queued_execution_config
    config,execution_commit=queued_execution_config(job.get('config') or {})
    expected=evaluation_request(store,config.get('wizard_id'),config.get('model_job_id'))
    if (job.get('kind'),job.get('project_id'))!=(EVAL_KIND,'06') or config!=expected:
        raise ValueError('Exact queued target06 saved-model DEV binding required')
    training=store.job(config['model_job_id'])
    return evaluate_saved_native9(store,job,training,expected,execution_commit=execution_commit,loader=loader,evaluator=evaluator)
