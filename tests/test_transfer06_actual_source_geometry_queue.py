"""Opt-in actual shared native6 ACT2 on generated originals carrying calibration.

This proves the software geometry/learning path, never a learned object skill.
"""
import json
import os
from pathlib import Path

import pytest

from explorer import lab_skill_workflow as flow
from explorer import transfer_geometry as geometry
from explorer import transfer_source
from explorer.scheduler import Supervisor
from explorer.queue_policy import GIB
from native02_queue_helpers import freeze_source,private_config,QueueTrace,pump,stop_fixture_workers,sha
from test_native02_queued_learning import SOURCES
from transfer06_cohort_helpers import geometry_source_cohort

ROOT=Path(__file__).parents[1]


@pytest.mark.skipif(os.environ.get('EXPLORER_RUN_TRANSFER06_SOURCE_GEOMETRY')!='1',reason='Separate bounded real native6 ACT2 on generated original captures')
def test_actual_native6_queue_keeps_source_calibration_and_official_processors_for_geometry(tmp_path,monkeypatch):
    allowed=os.sched_getaffinity(0);assert len(allowed)>=4
    os.sched_setaffinity(0,set(sorted(allowed)[:4]));supervisor=None
    try:
        store,chosen,sources,originals=geometry_source_cohort(tmp_path,monkeypatch)
        release,frozen=freeze_source(ROOT,tmp_path/'frozen',SOURCES+[
            '00-control-center/src/explorer/so101_source_evaluation.py','00-control-center/src/explorer/transfer_source.py',
            '00-control-center/src/explorer/transfer_geometry.py','shared/explorer_shared/source_geometry_context.py'])
        config=private_config(tmp_path/'private-config.json',store.root);monkeypatch.setenv('EXPLORER_CONFIG',str(config))
        job=flow.enqueue_training(store,chosen['spec']['skill_id'],1,dict(backend='act',act_steps=2,batch_size=2,image_size=32,chunk_size=2,device='cpu'))
        trace=QueueTrace(store);supervisor=Supervisor({'root':str(release)},store,quantum_s=3600,popen=trace.popen)
        pump(supervisor,trace,lambda:store.job(job['id'])['status'] in ('completed','failed','cancelled') and job['id'] not in supervisor.processes,timeout=360,required_job_id=job['id'])
        current=store.job(job['id']);assert current['status']=='completed',current
        result=current['result'];assert result['trained'] and result['weights_changed'] and result['save_reload_passed'] and result['optimizer_steps']==2
        assert result['official_saved_processors_executed'] and result['saved_processor_reload_max_abs_error']<=1e-6
        budget=trace.admitted[job['id']]['metadata']['resources'];assert budget['memory_bytes']==2*GIB and budget['cpu_cores']==2 and budget['gpu_slots']==0
        assert current['config']['_execution_commit']==frozen['source_commit']
        choice=transfer_source.inspect_source_model(store,job['id']);assert choice['technical_source_ready'] and choice['task_type']=='pick_object_to_container'
        assert not choice['eligible_for_transfer'] and not choice['source_object_rollout_qualified']
        assert 'C_OBJECT_SKILL_SOURCE_REQUIRED' not in {row['code'] for row in choice['blockers']}
        model=geometry.admitted_model(store,job['id']);envelope=geometry.saved_simulation_calibration(model);prepared,context=geometry.build_geometry(model,envelope)
        assert prepared['geometry_preview_ready'] and not prepared['physical_execution_authorized']
        assert prepared['source_model_binding']['source_calibration_sha256']==choice['source_calibration_sha256']
        for name,expected in originals['original_source_manifest_sha256'].items():assert sha(name)==expected
        assert all(sha(release/name)==expected for name,expected in frozen['frozen_source_files_sha256'].items())
        receipt=dict(schema='explorer06_actual_generated_source_geometry_queue_v1',job_id=job['id'],source_commit=frozen['source_commit'],
            original_source_kind='GENERATED_ORIGINAL_CAPTURES_WITH_PRESTART_CALIBRATION',real_experts=0,
            optimizer_steps=2,reload_max_abs_error=result['reload_max_abs_error'],source_kind=result['source_kind'],
            source_calibration_sha256=choice['source_calibration_sha256'],geometry=prepared,model_choice=choice,
            worker_admissions=trace.public(),originals=originals,original_source_manifests_unchanged=True,
            target_training_performed=False,actual_native_commands=0,source_or_target_physical_acceptance=False)
        (tmp_path/'qualification.json').write_text(json.dumps(receipt,indent=2)+'\n')
    finally:
        if supervisor is not None:stop_fixture_workers(supervisor)
        os.sched_setaffinity(0,allowed)
