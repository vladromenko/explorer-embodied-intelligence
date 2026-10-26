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
