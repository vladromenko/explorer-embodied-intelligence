"""ENGINEERING_GLUE: known calibration arithmetic + small observed03 wizard.

Only reads the existing leader cache/calibration. No connect/write/calibrate,
hardware command, initial-pose command or second camera/servo owner is created.
"""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import threading
import time
import uuid

import numpy as np

from .kinematics import URDFChain
from .retarget_recording import canonical,write_json
from .transfer_mapping04 import SourceCoordinates
from .native_stream03 import RelativeLeaderGeometry

LEADER_SHA256='9512a1d08ca9f4a8fcd55ac230df5fcf2636e3e1661e9c44ea0ba68a4b8de78c'
DERIVED='calibration_derived_with_observed_taskframe'
KINDS=('leader_gripper_open','leader_gripper_closed','task_axes')


def sha(raw):return hashlib.sha256(raw).hexdigest()


def write_original(path,raw):
    with Path(path).open('xb') as stream:
        stream.write(raw);stream.flush();os.fsync(stream.fileno())
    descriptor=os.open(Path(path).parent,os.O_RDONLY|os.O_DIRECTORY)
    try:os.fsync(descriptor)
    finally:os.close(descriptor)


class NativeLeaderContext:
    def __init__(self,root,calibration_path,*,expected_sha256=LEADER_SHA256):
        self.root=Path(root);self.calibration_path=Path(calibration_path)
        if self.calibration_path.stat().st_size>128*1024:
            raise ValueError('Existing calibration metadata exceeds source bound')
        self.raw=self.calibration_path.read_bytes()
        self.coordinates=SourceCoordinates(self.raw,expected_sha256)
        self.source=URDFChain.from_urdf(self.root/'04-cross-embodiment-transfer/configs/so101/so101_leader_new_calib.urdf','base_link','gripper_frame_link')
        self.target=URDFChain.from_urdf(self.root/'01-autonomous-learning/src/legacy/config/explorer.urdf','base_footprint','Gripping')
        self.profile=self.coordinates.profile(self.source,(self.root/'04-cross-embodiment-transfer/configs/so101/README.md').read_bytes())
        self.native_source=(self.root/'01-autonomous-learning/src/legacy/src/servo_coordinates.py').read_bytes()
        from .native_leader_geometry import original_servo_coordinates
        self.to_radians,self.to_servo=original_servo_coordinates(self.native_source)
        self.gripper_raw=(self.root/'01-autonomous-learning/src/legacy/config/gripper-reference.json').read_bytes()
        baseline=json.loads((self.root/'_registry/controller-config-baseline.json').read_bytes())
        if sha(self.gripper_raw)!=baseline['gripper-reference.json']:
            raise ValueError('Original target gripper command reference changed')
        self.gripper=json.loads(self.gripper_raw)
    def status(self):
        return {'all_computable_A_resolved':True,'source_coordinate_profile':deepcopy(self.profile),
                'source_coordinate_profile_sha256':sha(canonical(self.profile)),
                'target_original_coordinates_sha256':sha(self.native_source),
                'target_gripper_reference_sha256':sha(self.gripper_raw),
                'known_target_gripper_commands':{'open':self.gripper['observed_open_command_deg'],
                    'empty_visually_closed':self.gripper['observed_empty_visually_closed_command_deg']},
                'original_SO101_calibration_retained':True,'device_recalibration_requested':False,
                'metric_aperture_known':False,'force_or_contact_inferred':False,
                'physical_unit_axis_registration_inferred':False}
    def preview(self,native6):
        q=self.coordinates.nominal_urdf_radians(native6,self.source)
        return {'source_q_nominal_rad':q.tolist(),'source_base_from_tool':self.source.forward(q).tolist(),
                'basis':self.profile['basis'],'scope':'preview_shadow_not_measured_unit_pose','physical_commands':0}


class DerivedUnregisteredGeometry:
    registration=None
    def __init__(self,context,reason=None):self.context,self.reason=context,reason
    def status(self):
        return {'state':'UNREGISTERED','A':self.context.status(),
                'missing_cross_map':['OBSERVED_TASK_AXES_AND_ORIENTATION','TWO_OBSERVED_LEADER_GRIPPER_ENDPOINTS'],
                'cross_map_error':self.reason,'leader_device_recalibration_requested':False,'physical_acceptance':False}


