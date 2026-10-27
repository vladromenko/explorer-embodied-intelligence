"""Injected dummy weights/evaluator check exact source and original02/03 routing."""
from pathlib import Path
from types import SimpleNamespace

import pytest

from explorer import rosmaster_eval_job as module
from explorer.rosmaster_learning import evaluation_request
from test_rosmaster_learning import model_fixture


def evaluator_fixture(tmp_path,*,project='02'):
    store,trained,path,model,corpus=model_fixture(tmp_path)
    if project=='03':
        source=store.create_job('rosmaster_skill_train','03',trained['config']);store.complete_job(source['id'],trained['result']);trained=store.job(source['id'])
    config=evaluation_request(store,corpus.skill_id,1,trained['id'])
    job=store.create_job('rosmaster_skill_eval',project,config)
    return store,job


def loaders():
    return {'loader':lambda path:SimpleNamespace(policy=SimpleNamespace(config=SimpleNamespace(chunk_size=2))),
        'evaluator':lambda *args:{'sealed_test_used':False,'learned_policy_rollout_executed':False,'fixture_not_actual_inference':True}}


@pytest.mark.parametrize('project',['02','03'])
def test_only_trusted_execution_commit_is_removed_and_matches_exact_archive_marker(tmp_path,monkeypatch,project):
    store,job=evaluator_fixture(tmp_path,project=project)
    identity='d'*40;release=tmp_path/'releases'/identity;release.mkdir(parents=True);(release/'RELEASE_COMMIT').write_text(identity+'\n')
    monkeypatch.setattr(module,'__file__',str(release/'00-control-center/src/explorer/rosmaster_eval_job.py'))
    job['config']['_execution_commit']=identity
    result=module.run_rosmaster_evaluation(store,job,**loaders())
    assert result['execution_source_commit']==identity and result['project_id']==project and not result['dispatch_performed']
    assert job['config']['_execution_commit']==identity  # Never mutate the queued original.


@pytest.mark.parametrize('case',['null','malformed','foreign_commit','missing_marker','marker_tamper','extra','wrong_origin'])
def test_untrusted_runtime_or_route_fields_reject_before_any_model_load(tmp_path,monkeypatch,case):
    store,job=evaluator_fixture(tmp_path);identity='d'*40;release=tmp_path/'releases'/identity;release.mkdir(parents=True)
    (release/'RELEASE_COMMIT').write_text(identity+'\n');monkeypatch.setattr(module,'__file__',str(release/'00-control-center/src/explorer/rosmaster_eval_job.py'))
    job['config']['_execution_commit']=identity
    if case=='null':job['config']['_execution_commit']=None
    elif case=='malformed':job['config']['_execution_commit']='../not-source'
    elif case=='foreign_commit':job['config']['_execution_commit']='e'*40
    elif case=='missing_marker':(release/'RELEASE_COMMIT').unlink()
    elif case=='marker_tamper':(release/'RELEASE_COMMIT').write_text('e'*40)
    elif case=='extra':job['config']['_gpu_binding_sha256']='a'*64
    else:job['project_id']='03'
    def forbidden(*args,**kwargs):raise AssertionError('Invalid source/route config cannot load a policy')
    with pytest.raises(ValueError):module.run_rosmaster_evaluation(store,job,loader=forbidden,evaluator=forbidden)
