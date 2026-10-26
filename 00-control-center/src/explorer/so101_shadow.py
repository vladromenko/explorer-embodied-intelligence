"""ENGINEERING_GLUE: server-bound source04 ACT6 shadow adapter, never a dispatcher.

Caller supplies authenticated current observations and verifies local transport
freshness. Historical development images are offline inference, not live shadow.
"""
from pathlib import Path

from explorer_shared.contracts import digest
from explorer_shared.so101_station import NAMES,UNITS,LOW,HIGH
from .so101_dataset_admission import verify_so101_aggregate,managed_file


def load_shadow_policy(store,model_job_id,*,expected_camera_id,expected_calibration_sha256,allow_simulation=False):
    job=store.job(model_job_id)
    if not job or job['kind']!='so101_skill_train' or job['status']!='completed':raise ValueError('Completed server SO101 whole-skill training job required')
    result=job.get('result') or {};config=job.get('config') or {}
    if config.get('backend','act')!='act' or result.get('backend','act')!='act':raise ValueError('This shadow adapter owns ACT6; Diffusion has distinct history semantics')
    path,outer,_,view=verify_so101_aggregate(store,config)
    if (result.get('aggregate_manifest_sha256')!=config['aggregate_manifest_sha256']
            or any(result.get(key)!=outer.get(key) for key in ('skill_id','skill_version','spec_sha256','action_contract_id','source_kind'))
            or not all(result.get(key) is True for key in ('trained','weights_changed','save_reload_passed'))):
        raise ValueError('Completed model/source lineage or actual optimizer/reload receipt differs')
    if result['source_kind']=='SIMULATION' and not allow_simulation:raise ValueError('SIMULATION fixture cannot qualify live physical shadow')
    if allow_simulation is not True and allow_simulation is not False:raise ValueError('Explicit software fixture mode required')
    cards=[row for row in store.skills() if row['id']==(result.get('skill_card') or {}).get('id')]
    if not cards or cards[0].get('lineage',{}).get('checkpoint_sha256')!=digest(result['checkpoint_sha256']):raise ValueError('Registered model card/checkpoint lineage required')
    checkpoint=Path(result['checkpoint_dir']).resolve()
    for name,expected in result['checkpoint_sha256'].items():
        if Path(name).name!=name:raise ValueError('Invalid checkpoint member')
        managed_file(store,checkpoint/name,expected)
    calibration=((outer.get('source_calibration_metadata') or {}).get('calibration_metadata') or {}).get('follower') or {}
    cameras={camera for row in view['source_records'] for camera in row['source_evidence']['camera_ids']}
    if expected_camera_id not in cameras or expected_calibration_sha256!=calibration.get('calibration_sha256'):raise ValueError('Current source camera/follower calibration differs from training cohort')
    from explorer_shared.learning.act_policy import NativeACTPolicy
    policy=NativeACTPolicy(checkpoint,result['checkpoint_sha256'],expected_contract_id=outer['action_contract_id'],expected_names=NAMES,
        expected_units=UNITS,expected_low=LOW,expected_high=HIGH,expected_camera_id=expected_camera_id,expected_calibration_sha256=expected_calibration_sha256)
    policy.shadow_lineage=dict(model_job_id=model_job_id,skill_id=outer['skill_id'],skill_version=outer['skill_version'],spec_sha256=outer['spec_sha256'],
        aggregate_manifest_sha256=config['aggregate_manifest_sha256'],checkpoint_sha256=digest(result['checkpoint_sha256']),source_kind=result['source_kind'],
        software_fixture_mode=allow_simulation,physical_execution_authorized=False)
    return policy


def infer_shadow(policy,rgb,measured_native6,*,valid_mask,observation):
    lineage=getattr(policy,'shadow_lineage',None)
    if not isinstance(lineage,dict):raise ValueError('Load the policy through the trusted server source adapter')
    predicted=policy.predict(rgb,measured_native6,valid_mask=valid_mask,observation=observation)
    return dict(schema='explorer_so101_shadow_inference_v1',lineage=lineage,observation_receipt=policy.last_receipt,
        action_names=list(NAMES),action_units=list(UNITS),predicted_native6=predicted.tolist(),
        execution_kind='fresh_observation_policy_inference_without_dispatch' if observation['source_cutoff_basis']=='native_observation_packet_created' else 'offline_original_observation_policy_inference',
        official_saved_processors_executed=True,hardware_commands_dispatched=False,physical_execution_authorized=False,
        independent_full_task_success=False,transport_freshness_verified_by_this_pure_adapter=False)