def derived_geometry(config_dir,profile,context):
    config_dir=Path(config_dir)
    if (profile.get('schema')!='explorer_native_leader_registration_v1' or
            profile.get('source_calibration_sha256')!=context.coordinates.sha256 or
            profile.get('source_urdf_sha256')!=context.source.source_sha256 or
            profile.get('target_urdf_sha256')!=context.target.source_sha256 or
            profile.get('source_coordinate_profile_sha256')!=sha(canonical(context.profile)) or
            profile.get('target_gripper_reference_sha256')!=sha(context.gripper_raw)):
        raise ValueError('Computed source-coordinate or original URDF binding changed')
    evidence={}
    for name,digest in profile.get('evidence_source_files_sha256',{}).items():
        path=(config_dir/name).resolve()
        if Path(name).is_absolute() or not path.is_relative_to(config_dir.resolve()) or sha(path.read_bytes())!=digest:
            raise ValueError('Observed cross-map original evidence hash/path mismatch')
        evidence[name]=path.read_bytes()
    records={kind:json.loads(evidence[profile['observed_captures'][kind]]) for kind in KINDS}
    for kind,record in records.items():
        source=record.get('measured_leader',{});operator=record.get('operator_observation',{})
        if (record.get('kind')!=kind or source.get('calibration_sha256')!=context.coordinates.sha256 or
                source.get('state_source')!='measured_leader' or operator.get('observing') is not True or
                operator.get('user_observed_test_confirmed') is not True or not operator.get('actor_id')):
            raise ValueError('Explicit observed existing leader capture missing')
        context.coordinates.encoder(source['positions'])
    task=records['task_axes']
    reference=task.get('target_command_reference',{})
    q=np.asarray(reference.get('servo_deg'),float)
    if (task['operator_observation'].get('axes_and_orientation_confirmed') is not True or
            not task.get('target_command_reference',{}).get('boot_id') or
            reference.get('measured') is not False or reference.get('state_source')!='command_estimate' or
            q.shape!=(6,) or not np.isfinite(q).all() or
            task.get('task_frame_convention')!='clutch_relative_end_effector_frames'):
        raise ValueError('Separate observed task-axis/orientation and native command reference required')
    context.preview(task['measured_leader']['positions'])
    source_open=float(records['leader_gripper_open']['measured_leader']['positions'][5])
    source_closed=float(records['leader_gripper_closed']['measured_leader']['positions'][5])
    if source_open==source_closed:
        raise ValueError('Two distinct explicitly observed leader gripper endpoints required')
    curve=np.asarray(sorted([[source_open,context.gripper['observed_open_command_deg']],
                             [source_closed,context.gripper['observed_empty_visually_closed_command_deg']]]),float)
    def gripper(value):
        if not curve[0,0]<=value<=curve[-1,0]:
            raise ValueError('Leader outside observed endpoint range; never silently clip')
        return float(np.interp(value,curve[:,0],curve[:,1]))
    registration={**profile,'sha256':sha(canonical(profile)),'computed_A':context.status(),
                  'physical_unit_pose_measured':False,'metric_gripper_aperture_measured':False}
    geometry=RelativeLeaderGeometry(context.source,context.target,
        lambda native6:context.coordinates.nominal_urdf_radians(native6,context.source),gripper,
        context.to_radians,context.to_servo,registration=registration,source_mapper_uses_full_leader=True)
    geometry.context=context
    return geometry


