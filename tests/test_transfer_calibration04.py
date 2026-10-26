"""Synthetic camera measurements only; fitting never opens an arm or camera."""
from copy import deepcopy

import numpy as np
import pytest

from explorer_shared.kinematics import URDFChain,transform
from explorer_shared.transfer_mapping04 import SourceCoordinates,TransferBlocked,sha
from explorer_shared.transfer_calibration import fit_joint_reference,fit_aperture_reference,calibrated_aperture,command_for_aperture,register_task_frames,task_registration
from test_transfer_mapping04 import ROOT,SOURCE,CALIBRATION,encoded


def test_joint_registration_uses_external_poses_and_independent_holdout_views():
    chain=URDFChain.from_urdf(SOURCE,'base_link','gripper_frame_link');raw=encoded(CALIBRATION);coordinates=SourceCoordinates(raw,sha(raw))
    offsets=np.array([.01,-.015,.01,-.007,.005]);views=[]
    for index in range(6):
        native=np.array([-12+index*5,8-index*5,-10+index*5,11-index*5,-12+index*5,50.])
        measured=chain.forward(coordinates.arm_encoder_delta_radians(native)+offsets)
        views.append(dict(native6=native.tolist(),measured_base_from_tool=measured.tolist(),uncertainty_rad=.02,frame_sha256=str(index)*64,calibration_id='synthetic-source-camera',pose_basis='synthetic_camera_geometry'))
    profile=fit_joint_reference(coordinates,chain,views,provenance='synthetic')
    assert profile['heldout_view_count']==2 and profile['fit_view_count']==4
    assert not profile['physical_execution_authorized'] and not profile['controller_calibration_modified']
    for index,name in enumerate(chain.names):assert abs(profile['joints'][name]['offset_rad']-offsets[index])<.005
    duplicate=deepcopy(views);duplicate[-1]['frame_sha256']=duplicate[0]['frame_sha256']
    with pytest.raises(TransferBlocked,match='DUPLICATE'):fit_joint_reference(coordinates,chain,duplicate,provenance='synthetic')


def test_metric_gripper_curves_need_actual_scale_span_and_never_invent_force():
    source=[dict(role='source',native_gripper=value,aperture_m=gap,uncertainty_m=.001,frame_sha256=str(index)*64,calibration_id='scale-source') for index,(value,gap) in enumerate([(0,.002),(50,.03),(100,.06)])]
    target=[dict(role='target',native_gripper=value,aperture_m=gap,uncertainty_m=.001,frame_sha256=str(index+3)*64,calibration_id='scale-target') for index,(value,gap) in enumerate([(30,.06),(100,.03),(170,.002)])]
    source_curve=fit_aperture_reference(source,role='source',provenance='synthetic');target_curve=fit_aperture_reference(target,role='target',provenance='synthetic')
    gap=calibrated_aperture(source_curve,50);assert gap==.03
    assert command_for_aperture(target_curve,gap)==100
    assert not target_curve['force_calibrated'] and target_curve['empty_closed_is_not_object_squeeze_target']
    with pytest.raises(TransferBlocked,match='OUTSIDE_MEASURED'):command_for_aperture(target_curve,.2)
    target[-1]['aperture_m']=.1
    with pytest.raises(TransferBlocked,match='MONOTONICITY'):fit_aperture_reference(target,role='target',provenance='synthetic')


def test_source_and_target_frames_preserve_object_relative_effect_and_calibration_ids():
    views=[]
    for index,(role,point) in enumerate([('source_object',[.2,0,0]),('source_goal',[.2,.2,0]),('target_object',[.3,0,0]),('target_goal',[.1,.25,0])]):
        views.append(dict(role=role,base_from_frame=transform(point).tolist(),frame_sha256=str(index)*64,calibration_id='camera-'+role,uncertainty_m=.002))
    profile=register_task_frames(views,provenance='synthetic');registration=task_registration(profile,'object')
    assert np.allclose(registration.transfer(transform([.2,0,.1]))[:3,3],[.3,0,.1])
    assert len(registration.calibration_ids)==2 and not profile['physical_execution_authorized']
    bad=deepcopy(views);bad[0].pop('calibration_id')
    with pytest.raises(TransferBlocked,match='CALIBRATED_METRIC'):register_task_frames(bad,provenance='synthetic')


