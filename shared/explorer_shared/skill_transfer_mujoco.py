"""ENGINEERING_GLUE: injected finite CAD target provider for skill transfer.

No experiment import, controller connection or hardware IO. The caller supplies
an existing MuJoCo environment; recorded9D commands preserve actual neutral base
channels for a declared stationary fixture, or use its actual mobile wheel path.
"""
from __future__ import annotations

from copy import deepcopy
import inspect
import uuid
from pathlib import Path

import numpy as np

from .kinematics import rigid_transform
from .transfer_mapping04 import TransferBlocked,sha


class MuJoCoTargetProvider:
    physical_io=False
    def __init__(self,env,*,target_urdf_sha256,mesh_files,phase_evidence_map,navigation_map_provider=None):
        import mujoco
        self.mj,self.env=mujoco,env;self.urdf_sha=target_urdf_sha256;self.phase_evidence_map=deepcopy(phase_evidence_map)
        self.navigation_map_provider=navigation_map_provider;self.mesh_files={str(Path(path).resolve()):expected for path,expected in mesh_files.items()}
        if not self.mesh_files or any(sha(Path(path).read_bytes())!=expected for path,expected in self.mesh_files.items()):raise TransferBlocked('ACTUAL_TARGET_CAD_MESH_SHA_REQUIRED','A')
        if not isinstance(env.model,mujoco.MjModel) or not isinstance(env.data,mujoco.MjData) or not isinstance(env.xml,str):raise TransferBlocked('ACTUAL_MUJOCO_MODEL_DATA_REQUIRED','A')
        self.model_sha=sha(env.xml.encode());self.sequence=0;self.boot='sim-target-'+self.model_sha[:12]
        self.env_source=Path(inspect.getfile(type(env))).resolve()
        self.provider_identity=dict(kind='MUJOCO_CAD',implementation_sha256=sha(Path(__file__).read_bytes()),environment_implementation_sha256=sha(self.env_source.read_bytes()),
            target_model_sha256=self.model_sha,target_urdf_sha256=target_urdf_sha256,cad_mesh_sha256=self.mesh_files,engine_version=mujoco.__version__,
            native_action_dimension=9,base_motion_backend='actual_finite_wheel_dynamics' if len(env.command)==9 else 'explicit_stationary_base_physics_fixture',physical_io=False)
        allowed={'grasped','lifted','carried_above_target','released_inside_goal','success','approach_proximity','withdrawn',
                 'navigate_to_source','outbound_door_crossed','inbound_door_crossed','retained_transport'}
        if not self.phase_evidence_map or any(not values or set(values)-allowed for values in self.phase_evidence_map.values()):raise TransferBlocked('EXPLICIT_INDEPENDENT_PHASE_CRITERIA_REQUIRED','A')

    def recheck(self):
        if sha(self.env.xml.encode())!=self.model_sha or any(sha(Path(path).read_bytes())!=expected for path,expected in self.mesh_files.items()):raise TransferBlocked('TARGET_MODEL_OR_MESH_CHANGED_DURING_TRIAL','A')
        if sha(self.env_source.read_bytes())!=self.provider_identity['environment_implementation_sha256']:raise TransferBlocked('TARGET_PHYSICS_SOURCE_CHANGED_DURING_TRIAL','A')

    def _stamp(self):return dict(clock_domain=self.boot,boot_id=self.boot,monotonic_ns=int(round(float(self.env.data.time)*1e9)))

    def _command9(self):
        command=np.asarray(self.env.command,float)
        if command.shape==(9,):return command.tolist()
        if command.shape==(6,):
            if 'manipulation_base_fixture' not in self.env.xml:raise TransferBlocked('SIX_COMMAND_ENV_REQUIRES_EXPLICIT_STATIONARY_BASE_FIXTURE','A')
            return np.r_[command,0.,0.,0.].tolist()
        raise TransferBlocked('TARGET_NATIVE_COMMAND_DIMENSION_INVALID','A')

    def _base(self):
        base=self.env.data.body('base_link');result=np.eye(4);result[:3,:3]=base.xmat.reshape(3,3);result[:3,3]=base.xpos
        return rigid_transform(result)

    def reset(self,seed):
        self.recheck();self.env.reset(seed=seed);self.sequence=0;self.boot='sim-target-'+self.model_sha[:12]+'-'+uuid.uuid4().hex
        if hasattr(self.env,'record_substeps'):self.env.record_substeps=True

    def observe(self):
        image=self.env.render();stamp=self._stamp()
        result=dict(target_rgb=image,preceding_command9=self._command9(),world_from_base=self._base().tolist(),joint_feedback6_rad=self.env.data.qpos[self.env.qids].tolist(),
            renderer_receipt=dict(embodiment='ROSMASTER',rendered_from_target_physics=True,target_model_sha256=self.model_sha,
                frame_id=self.boot+':'+str(self.sequence),stamp=stamp,camera_id='actual_mujoco_overview',engine_version=self.mj.__version__),observation_stamp=stamp)
        if self.navigation_map_provider:result['observed_navigation_map']=self.navigation_map_provider(self.env)
        return result

    def advance(self,command,phase_id):
        command=np.asarray(command,float)
        if command.shape!=(9,) or not np.isfinite(command).all():raise TransferBlocked('SIMULTANEOUS_FINITE_TARGET9_REQUIRED','A')
        before=self._stamp();self.env.phase=phase_id
        if len(self.env.command)==9:self.env.step_command(command)
        elif np.any(abs(command[6:])>1e-12):raise TransferBlocked('STATIONARY_TARGET_FIXTURE_CANNOT_EXECUTE_BASE_NAVIGATION','A')
        else:self.env.step_command(command[:6])
        self.sequence+=1;after=self._stamp()
        return dict(receipt_id=self.boot+':dispatch:'+str(self.sequence),physical_io=False,finite_dynamics_step_completed=after['monotonic_ns']>before['monotonic_ns'],
            requested_action9=command.tolist(),dispatched_action9=self._command9(),pre_action_stamp=before,dispatch_stamp=after,
            measured_qpos=self.env.data.qpos.tolist(),measured_qvel=self.env.data.qvel.tolist(),independent_evaluator=deepcopy(self.env.evaluator.result()),
            native_integration_state=self.env.integration_state().tolist(),attainment_inferred_from_commands=False)

    def collision_check(self,start,end,world_from_base):
        """Sample the bounded joint segment against target CAD contact geometry.

        Finger/object contact is allowed for grasp; robot/environment and other
        object collisions are rejected. This is sampled geometric evidence,
        explicitly not a continuous collision theorem or physical qualification.
        """
        start,end=np.asarray(start,float),np.asarray(end,float);base=rigid_transform(world_from_base)
        data=self.mj.MjData(self.env.model);data.qpos[:]=self.env.data.qpos;data.qvel[:]=0.
        base_body=self.env.model.body('base_link');jnt=int(self.env.model.body_jntadr[base_body.id]);adr=int(self.env.model.jnt_qposadr[jnt])
        if self.env.model.jnt_type[jnt]==self.mj.mjtJoint.mjJNT_FREE:
            data.qpos[adr:adr+3]=base[:3,3];quat=np.zeros(4);self.mj.mju_mat2Quat(quat,base[:3,:3].reshape(-1));data.qpos[adr+3:adr+7]=quat
        pads=set(self.env.pad_ids);objects=set(self.env.object_geom_ids);violations=[]
        count=max(2,int(np.ceil(np.max(abs(end-start))/.02))+1)
        for fraction in np.linspace(0,1,count):
            data.qpos[self.env.qids[:5]]=start+(end-start)*fraction;self.mj.mj_forward(self.env.model,data)
            for contact in data.contact[:data.ncon]:
                a,b=map(int,contact.geom);names=[self.env.model.body(int(self.env.model.geom_bodyid[index])).name for index in (a,b)]
                arm=[name.startswith(('arm','Gripping','rlink','llink')) for name in names]
                finger_object=(a in pads and b in objects) or (b in pads and a in objects)
                if any(arm) and not finger_object and contact.dist<-.0015:violations.append(dict(geoms=[a,b],distance_m=float(contact.dist),segment_fraction=float(fraction)))
        return dict(collision_free=not violations,continuous_segment_checked=False,sampled_segment_checked=True,target_urdf_sha256=self.urdf_sha,
            scene_sha256=self.model_sha,samples=count,max_joint_sample_spacing_rad=.02,gripper_segment_geometry_checked=False,violations=violations[:32],
            method='MUJOCO_FORWARD_SAMPLED_CAD_CONTACTS_NOT_CONTINUOUS_PROOF',physical_qualification=False)

    def report(self):
        self.recheck();facts=deepcopy(self.env.evaluator.result());last=facts.get('final',{})
        center=self.env.points().mean(0);jaw=self.env.jaw_center();distance=float(np.linalg.norm(center-jaw))
        facts.update(approach_proximity=distance<.15,withdrawn=distance>.05 and last.get('free_of_fingers') is True,
            released_inside_goal=facts.get('carried_above_target') is True and last.get('inside_all_points') is True and last.get('free_of_fingers') is True and last.get('bottom_supported') is True)
        mobile=getattr(self.env,'mobile_evaluator',None)
        mobile_result=mobile.result() if mobile else None
        if mobile_result:
            # Full mobile success remains the environment's independent whole
            # task criterion; an object-only pass cannot replace it.
            facts.update(mobile_result.get('conditions') or {});facts['retained_transport']=facts.get('base_carried_held_object_half_metre') is True
        per_phase={phase:dict(success=all(facts.get(name) is True for name in required),criteria={name:facts.get(name) for name in required}) for phase,required in self.phase_evidence_map.items()}
        return dict(source='target_finite_physics_evaluator',independent_of_action_commands=True,end_to_end_success=bool((mobile_result or facts).get('success')),
            per_phase=per_phase,object_facts=facts,mobile_facts=mobile_result,substep_contact_trace=np.asarray(self.env.substep_trace).tolist(),
            finite_step_count=self.env.steps,simulation_time_s=float(self.env.data.time),physical_io=False)