class NativeLeaderRegistrationWizard:
    def __init__(self,context,config_dir):
        self.context,self.config_dir=context,Path(config_dir);self.lock=threading.RLock()
        self.folder=self.config_dir/'native-leader-evidence';self.draft_path=self.folder/'draft.json'
    def _draft(self):
        if not self.draft_path.exists():return {'captures':{}}
        return json.loads(self.draft_path.read_bytes())
    def status(self):
        with self.lock:
            draft=self._draft();profile=self.config_dir/'native-leader-registration.json'
            return {'schema':'explorer_native_leader_wizard_v1','A':self.context.status(),
                'captured':list(draft['captures']),'remaining_B':[kind for kind in KINDS if kind not in draft['captures']],
                'current_registration_sha256':sha(profile.read_bytes()) if profile.exists() else None,
                'physical_commands':0,'original_calibration_modified':False,'controller_settings_modified':False}
    def capture(self,body,measurement,*,target_reference=None,source_frame=None):
        kind=body.get('kind');actor=body.get('actor_id')
        if (kind not in KINDS or body.get('observing') is not True or
                body.get('user_observed_test_confirmed') is not True or not isinstance(actor,str) or not 0<len(actor)<=128):
            raise ValueError('Explicit typed observed capture required')
        if measurement.get('calibration_sha256')!=self.context.coordinates.sha256 or measurement.get('state_source')!='measured_leader':
            raise ValueError('Existing calibrated leader cache required')
        self.context.coordinates.encoder(measurement['positions'])
        if kind=='task_axes' and (body.get('axes_and_orientation_confirmed') is not True or
                not isinstance(target_reference,dict) or not target_reference.get('boot_id') or
                target_reference.get('measured') is not False or target_reference.get('state_source')!='command_estimate' or
                target_reference.get('blocked_by')):
            raise ValueError('Observe task axes and restore original native reference before task-frame capture')
        if kind=='task_axes':
            q=np.asarray(target_reference.get('servo_deg'),float)
            if q.shape!=(6,) or not np.isfinite(q).all():raise ValueError('Original native six-axis command reference required')
            self.context.preview(measurement['positions'])
        record={'schema':'explorer_native_leader_observed_capture_v1','capture_id':uuid.uuid4().hex,
            'kind':kind,'measured_leader':deepcopy(measurement),'source_camera_frame':None,
            'target_command_reference':deepcopy(target_reference),
            'operator_observation':{key:body.get(key) for key in ('actor_id','observing','user_observed_test_confirmed','axes_and_orientation_confirmed')},
            'task_frame_convention':'clutch_relative_end_effector_frames',
            'cross_device_clock_alignment_assumed':False,'physical_commands':0}
        with self.lock:
            self.folder.mkdir(parents=True,exist_ok=True,mode=0o700)
            if source_frame is not None:
                frame=deepcopy(source_frame);jpeg=frame.pop('jpeg',None)
                if jpeg is not None:
                    if not isinstance(jpeg,bytes) or len(jpeg)>2*1024**2 or not jpeg.startswith(b'\xff\xd8') or not jpeg.endswith(b'\xff\xd9'):
                        raise ValueError('Existing cached source JPEG required')
                    image=self.folder/(record['capture_id']+'.jpg')
                    write_original(image,jpeg)
                    frame.update(source_file=str(image.relative_to(self.config_dir)),sha256=sha(jpeg))
                record['source_camera_frame']=frame
            path=self.folder/(record['capture_id']+'.json');write_json(path,record)
            draft=self._draft();draft['captures'][kind]=str(path.relative_to(self.config_dir));write_json(self.draft_path,draft)
        return {'capture':record,**self.status()}
    def accept(self,body):
        if body.get('observing') is not True or body.get('accept_observed_cross_map') is not True:
            raise ValueError('Explicit observed cross-map acceptance required')
        with self.lock:
            draft=self._draft();path=self.config_dir/'native-leader-registration.json'
            previous=sha(path.read_bytes()) if path.exists() else None
            if body.get('expected_registration_sha256')!=previous:
                raise ValueError('Application cross-map changed; review current draft')
            if any(kind not in draft['captures'] for kind in KINDS):
                raise ValueError('Observed task-frame and two gripper endpoint captures required')
            artifacts={name:sha((self.config_dir/name).read_bytes()) for name in draft['captures'].values()}
            for name in draft['captures'].values():
                frame=json.loads((self.config_dir/name).read_bytes()).get('source_camera_frame') or {}
                if frame.get('source_file'):
                    image=(self.config_dir/frame['source_file']).resolve()
                    if not image.is_relative_to(self.folder.resolve()) or sha(image.read_bytes())!=frame.get('sha256'):
                        raise ValueError('Cached source calibration image changed')
                    artifacts[frame['source_file']]=frame['sha256']
            calibration=self.folder/(self.context.coordinates.sha256+'.json')
            if calibration.exists() and calibration.read_bytes()!=self.context.raw:
                raise ValueError('Original calibration evidence conflicts')
            if not calibration.exists():
                write_original(calibration,self.context.raw)
            calibration_name=str(calibration.relative_to(self.config_dir));artifacts[calibration_name]=sha(self.context.raw)
            profile={'schema':'explorer_native_leader_registration_v1','provenance':DERIVED,
                'source_urdf_sha256':self.context.source.source_sha256,'target_urdf_sha256':self.context.target.source_sha256,
                'source_calibration_sha256':self.context.coordinates.sha256,'source_calibration_file':calibration_name,
                'source_coordinate_profile_sha256':sha(canonical(self.context.profile)),
                'source_coordinate_profile':deepcopy(self.context.profile),'observed_captures':draft['captures'],
                'evidence_source_files_sha256':artifacts,'target_gripper_reference_sha256':sha(self.context.gripper_raw),
                'physical_acceptance':False,'created_receiver_monotonic_ns':time.monotonic_ns()}
            derived_geometry(self.config_dir,profile,self.context)
            if path.exists():
                backup=self.folder/('registration-'+previous+'.json')
                if not backup.exists():write_original(backup,path.read_bytes())
            write_json(path,profile)
        return {'registration':profile,**self.status()}
