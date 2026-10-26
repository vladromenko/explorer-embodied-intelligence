"""Known-source arithmetic only. No motor object is instantiated or connected."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from explorer_shared.kinematics import URDFChain
from explorer_shared.so101_station import NAMES
from explorer_shared.transfer_mapping04 import SourceCoordinates,TargetCoordinates,TransferBlocked,audit_known_mapping,sha,write_audit

ROOT=Path(__file__).parents[1]
SOURCE=ROOT/'04-cross-embodiment-transfer/configs/so101/so101_new_calib.urdf'
TARGET=ROOT/'01-autonomous-learning/src/legacy/config/explorer.urdf'
NATIVE=ROOT/'01-autonomous-learning/src/legacy/src/servo_coordinates.py'
README=ROOT/'04-cross-embodiment-transfer/configs/so101/README.md'
CALIBRATION={name:dict(id=index+1,drive_mode=0,homing_offset=100+index,range_min=values[0],range_max=values[1]) for index,(name,values) in enumerate(zip(NAMES,[(853,3475),(834,3216),(878,3091),(858,3202),(0,4095),(1389,2900)]))}


def encoded(body):return json.dumps(body,sort_keys=True).encode()


def test_exact_encoder_and_radian_conversion_matches_INSTALLED_official_pure_methods():
    pytest.importorskip('lerobot')
    from lerobot.motors.motors_bus import MotorsBus,MotorNormMode
    raw=encoded(CALIBRATION);mapping=SourceCoordinates(raw,sha(raw))
    bus=SimpleNamespace(calibration={name:SimpleNamespace(**row) for name,row in CALIBRATION.items()},apply_drive_mode=True,
        motors={name:SimpleNamespace(norm_mode=MotorNormMode.RANGE_M100_100 if index<5 else MotorNormMode.RANGE_0_100) for index,name in enumerate(NAMES)},
        _id_to_name=lambda value:NAMES[value-1],_id_to_model=lambda value:'sts3215',model_resolution_table={'sts3215':4096})
    native=np.array([-65.4,22.2,9.1,-39.9,70.2,55.5])
    official=MotorsBus._unnormalize(bus,{index+1:value for index,value in enumerate(native)})
    assert np.array_equal(mapping.encoder(native),list(official.values()))
    for name in NAMES:bus.motors[name].norm_mode=MotorNormMode.DEGREES
    degrees=MotorsBus._normalize(bus,official)
    assert np.allclose(mapping.arm_encoder_delta_radians(native),np.radians(list(degrees.values())[:5]),atol=1e-12)


def test_homing_offset_is_not_applied_twice_and_source_bytes_are_immutable():
    raw=encoded(CALIBRATION);changed=deepcopy(CALIBRATION);changed['shoulder_pan']['homing_offset']+=999
    second=encoded(changed);native=[0.,0.,0.,0.,0.,0.]
    assert np.array_equal(SourceCoordinates(raw,sha(raw)).arm_encoder_delta_radians(native),SourceCoordinates(second,sha(second)).arm_encoder_delta_radians(native))
    assert raw==encoded(CALIBRATION)
    with pytest.raises(TransferBlocked,match='SHA_MISMATCH'):SourceCoordinates(raw,'0'*64)
    with pytest.raises(TransferBlocked,match='NATIVE_CONTRACT'):SourceCoordinates(raw,sha(raw)).encoder([101,0,0,0,0,50])


def test_nominal_mapping_does_not_stretch_full_turn_wrist_to_URDF_limits():
    source=URDFChain.from_urdf(SOURCE,'base_link','gripper_frame_link');raw=encoded(CALIBRATION);mapping=SourceCoordinates(raw,sha(raw))
    profile=mapping.profile(source,README.read_bytes())
    assert profile['joints']['wrist_roll']['scale_rad_per_native_unit']==pytest.approx(np.pi/100)
    assert profile['physical_URDF_registration_measured'] is False
    with pytest.raises(TransferBlocked,match='OUTSIDE_CAD_LIMITS'):mapping.nominal_urdf_radians([0,0,0,0,100,50],source)
    q=mapping.nominal_urdf_radians([0,0,0,0,40,50],source)
    assert source.forward(q).shape==(4,4)


def test_target_native_adapter_is_bound_to_existing_exact_factory_source():
    mapping=TargetCoordinates(NATIVE.read_bytes());q=[-.2,.3,-.4,.5,-.6];native=mapping.servo(q,160)
    assert native.shape==(6,) and native[5]==160
    assert np.allclose(mapping.radians(native),q)
    namespace={};exec(compile(NATIVE.read_bytes(),str(NATIVE),'exec'),namespace)
    assert np.array_equal(mapping.radians(native),namespace['to_radians'](native[:5]))
    assert np.array_equal(native[:5],namespace['to_servo'](q))
    with pytest.raises(TransferBlocked,match='SOURCE_CHANGED'):TargetCoordinates(NATIVE.read_bytes()+b'\n')


def test_audit_resolves_empty_profile_causes_but_preserves_B_C_and_no_physical_permission(tmp_path):
    source=URDFChain.from_urdf(SOURCE,'base_link','gripper_frame_link');target=URDFChain.from_urdf(TARGET,'base_footprint','Gripping')
    raw=encoded(CALIBRATION)
    result=audit_known_mapping(source_chain=source,target_chain=target,source_coordinates=SourceCoordinates(raw,sha(raw)),source_zero_convention_bytes=README.read_bytes(),target_coordinates=TargetCoordinates(NATIVE.read_bytes()),
        gripper_reference_bytes=(ROOT/'01-autonomous-learning/src/legacy/config/gripper-reference.json').read_bytes(),handeye_reference_bytes=(ROOT/'01-autonomous-learning/src/legacy/config/handeye-accepted.json').read_bytes())
    assert result['all_computable_mapping_A_resolved'] and all(row['status'].startswith('RESOLVED') for row in result['A'])
    assert len(result['B'])==3 and result['C'][0]['id']=='qualified06_object_skill_source'
    assert not result['physical_execution_authorized'] and not result['controller_settings_modified']
    camera=next(row for row in result['A'] if row['id']=='existing_stationary_camera_to_arm4_reference')
    assert camera['validated_joints']==[1,2,3,4] and not camera['moving_frame_use_qualified']
    stored=write_audit(tmp_path,result);assert write_audit(tmp_path,result)==stored
    assert hashlib.sha256(Path(stored['path']).read_bytes()).hexdigest()==stored['sha256']
