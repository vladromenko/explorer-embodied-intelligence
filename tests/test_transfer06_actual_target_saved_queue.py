"""Opt-in new-process saved ACT9 DEV prediction, no optimizer or physical IO."""
import json
import os
from pathlib import Path

import pytest

from explorer.store import Store
from explorer import transfer_target_models as models
from explorer.scheduler import Supervisor
from explorer.queue_policy import GIB
from native02_queue_helpers import freeze_source,private_config,QueueTrace,pump,stop_fixture_workers,sha
from test_native02_queued_learning import SOURCES

ROOT=Path(__file__).parents[1]


@pytest.mark.skipif(os.environ.get('EXPLORER_RUN_TRANSFER06_TARGET_SAVED')!='1',reason='Separate saved generated target ACT9 CPU DEV/new-PID qualifier')
def test_actual_saved_target_ACT9_new_process_DEV_predictions_match_training_without_optimizer(tmp_path,monkeypatch):
    root=Path(os.environ['EXPLORER_TRANSFER06_SOURCE_STATE']).resolve()
    assert root.is_relative_to(Path('/mnt/explorer-data/explorer-work'))
    assert not root.is_relative_to((Path.home()/'.local/share/explorer/state').resolve())
    store=Store(root);ids=os.environ['EXPLORER_TRANSFER06_TARGET_MODELS'].split(',')
    assert len(ids)==2 and len(set(ids))==2
    assert not [job for job in store.queue_jobs() if job['status'] not in ('completed','failed','cancelled')]
    originals={}
    for ident in ids:
        trained=store.job(ident);result=trained['result']
        originals[str(root/'jobs'/ident/'result.json')]=sha(root/'jobs'/ident/'result.json')
        originals.update({str(Path(result['checkpoint_dir'])/name):expected for name,expected in result['checkpoint_sha256'].items()})
    release,frozen=freeze_source(ROOT,tmp_path/'frozen',SOURCES+[
        '00-control-center/src/explorer/transfer_target.py','00-control-center/src/explorer/transfer_target_models.py',
        'shared/explorer_shared/learning/transfer_target_corpus.py','shared/explorer_shared/learning/transfer_target_training.py'])
    config=private_config(tmp_path/'private-config.json',store.root);monkeypatch.setenv('EXPLORER_CONFIG',str(config))
    trace=QueueTrace(store);supervisor=Supervisor({'root':str(release)},store,quantum_s=3600,popen=trace.popen);reports=[]
    try:
        for ident in ids:
            trained=store.job(ident);wizard_id=trained['config']['wizard_id']
            reference=models.model_reference(store,wizard_id,ident)
            job=models.enqueue_evaluation(store,wizard_id,ident)
            pump(supervisor,trace,lambda:store.job(job['id'])['status'] in ('completed','failed','cancelled') and
                job['id'] not in supervisor.processes,timeout=240,required_job_id=job['id'])
            completed=store.job(job['id']);assert completed['status']=='completed',completed
            result=completed['result'];assert result['optimizer_steps_executed']==0 and result['weights_unchanged']
            assert result['evaluation']==trained['result']['evaluation']
            assert result['evaluation']['ACT9']['evaluated_samples']==7
            assert result['source_network_requests_sent']==result['physical_control_requests_sent']==0
            assert not result['dispatch_performed'] and not result['independent_full_task_success']
            assert result['evaluated_original_episode_ids']==json.loads(Path(trained['result']['model_manifest']).read_bytes())['development_original_episode_ids']
            budget=trace.admitted[job['id']]['metadata']['resources']
            assert budget['memory_bytes']==2*GIB and budget['cpu_cores']==2 and budget['gpu_slots']==0
            reports.append(dict(model_job_id=ident,evaluation_job_id=job['id'],reference=reference,result=result,worker_resources=budget))
        assert all(sha(name)==expected for name,expected in originals.items())
        receipt=dict(schema='explorer06_actual_saved_generated_target_new_process_DEV_v1',source_commit=frozen['source_commit'],
            inputs='RETAINED_GENERATED_SOFTWARE_ORIGINALS_NOT_REAL_EXPERTS',optimizer_steps_executed=0,
            actual_native_commands=0,physical_acceptance=False,all_original_training_and_checkpoint_files_unchanged=True,
            DEV_predictions_exact_to_training=True,real_experts=0,reports=reports,workers=trace.public())
        (tmp_path/'qualification.json').write_text(json.dumps(receipt,indent=2)+'\n')
    finally:stop_fixture_workers(supervisor)
