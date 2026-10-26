import json
from pathlib import Path

import pytest
from explorer.store import Store
from explorer.scheduler import execution_environment


def releases(tmp_path):
    parent=tmp_path/'releases'
    paths=[]
    for commit in ('a'*40,'b'*40):
        root=parent/commit
        root.mkdir(parents=True)
        (root/'RELEASE_COMMIT').write_text(commit+'\n')
        paths.append(root)
    return paths


def test_resumed_job_keeps_original_source_after_deployment_changes(tmp_path):
    first,second=releases(tmp_path)
    store=Store(tmp_path/'state')
    job=store.create_job('sim_pilot','01',{})
    original=execution_environment({'root':str(first)},store,job)
    resumed=execution_environment({'root':str(second)},store,store.job(job['id']))
    assert original['PYTHONPATH']==resumed['PYTHONPATH']
    assert str(first) in resumed['PYTHONPATH']
    assert store.job(job['id'])['config']['_execution_commit']=='a'*40
    (first/'RELEASE_COMMIT').unlink()
    with pytest.raises(ValueError,match='unavailable'):
        execution_environment({'root':str(second)},store,store.job(job['id']))


def test_job_and_completion_event_are_idempotent_and_commit_atomically(tmp_path):
    store=Store(tmp_path)
    job=store.create_job('train_act','personal',{})
    result={'trained':True,'weights_sha256':'fixture-not-a-real-model'}
    assert store.complete_job(job['id'],result)=='completed'
    assert store.complete_job(job['id'],result)=='completed'
    with store.connect() as db:
        assert db.execute('SELECT count(*) FROM events').fetchone()[0]==1
    with pytest.raises(ValueError,match='cannot change'):
        store.complete_job(job['id'],{'trained':False})
    assert store.job(job['id'])['result']==result


def test_cancellation_is_not_overwritten_by_late_result_commit(tmp_path):
    store=Store(tmp_path)
    job=store.create_job('station_rollout','personal',{})
    store.update_job(job['id'],status='cancel_requested')
    assert store.complete_job(job['id'],{'outcome':'unknown'})=='cancelled'
    assert store.job(job['id'])['status']=='cancelled'


def test_ui_counts_actual_optimizer_receipts_not_requested_budget(tmp_path):
    store=Store(tmp_path)
    job=store.create_job('manipulation_student','01',{'act_steps':1000})
    folder=store.root/'jobs'/job['id'];folder.mkdir(parents=True)
    assert 'completed_updates' not in store.job(job['id'])
    receipt=folder/'act_training.json'
    receipt.write_text(json.dumps({'losses':[{'step':1,'loss':.4},{'step':2,'loss':.3}],'device':'cpu'}))
    observed=store.job(job['id'])
    assert observed['completed_updates']==2 and observed['metrics']['loss']==.3
    assert observed['result'] is None and observed['progress']==0
    receipt.write_text(json.dumps({'losses':[{'step':1000,'loss':.3}]}))
    assert 'completed_updates' not in store.job(job['id'])
    receipt.write_text('[1]')
    assert 'completed_updates' not in store.job(job['id'])


def test_explicit_data_retry_preserves_original_failure_source_and_single_child(tmp_path):
    store=Store(tmp_path)
    original=store.create_import_job('retarget_import','03','a'*32,'b'*64)
    store.pin_job_release(original['id'],'c'*40)
    store.update_job(original['id'],status='failed',error='Interrupted network transfer')
    retried=store.retry_import_job(original['id'])
    assert retried['id']!=original['id'] and retried['config']['archive_sha256']=='b'*64
    assert retried['config']['retry_of_job_id']==original['id'] and '_execution_commit' not in retried['config']
    assert store.job(original['id'])['status']=='failed'
    assert store.retry_import_job(original['id'])['id']==retried['id']
    assert store.create_import_job('retarget_import','03','a'*32,'b'*64)['id']==retried['id']
    with pytest.raises(ValueError):store.retry_import_job(retried['id'])
