"""Known encoder arithmetic + cache-only observed metadata wizard, no motors."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import time

import numpy as np
import pytest

from explorer_shared import native_leader_registration as registration
from explorer_shared.native_leader_geometry import registered_native_leader_geometry
from explorer_shared.pi_native_retarget import PiNativeRetargetService
from explorer_shared.node_agent import CameraSource
from explorer_shared.so101_station import NAMES
from test_pi_native_retarget import Peer,Station

ROOT=Path(__file__).resolve().parents[1]


@pytest.fixture
def context(tmp_path):
    # Numerical fixture only; production defaults bind the already-known actual
    # leader SHA9512..., never a bus connection or rewritten calibration file.
    calibration={name:{'id':index+1,'drive_mode':int(index==1),'homing_offset':300+index,
                       'range_min':0,'range_max':4095} for index,name in enumerate(NAMES)}
    raw=json.dumps(calibration,indent=2).encode();path=tmp_path/'existing-leader.json';path.write_bytes(raw)
    return registration.NativeLeaderContext(ROOT,path,expected_sha256=hashlib.sha256(raw).hexdigest())


def measured(context,grip=50):
    return {'positions':[0.,0.,0.,0.,0.,float(grip)],'calibration_sha256':context.coordinates.sha256,
            'state_source':'measured_leader','clock_domain':'pi:pi-boot','boot_id':'pi-boot',
            'sample_id':'original-cached-sample','monotonic_ns':time.monotonic_ns()}


def record(wizard,context,kind,grip=50):
    body={'kind':kind,'actor_id':'mock-observed-owner','observing':True,
          'user_observed_test_confirmed':True,'axes_and_orientation_confirmed':kind=='task_axes'}
    reference={'servo_deg':[90]*6,'boot_id':'nano-boot','clock_domain':'nano:nano-boot',
               'reference_read_native_monotonic_ns':100,'state_source':'command_estimate','measured':False}
    return wizard.capture(body,measured(context,grip),target_reference=reference if kind=='task_axes' else None,
                          source_frame={'frame_id':'source-cached-original','monotonic_ns':90,
                                        'jpeg':b'\xff\xd8original-cached-source\xff\xd9'})


def test_computable_A_has_exact_encoder_scale_homing_not_reapplied_and_known_gripper_commands(context):
    status=context.status();assert status['all_computable_A_resolved']
    assert status['known_target_gripper_commands']=={'open':30,'empty_visually_closed':170}
    assert not status['device_recalibration_requested'] and not status['metric_aperture_known']
    expected=np.pi/100
    assert status['source_coordinate_profile']['joints']['wrist_roll']['scale_rad_per_native_unit']==pytest.approx(expected)
    assert status['source_coordinate_profile']['joints']['shoulder_lift']['scale_rad_per_native_unit']==pytest.approx(-expected)
    assert all(row['homing_offset_reapplied'] is False for row in status['source_coordinate_profile']['joints'].values())
    pose=context.preview(measured(context)['positions'])
    assert np.asarray(pose['source_base_from_tool']).shape==(4,4) and pose['physical_commands']==0


def test_known_source_arithmetic_is_available_without_new_generic_joint_pairs(tmp_path,context,monkeypatch):
    monkeypatch.setattr(registration,'NativeLeaderContext',lambda root,path:context)
    geometry=registered_native_leader_geometry(tmp_path/'config',source_calibration_path=context.calibration_path)
    status=geometry.status()
    assert status['state']=='UNREGISTERED' and status['A']['all_computable_A_resolved']
    assert status['missing_cross_map']==['OBSERVED_TASK_AXES_AND_ORIENTATION','TWO_OBSERVED_LEADER_GRIPPER_ENDPOINTS']
    assert 'source_joint_points' not in json.dumps(status)
    config=tmp_path/'config';config.mkdir();(config/'native-leader-registration.json').write_text('{')
    recovered=registered_native_leader_geometry(config,source_calibration_path=context.calibration_path)
    assert recovered.status()['A']['all_computable_A_resolved']
    assert recovered.status()['cross_map_error']=='INVALID_CROSS_MAP:JSONDecodeError'


def test_three_small_observed_captures_accept_derived_profile_and_keep_original_bytes(tmp_path,context):
    wizard=registration.NativeLeaderRegistrationWizard(context,tmp_path/'private-app-config')
    original=context.calibration_path.read_bytes()
    with pytest.raises(ValueError):wizard.accept({'observing':True,'accept_observed_cross_map':True})
    record(wizard,context,'leader_gripper_open',90);record(wizard,context,'leader_gripper_closed',10)
    record(wizard,context,'task_axes',50)
    result=wizard.accept({'observing':True,'accept_observed_cross_map':True,'expected_registration_sha256':None})
    profile=result['registration'];geometry=registration.derived_geometry(wizard.config_dir,profile,context)
    assert geometry.status()['state']=='REGISTERED_DECLARATION' and not geometry.status()['physical_acceptance']
    assert geometry.full_source and geometry.gripper_mapper(90)==30 and geometry.gripper_mapper(10)==170
    geometry.recenter(measured(context)['positions'],[90]*6)
    proposal=geometry.propose(measured(context)['positions'],[90]*6,original_native_period_s=.04)
    assert proposal['accepted'] and not proposal['native_dispatched']
    assert context.calibration_path.read_bytes()==original
    assert (wizard.config_dir/profile['source_calibration_file']).read_bytes()==original
    assert len([name for name in profile['evidence_source_files_sha256'] if name.endswith('.jpg')])==3
    assert 'source_joint_points' not in profile
    assert profile['provenance']=='calibration_derived_with_observed_taskframe'
    with pytest.raises(ValueError,match='changed'):wizard.accept({'observing':True,'accept_observed_cross_map':True})


def test_tampering_missing_axis_observation_and_equal_gripper_endpoints_fail_closed(tmp_path,context):
    wizard=registration.NativeLeaderRegistrationWizard(context,tmp_path/'config')
    record(wizard,context,'leader_gripper_open',50);record(wizard,context,'leader_gripper_closed',50)
    record(wizard,context,'task_axes',50)
    with pytest.raises(ValueError,match='distinct'):wizard.accept({'observing':True,'accept_observed_cross_map':True})
    record(wizard,context,'leader_gripper_open',90)
    result=wizard.accept({'observing':True,'accept_observed_cross_map':True});profile=result['registration']
    name=profile['observed_captures']['task_axes'];path=wizard.config_dir/name
    value=json.loads(path.read_bytes());value['operator_observation']['axes_and_orientation_confirmed']=False
    path.write_text(json.dumps(value))
    with pytest.raises(ValueError,match='hash'):registration.derived_geometry(wizard.config_dir,profile,context)


def test_pi_metadata_wizard_consumes_existing_cache_and_only_native_GET_reference(tmp_path,context,monkeypatch):
    monkeypatch.setattr(registration,'NativeLeaderContext',lambda root,path:context)
    class CachedStation(Station):
        configuration={'roles':{'leader':{'calibration':str(context.calibration_path)}}}
        def status(self):
            result=super().status();result['roles']['leader']['calibration_sha256']=context.coordinates.sha256
            return result
    station=CachedStation();station.positions=[0.]*5+[50.]
    camera=CameraSource('pi-overview','existing-only');camera.publish(b'\xff\xd8cached\xff\xd9',2,2);station.camera=camera
    source=PiNativeRetargetService.from_config(station,tmp_path/'state',tmp_path/'config');peer=Peer()
    original_reference=peer.reference
    peer.reference=lambda:{**original_reference(),'state_source':'command_estimate'};source.client=peer
    assert source.status()['registration']['A']['all_computable_A_resolved']
    assert source.command('calibration/preview',{})['preview']['physical_commands']==0
    for kind,grip in [('leader_gripper_open',90),('leader_gripper_closed',10),('task_axes',50)]:
        station.positions[-1]=grip;station.tick=time.monotonic_ns()
        source.command('calibration/capture',{'kind':kind,'actor_id':'mock-owner','observing':True,
            'user_observed_test_confirmed':True,'axes_and_orientation_confirmed':kind=='task_axes'})
    result=source.command('calibration/accept',{'observing':True,'accept_observed_cross_map':True})
    assert result['retarget']['state']=='IDLE' and result['retarget']['registration']['state']=='REGISTERED_DECLARATION'
    assert [name for name,_ in peer.calls]==['status','reference']
    source.close();camera.close()
