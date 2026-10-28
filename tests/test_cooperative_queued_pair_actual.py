"""Opt-in finite real dual native queue qualification on GENERATED data only."""
import json
import os
from pathlib import Path

import pytest

from cooperative05_cohort_helpers import generated_pair_cohort,ACTOR
from native02_queue_helpers import freeze_source,private_config,QueueTrace,pump,stop_fixture_workers,sha
from explorer.scheduler import Supervisor
from explorer.queue_policy import GIB
from explorer.rosmaster_learning import enqueue_evaluation
from test_native02_queued_learning import SOURCES

ROOT=Path(__file__).parents[1]


@pytest.mark.skipif(os.environ.get('EXPLORER_RUN_COOPERATIVE_PAIR_ACTUAL')!='1',reason='Separate finite actual native trainers on generated paired data')
def test_current_cooperative_queue_uses_both_original_trainers_reload_and_saved_DEV(tmp_path,monkeypatch):
    original_affinity=os.sched_getaffinity(0)
    assert len(original_affinity)>=4,'Own qualifier needs four allowed CPUs to reserve two for workers'
    os.sched_setaffinity(0,set(sorted(original_affinity)[:4]))  # THIS process and its children only; no host setting.
    root=None;supervisor=None
    try:
        root,store,spec,rows,reviews,aggregate,originals=generated_pair_cohort(tmp_path,monkeypatch)
        release,frozen=freeze_source(ROOT,tmp_path/'frozen',SOURCES)
        config=private_config(tmp_path/'private-config.json',store.root);monkeypatch.setenv('EXPLORER_CONFIG',str(config))
        for key in ('HF_HUB_OFFLINE','HF_DATASETS_OFFLINE','TRANSFORMERS_OFFLINE'):monkeypatch.setenv(key,'1')
        monkeypatch.setenv('CUDA_VISIBLE_DEVICES','')
        settings={role:dict(act_steps=2,batch_size=2,image_size=32,chunk_size=2,device='cpu') for role in ('giver','receiver')}
        settings['receiver']['backend']='act'
        reservation=root.command('train',{'skill_id':spec['skill_id'],'version':1,'settings':settings},authenticated_actor=ACTOR)['result']
        assert reservation['complete_native_reservation'] and not reservation['errors'],reservation
        assert set(reservation['jobs'])=={'giver','receiver'}
        trace=QueueTrace(store);supervisor=Supervisor({'root':str(release)},store,quantum_s=3600,popen=trace.popen)
        peak_workers=0
        def finished(identity):
            nonlocal peak_workers
            peak_workers=max(peak_workers,len(supervisor.processes))
            return store.job(identity)['status'] in ('completed','failed','cancelled') and identity not in supervisor.processes
        trained={}
        for role in ('giver','receiver'):
            identity=reservation['jobs'][role]['id']
            pump(supervisor,trace,lambda:finished(identity),timeout=360,required_job_id=identity)
            job=store.job(identity);assert job['status']=='completed',job
            result=job['result'];assert result['trained'] and result['weights_changed'] and result['save_reload_passed'] and result['optimizer_steps']==2
            admitted=trace.admitted[identity];budget=admitted['metadata']['resources']
            assert budget['cpu_cores']==2 and budget['memory_bytes']==({'giver':4,'receiver':2}[role])*GIB and budget['gpu_slots']==0
            assert job['config']['_execution_commit']==frozen['source_commit']
            runtime=store.schedule_status(identity)['runtime'];assert runtime['process_exit_code']==0 and not runtime['memory_retained']
            trained[role]=job
        assert peak_workers==1,'Both original model allocations must run serially inside this own budget'
        evaluation=enqueue_evaluation(store,spec['subskills']['giver']['skill_id'],1,trained['giver']['id'])
        pump(supervisor,trace,lambda:finished(evaluation['id']),timeout=240,required_job_id=evaluation['id'])
        evaluated=store.job(evaluation['id']);assert evaluated['status']=='completed',evaluated
        assert evaluated['result']['weights_unchanged'] and evaluated['result']['optimizer_steps_executed']==0
        assert evaluated['result']['evaluation']['ACT9']['evaluated_samples']==10 and not evaluated['result']['evaluation']['sealed_test_used']
        assert evaluated['result']['source_network_requests_sent']==evaluated['result']['physical_control_requests_sent']==0
        assert len({entry['worker']['pid'] for entry in trace.public()})==3
        assert all(sha(name)==checksum for name,checksum in originals['native9_originals']['originals_sha256'].items())
        assert all(sha(release/name)==checksum for name,checksum in frozen['frozen_source_files_sha256'].items())
        # The six-dimensional policy is loaded through the existing original
        # saved-processor path, explicitly allowing ONLY this generated fixture.
        import hashlib
        import numpy as np
        from PIL import Image
        from explorer.so101_shadow import load_shadow_policy,infer_shadow
        dev=next(review for review in reviews if review['split']=='development')
        source=store.job(dev['native_episode_refs']['receiver']['import_job_id'])
        original=Path(source['result']['source_manifest_path']);raw=json.loads(original.read_bytes());sample=raw['samples'][0]
        camera=sample['frame']['camera_id'];calibration=raw['calibration_metadata']['follower']['calibration_sha256']
        policy=load_shadow_policy(store,trained['receiver']['id'],expected_camera_id=camera,expected_calibration_sha256=calibration,allow_simulation=True)
        rgb=np.asarray(Image.open(original.parent/sample['frame']['file']).convert('RGB'));domain=sample['clock_domain'];boot=raw['boot_id']
        observation=dict(camera_id=camera,frame_id=sample['frame']['frame_id'],frame_sha256=sample['frame']['sha256'],decoded_rgb_sha256=hashlib.sha256(rgb.tobytes()).hexdigest(),
            state_basis='measured_state',follower_calibration_sha256=calibration,frame_clock_domain=domain,state_clock_domain=domain,source_cutoff_clock_domain=domain,
            frame_boot_id=boot,state_boot_id=boot,source_cutoff_boot_id=boot,frame_monotonic_ns=sample['frame']['monotonic_ns'],
            state_monotonic_ns=sample['state_monotonic_ns'],source_cutoff_monotonic_ns=sample['action_monotonic_ns'],source_cutoff_basis='original_native_dispatch_time_for_offline_evaluation')
        prediction=infer_shadow(policy,rgb,np.asarray(sample['state'],np.float32),valid_mask=np.ones(6,bool),observation=observation)
        assert len(prediction['predicted_native6'])==6 and prediction['official_saved_processors_executed']
        assert not prediction['hardware_commands_dispatched'] and not prediction['physical_execution_authorized']
        report={'schema':'explorer05_actual_generated_pair_queue_qualification_v1','input_kind':'GENERATED_SOFTWARE_INPUTS',
            'actual_physical_inputs':0,'production_state_used':False,'physical_acceptance':False,'frozen_source':frozen,
            'cooperative_aggregate_sha256':aggregate['aggregate_manifest_sha256'],'reservation':reservation,'trained':trained,
            'queue_launches':trace.public(),'runtime_admissions':trace.admitted,'max_parallel_model_workers':peak_workers,
            'actual_optimizer_steps':{'giver':2,'receiver':2},'queued_native9_DEV':evaluated,'native6_offline_shadow':prediction,
            'all_workers_exited':not supervisor.processes,'originals':originals,'action_spaces_merged':False,'real_demonstrations':0}
        (tmp_path/'queue-qualification.json').write_text(json.dumps(report,indent=2,allow_nan=False)+'\n')
    finally:
        if supervisor is not None:stop_fixture_workers(supervisor)
        if root is not None:root.close()
        os.sched_setaffinity(0,original_affinity)
