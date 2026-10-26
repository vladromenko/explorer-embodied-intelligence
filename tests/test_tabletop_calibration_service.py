"""Source-only cached JPEG wizard qualifications; no camera/device/motor owner."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace
import uuid

import cv2
import numpy as np
import pytest

from explorer_shared.contracts import digest
from explorer_shared.lab_vision_service import LabVisionService
from explorer_shared.tabletop_calibrate import CalibrationBlocked,DEFAULT_BOARD
from explorer_shared.tabletop_calibration_service import TabletopCalibrationService
from explorer_shared.tabletop_grounding import CachedFrame


def identity():return str(uuid.uuid4())


def rendered_views():
    board=cv2.aruco.CharucoBoard((6,8),.03,.0225,cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50));board.setLegacyPattern(False)
    pattern=board.generateImage((1440,1920),marginSize=0,borderBits=1)
    plane=np.asarray([[0,0,0],[.18,0,0],[.18,.24,0],[0,.24,0]],np.float32)
    raster=np.asarray([[0,0],[1439,0],[1439,1919],[0,1919]],np.float32)
    camera=np.asarray([[700.,0,320.],[0,710.,240.],[0,0,1.]])
    frames=[]
    for index in range(12):
        rotation=np.asarray([[-.26,.02,.24][index%3],[-.24,.22,-.02][index//3%3],(index-5)*.015])
        centroid=np.asarray([[190,320,450][index%3],[140,240,340][index//3%3]],float);z=[.59,.68,.76][index%3]
        middle=cv2.Rodrigues(rotation)[0]@board.getChessboardCorners().mean(axis=0)
        translation=np.asarray([(centroid[0]-320)*z/700-middle[0],(centroid[1]-240)*z/710-middle[1],z-middle[2]])
        pixels=cv2.projectPoints(plane,rotation,translation,camera,np.zeros(5))[0].reshape(-1,2)
        image=cv2.warpPerspective(pattern,cv2.getPerspectiveTransform(raster,pixels),(640,480),borderValue=255)
        okay,encoded=cv2.imencode('.jpg',image,[cv2.IMWRITE_JPEG_QUALITY,95]);assert okay
        frames.append(CachedFrame('fixture-camera',f'fixture-{index}',encoded.tobytes(),640,480,'fixture-domain','fixture-boot',1000+index,'synthetic_projected_render'))
    return frames


@pytest.fixture(scope='module')
def images():return rendered_views()


def rig(tmp_path,images):
    cache={frame.frame_id:frame for frame in images};current=[images[0]];tick=[2000];profile={'camera':'fixture-camera','fixed_profile':'software-only'}
    context={'robot_id':'software-robot','base_frame':'software-base','mount_id':'software-fixed-mount','robot_calibration_sha256':'a'*64}
    service=TabletopCalibrationService(tmp_path/'wizard',lambda key:cache.get(key) if key else current[0],profile_provider=lambda:deepcopy(profile),context_provider=lambda:deepcopy(context),clock_domain='fixture-domain',boot_id='fixture-boot',clock=lambda:tick[0])
    return service,current,tick,profile,context


def capture(service,frame,state=None,purpose='intrinsics',request_id=None):
    return service.capture(dict(request_id=request_id or identity(),session_id=state['session']['session_id'] if state else None,expected_revision=state['session']['revision'] if state else None,frame_id=frame.frame_id,frame_sha256=frame.sha256,purpose=purpose))


def collect(service,current,images):
    state=None
    for index,frame in enumerate(images):
        current[0]=frame;state=capture(service,frame,state,'reference' if index==11 else 'intrinsics')
    return state


def form(state):
    return dict(operator_id='SOFTWARE_FIXTURE_NOT_HUMAN_MEASUREMENT',reference_capture_id=state['session']['captures'][-1]['capture_id'],printed_ruler_length_mm=100.,printed_ruler_sigma_mm=.2,measurement_sigma_mm=.5,board_thickness_mm=0.,base_corners_mm=[[0,0,0],[180,0,0],[180,-240,0],[0,-240,0]],printed_scale_checked=True,board_flat_on_table_confirmed=True,camera_fixed_confirmed=True,robot_base_fixed_confirmed=True)


def solve(service,state,measurements=None):
    result=service.solve(dict(request_id=identity(),session_id=state['session']['session_id'],expected_revision=state['session']['revision'],measurements=measurements or form(state)))
    service.worker.join(timeout=30)
    assert not service.worker.is_alive()
    return service.status(state['session']['session_id'])


def test_real_JPEG_capture_labels_duplicate_rejection_reload_and_source_tamper(tmp_path,images):
    service,current,tick,profile,context=rig(tmp_path,images);request=identity()
    state=capture(service,images[0],request_id=request);row=state['session']['captures'][0]
    assert row['quality']['usable'] and row['quality']['detected_corners']>=12
    assert row['quality']['board_corner_guide']['labels']==['A','B','C','D']
    assert row['quality']['board_corner_guide']['robot_extrinsics_inferred'] is False
    same=capture(service,images[0],request_id=request);assert same['session']['revision']==state['session']['revision']
    with pytest.raises(ValueError,match='reused'):capture(service,images[1],state,request_id=request)
    duplicate=capture(service,images[0],state)
    assert duplicate['steps'][1]['good_views']==1 and duplicate['steps'][1]['rejected_views']==1
    assert 'duplicate_original_view' in duplicate['session']['captures'][-1]['quality']['reasons'][-1]
    raw,meta=service.media(state['session']['session_id'],row['capture_id']);assert raw==images[0].jpeg and meta['frame_sha256']==images[0].sha256
    service.close();other,*_=rig(tmp_path,images)
    assert other.status()['session']['revision']==duplicate['session']['revision']
    source=other.root/row['jpeg_file'];source.write_bytes(b'tamper')
    with pytest.raises(CalibrationBlocked,match='JPEG_changed'):other._load_frames(duplicate['session'])
    other.close()


@pytest.mark.parametrize('fault',['sha','stale','boot','profile','revision'])
def test_frame_and_generation_fences_reject_source_mixing(tmp_path,images,fault):
    service,current,tick,profile,context=rig(tmp_path,images);state=capture(service,images[0]);frame=images[1];current[0]=frame
    body=dict(request_id=identity(),session_id=state['session']['session_id'],expected_revision=state['session']['revision'],frame_id=frame.frame_id,frame_sha256=frame.sha256)
    if fault=='sha':body['frame_sha256']='0'*64
    elif fault=='stale':tick[0]=3_000_000_000
    elif fault=='boot':service.boot_id='another-boot'
    elif fault=='profile':profile['fixed_profile']='changed'
    else:body['expected_revision']='outdated'
    with pytest.raises(ValueError):service.capture(body)
    assert len(service.status()['session']['captures'])==1;service.close()


def test_blank_view_is_retained_as_rejected_not_fake_coverage(tmp_path,images):
    okay,encoded=cv2.imencode('.jpg',np.zeros((480,640,3),np.uint8));assert okay
    blank=CachedFrame('fixture-camera','blank',encoded.tobytes(),640,480,'fixture-domain','fixture-boot',1000,'synthetic_blank')
    service,current,*_=rig(tmp_path,[blank]);state=capture(service,blank)
    assert state['steps'][1]['good_views']==0 and state['steps'][1]['rejected_views']==1
    assert service.media(state['session']['session_id'],state['session']['captures'][0]['capture_id'])[0]==blank.jpeg;service.close()


def test_actual_async_OpenCV_solve_persists_originals_and_cannot_accept_synthetic(tmp_path,images):
    service,current,*_=rig(tmp_path,images);state=collect(service,current,images)
    assert state['ready_to_solve'] and state['requires_manual_JSON_or_terminal'] is False
    result=solve(service,state);session=result['session']
    assert session['last_failure'] is None,session['last_failure']
    solution=session['solutions'][0];assert solution['provenance']=='synthetic' and service._bundle(solution).body['accepted_view_count']>=10
    assert len(solution['implementation'])==3 and solution['overlay']['physical_accuracy_verified'] is False
    calibrated=service._bundle(solution);assert calibrated.body['intrinsics_residual_rmse_px']<.8
    assert calibrated.body['physical_execution_allowed'] is False
    assert session['measurement_form']==form(state)
    assert len([name for name in calibrated.body['source_files_sha256'] if name.endswith('.jpg')])>=10
    with pytest.raises(CalibrationBlocked,match='synthetic_calibration'):
        service.accept(dict(request_id=identity(),session_id=session['session_id'],expected_revision=session['revision'],solution_id=solution['solution_id'],solution_sha256=solution['artifact_sha256'],confirm_axes_overlay=True,confirm_mount_unchanged=True,expected_active_calibration_sha256=None))
    assert service.accepted() is None;service.close()


@pytest.mark.parametrize('fault',['matrix','reference','robot_context','missing_confirmation','non_object'])
def test_structured_measurements_cannot_inject_extrinsics_or_rebind_reference(tmp_path,images,fault):
    service,current,tick,profile,context=rig(tmp_path,images);state=collect(service,current,images);measure=form(state)
    if fault=='matrix':measure['base_from_camera']=np.eye(4).tolist()
    elif fault=='reference':measure['reference_capture_id']=state['session']['captures'][0]['capture_id']
    elif fault=='robot_context':context['robot_calibration_sha256']='b'*64
    elif fault=='missing_confirmation':measure['robot_base_fixed_confirmed']=False
    else:measure=[]
    with pytest.raises(ValueError):service.solve(dict(request_id=identity(),session_id=state['session']['session_id'],expected_revision=state['session']['revision'],measurements=measure))
    assert service.worker is None and service.status()['session']['solutions']==[];service.close()


def test_LabVision_wizard_uses_existing_camera_without_segmentation_and_pins_exact_bytes(tmp_path,images,monkeypatch):
    def forbidden(*args,**kwargs):raise AssertionError('Device owner must never be constructed')
    monkeypatch.setattr(cv2,'VideoCapture',forbidden)
    original=SimpleNamespace(**{key:getattr(images[0],key) for key in ('frame_id','jpeg','width','height','monotonic_ns','timestamp_basis')})
    current=[original]
    source=SimpleNamespace(camera_id='fixture-camera',latest=lambda:current[0],metadata=lambda:{'available':True})
    config=tmp_path/'vision.json';config.write_text('{}');before=config.read_bytes()
    context={'robot_id':'software-robot','base_frame':'software-base','mount_id':'software-mount'}
    lab=LabVisionService(source,None,clock_domain='fixture-domain',boot_id='fixture-boot',config_path=config,calibration_dir=tmp_path/'wizard',calibration_context_provider=lambda:context,clock=lambda:2000)
    status=lab.command('calibration/status',{});frame=status['current_frame'];assert frame['frame_sha256']==images[0].sha256
    current[0]=None
    raw,metadata=lab.calibration_preview(frame['frame_id']);assert raw==images[0].jpeg and metadata==frame
    state=lab.command('calibration/capture',dict(request_id=identity(),frame_id=frame['frame_id'],frame_sha256=frame['frame_sha256'],purpose='reference'))
    assert state['session']['captures'][0]['quality']['usable'] and not state['physical_actions_emitted']
    assert lab.calibration_board('pdf')[0].startswith(b'%PDF') and config.read_bytes()==before
    assert lab.snapshot()['perception']['available'] is False
    lab.close()
    with pytest.raises(ValueError,match='closed'):lab.command('calibration/capture',{})


def test_measured_protocol_accept_is_CPU_fixture_only_CAS_bound_and_preserves_config(tmp_path,images):
    # Emulate the trusted existing-camera timestamp contract in a SOFTWARE test.
    # Known rendered geometry is not a physical calibration/accuracy qualification.
    frames=[CachedFrame(f.camera_id,f.frame_id,f.jpeg,f.width,f.height,f.clock_domain,f.boot_id,f.monotonic_ns,'host_receive_estimate') for f in images]
    service,current,tick,profile,context=rig(tmp_path,frames);state=collect(service,current,frames);result=solve(service,state)
    assert result['session']['last_failure'] is None,result['session']['last_failure']
    solution=result['session']['solutions'][0]
    session=result['session'];body=dict(request_id=identity(),session_id=session['session_id'],expected_revision=session['revision'],solution_id=solution['solution_id'],solution_sha256=solution['artifact_sha256'],confirm_axes_overlay=True,confirm_mount_unchanged=True,expected_active_calibration_sha256=None)
    original=service.root/solution['artifact_file'];before=original.read_bytes()
    with pytest.raises(ValueError,match='Active calibration changed'):service.accept(dict(body,request_id=identity(),expected_active_calibration_sha256='z'*64))
    assert service.accepted() is None
    accepted=service.accept(body);assert accepted['active']['artifact_sha256']==solution['artifact_sha256']
    assert service.accept(body)['session']['revision']==accepted['session']['revision']
    assert original.read_bytes()==before and service._bundle(solution).body['physical_execution_allowed'] is False
    assert service.accepted()['accept_event_id']==accepted['active']['accept_event_id']
    # The accepted index is invalid without the exact immutable accepted event.
    with sqlite3.connect(service.path) as db:db.execute("UPDATE active SET accept_event_id='forged'")
    with pytest.raises(ValueError,match='event binding'):service.accepted()
    service.close()


def test_saved_reference_tamper_is_rejected_before_acceptance(tmp_path,images):
    frames=[CachedFrame(f.camera_id,f.frame_id,f.jpeg,f.width,f.height,f.clock_domain,f.boot_id,f.monotonic_ns,'host_receive_estimate') for f in images]
    service,current,*_=rig(tmp_path,frames);result=solve(service,collect(service,current,frames));session=result['session'];solution=session['solutions'][0]
    reference=next(item for item in session['captures'] if item['capture_id']==solution['reference_capture_id'])
    (service.root/reference['observation_file']).write_text('{}')
    with pytest.raises(CalibrationBlocked,match='corner_observation_changed'):
        service.accept(dict(request_id=identity(),session_id=session['session_id'],expected_revision=session['revision'],solution_id=solution['solution_id'],solution_sha256=solution['artifact_sha256'],confirm_axes_overlay=True,confirm_mount_unchanged=True,expected_active_calibration_sha256=None))
    assert service.accepted() is None;service.close()


def test_failed_solve_is_durable_and_a_requested_snapshot_resumes_after_restart(tmp_path,images):
    service,current,*_=rig(tmp_path,images);state=collect(service,current,images)
    service.start=lambda:None  # Simulate process exit before its authorized CPU solve starts.
    queued=service.solve(dict(request_id=identity(),session_id=state['session']['session_id'],expected_revision=state['session']['revision'],measurements=form(state)))
    assert queued['session']['state']=='QUEUED';service.close()
    other,current,*_=rig(tmp_path,images);current[0]=images[-1];resumed=other.status()
    assert other.worker is not None
    other.worker.join(timeout=30);finished=other.status()
    assert finished['session']['solutions'] and finished['session']['last_failure'] is None
    # Retained source tampering on another explicitly requested solve becomes a
    # durable failed result; it cannot erase the prior candidate or raw evidence.
    state=finished;row=state['session']['captures'][0];(other.root/row['jpeg_file']).write_bytes(b'changed')
    failed=solve(other,state);assert failed['session']['last_failure']['reasons']==['stored_original_calibration_JPEG_changed']
    assert len(failed['session']['solutions'])==1 and other.accepted() is None
    other.close()


@pytest.mark.parametrize('fault',['stale','mount','profile','closed'])
def test_accept_rechecks_source_and_trusted_identity_after_slow_detection(tmp_path,images,monkeypatch,fault):
    import explorer_shared.tabletop_calibration_service as module
    frames=[CachedFrame(f.camera_id,f.frame_id,f.jpeg,f.width,f.height,f.clock_domain,f.boot_id,f.monotonic_ns,'host_receive_estimate') for f in images]
    service,current,tick,profile,context=rig(tmp_path,frames);result=solve(service,collect(service,current,frames));session=result['session'];solution=session['solutions'][0]
    original=module.observe_charuco
    def delayed(*args,**kwargs):
        observation=original(*args,**kwargs)
        if fault=='stale':tick[0]=3_000_000_000
        elif fault=='mount':context['mount_id']='different-mount'
        elif fault=='profile':profile['fixed_profile']='different-profile'
        else:service.closed=True
        return observation
    monkeypatch.setattr(module,'observe_charuco',delayed)
    with pytest.raises(ValueError):
        service.accept(dict(request_id=identity(),session_id=session['session_id'],expected_revision=session['revision'],solution_id=solution['solution_id'],solution_sha256=solution['artifact_sha256'],confirm_axes_overlay=True,confirm_mount_unchanged=True,expected_active_calibration_sha256=None))
    assert service.accepted() is None;service.close()
