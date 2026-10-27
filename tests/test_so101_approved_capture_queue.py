"""Generated approved04 capture cohort through the SAME real queue/resume proof.

Explicit bounded qualification only. No physical cameras, native grants or robot.
"""
import json
import os
from pathlib import Path

import numpy as np
from PIL import Image

import pytest

from test_official_queue_worker_pause import run_official_queue_pause
from test_so101_approved_capture_cohort import approved_cohort
from explorer.store import Store


@pytest.mark.skipif(os.environ.get('EXPLORER_RUN_APPROVED_SOURCE04_QUEUE')!='1',reason='Separate bounded approved04 ACT/DP actual worker qualification')
@pytest.mark.parametrize('backend',['act','diffusion'])
def test_approved_source04_original_capture_cohort_actual_queue_checkpoint_new_PID_and_equality(tmp_path,monkeypatch,backend):
    run_official_queue_pause(tmp_path,monkeypatch,backend,cohort_factory=approved_cohort)
    path=tmp_path/'qualification.json';result=json.loads(path.read_bytes())
    store=Store(tmp_path/'state');job=store.job(result['resumed_job_id'])
    outer_path=Path(job['config']['aggregate_manifest']);outer=json.loads(outer_path.read_bytes())
    view_path=outer_path.parent/outer['internal_view'];view=json.loads(view_path.read_bytes())
    assert len(outer['receipt']['source_capture_bindings'])==5
    assert len(view['episodes'])==8 and sum(row['frames'] for row in view['episodes'])==28
    sealed=set(outer['receipt']['sealed_original_episode_ids'])
    for episode in view['episodes']:
        assert episode['source_episode_id'] not in sealed and episode['split'] in ('train','development')
        with np.load(view_path.parent/episode['file'],allow_pickle=False) as data:assert 3 not in data['source_sample_index']
    if backend=='act':
        import hashlib
        from explorer.so101_shadow import load_shadow_policy,infer_shadow
        record=next(row for row in view['source_records'] if row['split']=='development')
        original=view_path.parent/record['saved_original_manifest'];raw=json.loads(original.read_bytes());row=raw['samples'][0]
        camera_id=row['frame']['camera_id'];calibration=raw['calibration_metadata']['follower']['calibration_sha256']
        with pytest.raises(ValueError,match='SIMULATION'):
            load_shadow_policy(store,job['id'],expected_camera_id=camera_id,expected_calibration_sha256=calibration)
        policy=load_shadow_policy(store,job['id'],expected_camera_id=camera_id,expected_calibration_sha256=calibration,allow_simulation=True)
        # Converted manifest retains each raw JPEG under source_originals/frames;
        # its original basename refers to the managed original import instead.
        producer=next(job for job in store.jobs(10000) if job['kind']=='station_import' and job['config']['episode_id']==raw['episode_id'])
        rgb=np.asarray(Image.open(Path(producer['result']['source_manifest_path']).parent/row['frame']['file']).convert('RGB'))
        clock=row['clock_domain'];boot=raw['boot_id']
        observation=dict(camera_id=camera_id,frame_id=row['frame']['frame_id'],frame_sha256=row['frame']['sha256'],decoded_rgb_sha256=hashlib.sha256(rgb.tobytes()).hexdigest(),
            state_basis='measured_state',follower_calibration_sha256=calibration,frame_clock_domain=clock,state_clock_domain=clock,source_cutoff_clock_domain=clock,
            frame_boot_id=boot,state_boot_id=boot,source_cutoff_boot_id=boot,frame_monotonic_ns=row['frame']['monotonic_ns'],
            state_monotonic_ns=row['state_monotonic_ns'],source_cutoff_monotonic_ns=row['action_monotonic_ns'],source_cutoff_basis='original_native_dispatch_time_for_offline_evaluation')
        prediction=infer_shadow(policy,rgb,np.asarray(row['state'],np.float32),valid_mask=np.ones(6,bool),observation=observation)
        assert prediction['execution_kind']=='offline_original_observation_policy_inference' and prediction['official_saved_processors_executed']
        assert len(prediction['predicted_native6'])==6 and not prediction['hardware_commands_dispatched'] and not prediction['physical_execution_authorized']
        result['offline_source04_shadow']=prediction
    result.update(source_profile='approved_capture',original_source_format='so101_source_capture_v1',
        original_samples_each=8,unpaused_samples_each=7,sealed_payload_excluded=True)
    path.write_text(json.dumps(result,indent=2))