def test_durable_wizard_retains_rejected_frames_and_rejects_stale_labels(tmp_path,monkeypatch):
    import io
    import uuid
    from PIL import Image
    from explorer.store import Store
    from explorer import transfer_calibration as wizard
    monkeypatch.setattr(wizard,'_project_id',lambda:'06')
    store=Store(tmp_path/'state')
    audit=dict(all_computable_mapping_A_resolved=True,source_profile=dict(source_calibration_sha256='a'*64,source_urdf_sha256='b'*64),A=[],C=[dict(code='SOURCE_OBJECT_POLICY_REQUIRED')])
    body=dict(skill_id=str(uuid.uuid4()),source_version=1,target_version=2,request_id=str(uuid.uuid4()))
    state=wizard.create_session(store,body,audit=audit)
    assert state['experiment_slug']=='cross_embodiment_transfer'
    assert wizard.create_session(store,body,audit=audit)['revision']==state['revision']
    image=Image.fromarray(np.tile(np.arange(96,dtype=np.uint8)[None,:,None],(64,1,3))*2)
    stream=io.BytesIO();image.save(stream,format='JPEG');raw=stream.getvalue()
    metadata=dict(width=96,height=64,clock_domain='camera',boot_id='boot',monotonic_ns=100)
    capture=lambda role:dict(bytes=raw,metadata=metadata)
    def measurement(step,role,data,meta,clicks,state):
        return dict(frame_sha256=sha(data),calibration_id='calib',clock_domain='another-clock',boot_id='boot',state_monotonic_ns=100,metric_geometry_verified=True,source_calibration_sha256='a'*64)
    request=str(uuid.uuid4())
    saved=wizard.capture_view(store,state['wizard_id'],'joint_reference_views','source',expected_revision=state['revision'],request_id=request,capture_provider=capture,measurement_provider=measurement)
    assert saved['steps'][0]['rejected_views']==1 and saved['steps'][0]['accepted_views']==0
    assert 'A_FRAME_STATE_CLOCK_MAPPING_REQUIRED' in saved['captures'][0]['quality']['reasons']
    assert saved['captures'][0]['native_POST_executed'] is False
    assert __import__('pathlib').Path(saved['captures'][0]['frame_file']).read_bytes()==raw
    repeated=wizard.capture_view(store,state['wizard_id'],'joint_reference_views','source',expected_revision=state['revision'],request_id=request,capture_provider=capture,measurement_provider=measurement)
    assert repeated['revision']==saved['revision'] and len(repeated['captures'])==1
    with pytest.raises(ValueError,match='refresh'):
        wizard.capture_view(store,state['wizard_id'],'joint_reference_views','source',expected_revision=state['revision'],request_id=str(uuid.uuid4()),capture_provider=capture,measurement_provider=measurement)
    assert wizard.session(store,state['wizard_id'])['revision']==saved['revision']


def test_simulation_wizard_cannot_authorize_physical_calibration(tmp_path,monkeypatch):
    import uuid
    from explorer.store import Store
    from explorer import transfer_calibration as wizard
    monkeypatch.setattr(wizard,'_project_id',lambda:'06')
    audit=dict(all_computable_mapping_A_resolved=True,source_profile={},A=[],C=[])
    state=wizard.create_session(Store(tmp_path),dict(skill_id=str(uuid.uuid4()),source_version=1,target_version=2,domain='simulation'),audit=audit)
    assert state['calibration_complete'] and not state['physical_calibration_ready']
    assert not state['physical_execution_authorized'] and not state['target_dataset_adaptation_completed']
