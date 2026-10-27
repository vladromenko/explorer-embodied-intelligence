"""Bootstrap geometry contracts; only fixture cache and in-process IK sinks."""
from copy import deepcopy
import json
from types import SimpleNamespace

import numpy as np
import pytest

from explorer_shared import native_leader_bootstrap as bootstrap
from explorer_shared.native_leader_geometry import registered_native_leader_geometry
from explorer_shared.native_gripper_alignment import gripper_alignment
from test_native_leader_registration import context, measured


def test_no_registration_file_builds_deterministic_bootstrap_without_any_file_writes(tmp_path,context,monkeypatch):
    from explorer_shared import native_leader_registration
    monkeypatch.setattr(native_leader_registration,'NativeLeaderContext',lambda root,path:context)
    config=tmp_path/'absent-config'
    before=context.calibration_path.read_bytes()
    a=registered_native_leader_geometry(config,source_calibration_path=context.calibration_path)
    b=registered_native_leader_geometry(config,source_calibration_path=context.calibration_path)
    assert a.registration==b.registration and not config.exists()
    assert context.calibration_path.read_bytes()==before
    assert a.status()['state']=='BOOTSTRAP_DERIVED'
    assert a.registration['target_measured'] is False
    assert a.registration['controller_configuration_writes']==a.registration['physical_commands']==0
    assert a.registration['provenance']==bootstrap.PROVENANCE


def test_registry_sha_mismatch_and_unknown_orientation_are_never_silent_defaults(context,monkeypatch,tmp_path):
    with pytest.raises(ValueError,match='named'):bootstrap.bootstrap_geometry(context,frame_convention='guess')
    monkeypatch.setattr(bootstrap,'expected_leader_sha',lambda root:'0'*64)
    with pytest.raises(ValueError,match='registry SHA'):bootstrap.bootstrap_geometry(context)


def test_no_homing_offset_reapplication_and_provisional_gripper_direction(context):
    geometry=bootstrap.bootstrap_geometry(context)
    assert geometry.source_mapper([0.,0.,0.,0.,0.,50.])==pytest.approx([0.]*5,abs=.002)
    assert geometry.gripper_mapper(0)==170 and geometry.gripper_mapper(100)==30
    assert geometry.gripper_mapper(50)==100
    for invalid in (-.001,100.001,float('nan')):
        with pytest.raises(ValueError):geometry.gripper_mapper(invalid)
    assert not geometry.registration['gripper_direction_physically_verified']
    assert not geometry.registration['metric_gripper_aperture_measured']


@pytest.mark.parametrize('frame_convention',list(bootstrap.FRAME_YAW))
def test_clutch_and_latest_pose_use_declared_base_rotation_and_current_command_estimate(context,frame_convention,monkeypatch):
    geometry=bootstrap.bootstrap_geometry(context,frame_convention=frame_convention)
    initial=[0.,0.,0.,0.,0.,50.];target=[90.]*5+[100.]
    captured=[]
    def inverse(goal,q,**kwargs):
        captured.append(np.array(goal));return SimpleNamespace(reached=True,positions=np.array(q),as_dict=lambda:{'fixture_inverse':True})
    monkeypatch.setattr(geometry.target,'inverse',inverse)
    assert geometry.recenter(initial,target)['physical_commands']==0
    geometry.propose(initial,target,original_native_period_s=.04)
    anchor=geometry.anchor;assert captured[-1]==pytest.approx(anchor.target_base_from_object)
    changed=[.05,-.05,.05,.05,.05,50.]
    geometry.propose(changed,target,original_native_period_s=.04)
    pose=geometry.source.forward(geometry.source_mapper(changed))
    expected=anchor.target_base_from_object.copy();rotation=geometry.base_rotation
    expected[:3,3]+=rotation@(pose[:3,3]-anchor.source_base_from_object[:3,3])
    expected[:3,:3]=rotation@(pose[:3,:3]@anchor.source_base_from_object[:3,:3].T)@rotation.T@anchor.target_base_from_object[:3,:3]
    assert captured[-1]==pytest.approx(expected)
    # Repeated recenter uses the NEW current estimate, never the initial pose.
    target2=[91.,89.,91.,89.,91.,100.]
    geometry.recenter(changed,target2);geometry.propose(changed,target2,original_native_period_s=.04)
    assert captured[-1]==pytest.approx(geometry.target.forward(geometry.to_radians(target2[:5])))


def test_bootstrap_never_silently_overrides_corrupt_explicit_registration(context,tmp_path,monkeypatch):
    from explorer_shared import native_leader_registration
    monkeypatch.setattr(native_leader_registration,'NativeLeaderContext',lambda root,path:context)
    path=tmp_path/'native-leader-registration.json';path.write_text('{')
    geometry=registered_native_leader_geometry(tmp_path,source_calibration_path=context.calibration_path)
    assert geometry.status()['state']=='UNREGISTERED' and geometry.status()['cross_map_error']=='INVALID_CROSS_MAP:JSONDecodeError'


def test_original_gripper_admission_still_refuses_an_absolute_first_goal_jump(context):
    geometry=bootstrap.bootstrap_geometry(context);leader=measured(context)['positions']
    aligned=gripper_alignment(geometry,leader,[90.]*5+[100.],calibration_sha256=context.coordinates.sha256)
    refused=gripper_alignment(geometry,leader,[90.]*6,calibration_sha256=context.coordinates.sha256)
    assert aligned['aligned'] and not refused['aligned']
    assert refused['reason']=='ALIGN_LEADER_GRIPPER_TO_CURRENT_COMMAND_ESTIMATE'


def test_valid_source_encoder_outside_nominal_CAD_is_diagnostic_without_clipping_or_target_limit_change(context):
    geometry=bootstrap.bootstrap_geometry(context)
    source=[100.,0.,0.,0.,0.,50.]
    q=geometry.source_mapper(source)
    with pytest.raises(ValueError,match='outside URDF'):geometry.source.forward(q)
    diagnostic=bootstrap.source_diagnostics(geometry,source)
    assert diagnostic['CAD_limit_excess_rad'][0]>0 and diagnostic['source_clipped'] is False
    assert diagnostic['nominal_source_radians']==q.tolist()
    assert np.asarray(diagnostic['source_base_from_tool'])==pytest.approx(geometry.source.forward(q,enforce_limits=False))
    geometry.recenter(source,[90.]*5+[100.])
    with pytest.raises(ValueError,match='outside URDF'):geometry.recenter(source,[999.]*5+[100.])
    with pytest.raises(ValueError):geometry.source_mapper([100.001,0.,0.,0.,0.,50.])
