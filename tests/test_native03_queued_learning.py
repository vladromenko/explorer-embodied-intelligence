"""Opt-in real03 queue proof reusing the unchanged common02 executor harness.

Only the producer fixture and authenticated queue project differ. Generated
source inputs, private nodes=[] and bounded CPU apply; no robot transport exists.
"""
import hashlib
import json
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import test_native02_queued_learning as common
from native03_consumer_helpers import native03_aggregate


@pytest.mark.skipif(os.environ.get('EXPLORER_RUN_NATIVE03_QUEUE_QUALIFICATION')!='1',reason='Separate bounded CPU2/4GiB/swap0 native03 qualification')
def test_real_native03_source_to_common_supervisor_ACT4_saved_DEV_no_physical_requests(tmp_path,monkeypatch):
    captured={}
    def source(path,patch):
        result=native03_aggregate(path,patch);captured['source']=result[2];return result
    monkeypatch.setattr(common,'native_aggregate',source)
    post=TestClient.post
    def scoped(self,url,**kwargs):
        if str(url).endswith('/train'):
            kwargs['json']=dict(kwargs['json'],project_id='03',input_mode='so101_leader')
        return post(self,url,**kwargs)
    monkeypatch.setattr(TestClient,'post',scoped)
    # Same executable test body: real ASGI→Supervisor→bootstrap→job_runner,
    # 4 ACT updates/reload and new-process DEV10 with0 optimizer updates.
    common.test_real_supervisor_bootstrap_dispatch_native02_ACT4_then_queued_saved_DEV(tmp_path,monkeypatch)
    original=tmp_path/'queue-qualification.json';raw=original.read_bytes();report=json.loads(raw)
    train=report['trained_job_id'];evaluation=report['queued_DEV_evaluation']
    from explorer.store import Store
    store=Store(tmp_path/'nx');trained=store.job(train)
    assert trained['project_id']==evaluation['project_id']=='03'
    assert trained['result']['optimizer_steps']==4 and trained['result']['save_reload_passed']
    assert evaluation['result']['project_id']=='03' and evaluation['result']['optimizer_steps_executed']==0
    assert all(job['project_id']=='03' and job['kind']=='retarget_import' for job in captured['source']['imports'])
    assert all(method=='GET' and host=='software-nano.invalid' for method,host,_ in captured['source']['requests'])
    assert len({launch['worker']['pid'] for launch in report['launches']})==2
    assert len(report['frozen_source']['frozen_source_files_sha256'])==11
    # Retain the unmodified common harness receipt even though its legacy schema
    # name includes02. This separate binding declares the actual source/project.
    result={'schema':'explorer_native03_queued_common_executor_qualification_v1','project_id':'03',
        'original_common_harness_receipt':str(original),'original_common_harness_receipt_sha256':hashlib.sha256(raw).hexdigest(),
        'common_harness_legacy_schema_name_not_source_identity':report['schema'],
        'frozen_source':report['frozen_source'],'train_job_id':train,'evaluation_job_id':evaluation['id'],
        'actual_optimizer_updates':4,'queued_DEV_optimizer_updates':0,'queued_DEV_samples':10,
        'same_shared_ASGI_Supervisor_bootstrap_job_runner_used':True,'source_native_imports':5,
        'source_project_ids':sorted({job['project_id'] for job in captured['source']['imports']}),
        'physical_requests_sent':0,'real_demonstrations':0,'all_inputs_are_software_fixtures':True,
        'production_state_used':False,'independent_full_task_success':False,'source_requests':captured['source']['requests']}
    (tmp_path/'native03-queue-qualification.json').write_text(json.dumps(result,indent=2,allow_nan=False))
