"""EXPLORER_CAD_MUJOCO / ENGINEERING_GLUE: finite mobile candy kitchen fixture.

Reuses the preserved CAD motor/passive-roller model and complete candy contacts.
Only a new simulation XML is changed: remove the stationary-base fixture, place
wrapped candy/tray in separate rooms, and add estimated kitchen collision boxes.
"""
from __future__ import annotations

import math
import xml.etree.ElementTree as ET

import gymnasium as gym
import mujoco
import numpy as np

from legacy.simulator.explorer_model import ARM_JOINTS, wheel_targets, servo_to_joint
from .manipulation_env import ManipulationEnv, FullTaskEvaluator
from .pilot import LOW, HIGH

SOURCE = np.array([1., 0., .014])
DESTINATION = np.array([.10, .24, 0.])
DOOR_X = .65


def kitchen_xml(base_xml):
    root = ET.fromstring(base_xml)
    equality = root.find('equality')
    fixture = equality.find("weld[@name='manipulation_base_fixture']")
    if fixture is None:
        raise ValueError('Expected the explicit stationary-base simulation fixture')
    equality.remove(fixture)
    if equality.findall('weld'):
        raise ValueError('A mobile contact task must not weld robot or object')
    world = root.find('worldbody')
    world.find("body[@name='candy']").set('pos', ' '.join(map(str,SOURCE)))
    world.find("body[@name='bin']").set('pos', ' '.join(map(str,DESTINATION)))
    for side in (-1,1):
        ET.SubElement(world,'geom',name='kitchen_divider_'+str(side),type='box',
            pos=f'{DOOR_X} {side*.925} .25',size='.025 .575 .25',
            contype='2',conaffinity='5',friction='.8 .005 .0001',rgba='.55 .58 .62 1')
    for name,pos,size in (
            ('south',[.55,-.70,.25],[1.,.025,.25]),('north',[.55,.80,.25],[1.,.025,.25]),
            ('west',[-.45,.05,.25],[.025,.75,.25]),('east',[1.55,.05,.25],[.025,.75,.25]),
            ('counter',[1.25,.58,.25],[.23,.16,.25])):
        ET.SubElement(world,'geom',name='kitchen_'+name,type='box',pos=' '.join(map(str,pos)),
            size=' '.join(map(str,size)),contype='2',conaffinity='5',friction='.8 .005 .0001',
            rgba='.55 .55 .58 1' if name!='counter' else '.55 .36 .20 1')
    camera = world.find("camera[@name='overview']")
    position,target = np.array([1.35,-1.70,1.60]),np.array([.50,.05,.15])
    forward=(target-position)/np.linalg.norm(target-position)
    right=np.cross(forward,[0.,0.,1.]);right/=np.linalg.norm(right)
    up=np.cross(right,forward)
    camera.set('pos',' '.join(map(str,position)));camera.set('xyaxes',' '.join(map(str,np.r_[right,up])))
    camera.set('fovy','55')
    return ET.tostring(root,encoding='unicode')


class WholeMobileEvaluator:
    """Truth-only substep scoring, independent of controller labels and navigation."""
    def __init__(self, object_evaluator, initial_base, source_center):
        self.object=object_evaluator
        self.initial_base=np.asarray(initial_base,float).copy()
        self.previous=self.initial_base.copy()
        self.source_approach=np.asarray(source_center,float)[:2]-[.20,0.]
        self.source_visited=self.outbound_door=self.inbound_door=False
        self.first_held_base=None
        self.carried_base_distance=0.
        self.max_base_displacement=0.
        self.path_length=0.
        self.forbidden_contact_substeps=0
        self.maximum_forbidden_contact_force=0.
        self.tipped=False
        self.stationary=False
        self.substeps=0

    def observe(self, base, speed, upright_cosine, forbidden_force):
        base=np.asarray(base,float)
        self.substeps+=1
        self.path_length+=float(np.linalg.norm(base[:2]-self.previous[:2]))
        self.max_base_displacement=max(self.max_base_displacement,float(np.linalg.norm(base[:2]-self.initial_base[:2])))
        inside_door=abs(float(base[1]))<.20
        self.outbound_door |= self.previous[0]<DOOR_X<=base[0] and inside_door
        self.inbound_door |= base[0]<DOOR_X<=self.previous[0] and inside_door
        self.source_visited |= bool(np.linalg.norm(base[:2]-self.source_approach)<.04)
        bilateral=all(force>.0001 for force in self.object.last.get('pad_force_N',[0.,0.]))
        if self.object.lifted and bilateral:
            if self.first_held_base is None:
                self.first_held_base=base.copy()
            self.carried_base_distance=max(self.carried_base_distance,float(np.linalg.norm(base[:2]-self.first_held_base[:2])))
        else:
            # A later regrasp cannot credit travel made while the object was dropped.
            self.first_held_base=None
        self.forbidden_contact_substeps+=int(forbidden_force>.0001)
        self.maximum_forbidden_contact_force=max(self.maximum_forbidden_contact_force,float(forbidden_force))
        self.tipped |= upright_cosine<math.cos(math.radians(15))
        self.stationary=bool(speed<.03)
        self.previous=base.copy()

    def result(self):
        conditions=dict(navigate_to_source=bool(self.source_visited),outbound_door_crossed=bool(self.outbound_door),
            inbound_door_crossed=bool(self.inbound_door),base_carried_held_object_half_metre=self.carried_base_distance>=.5,
            complete_object_placement=bool(self.object.success),no_kitchen_collision=self.forbidden_contact_substeps==0,
            base_upright=not self.tipped,base_stationary=self.stationary)
        return dict(success=all(conditions.values()),conditions=conditions,
            unsupported_or_failed_stages=[name for name,passed in conditions.items() if not passed],
            object=self.object.result(),carried_base_distance_m=self.carried_base_distance,
            maximum_base_displacement_m=self.max_base_displacement,path_length_m=self.path_length,
            kitchen_collision_substeps=self.forbidden_contact_substeps,
            maximum_kitchen_collision_force_N=self.maximum_forbidden_contact_force,physics_substeps=self.substeps,
            physical_run=False)


class MobileManipulationEnv(ManipulationEnv):
    """Nine native endpoints, genuine finite wheel actuation and no qpos control."""
    def __init__(self,mesh_dir,horizon=2400):
        self._mobile_ready=False
        self._mobile_resetting=False
        super().__init__(mesh_dir,'candy_to_tray',horizon)
        self.xml=kitchen_xml(self.xml)
        self.model=mujoco.MjModel.from_xml_string(self.xml)
        self.data=mujoco.MjData(self.model)
        self.qids=np.array([self.model.joint(name).qposadr[0] for name in ARM_JOINTS])
        self.dids=np.array([self.model.joint(name).dofadr[0] for name in ARM_JOINTS])
        self.joint_q=self.qids
        self.servo_ids=np.array([self.model.actuator('servo'+str(i)).id for i in range(1,7)])
        self.wheel_ids=np.array([self.model.actuator('wheel'+str(i)).id for i in range(4)])
        self.wheel_dofs=np.array([self.model.joint(name).dofadr[0] for name in ('lwheel1_Joint','rwheel1_Joint','lwheel2_Joint','rwheel2_Joint')])
        self.pad_ids=[self.model.geom(name).id for name in ('finger_left','finger_right')]
        self.object_id=self.model.body('candy').id
        self.object_geom_ids={self.model.geom(name).id for name in ('candy_contact','candy_wrapper_-1','candy_wrapper_1')}
        self.support_ids={self.model.geom('bin_bottom').id}
        self.kitchen_geom_ids={i for i in range(self.model.ngeom) if self.model.geom(i).name.startswith('kitchen_')}
        self.base_id=self.model.body('base_link').id
        self.goal=self.model.body('bin').pos.copy()
        self.half=self.model.geom('bin_bottom').size[:2].copy()
        self.rim=.044
        self.action_space=gym.spaces.Box(LOW,HIGH,dtype=np.float32)
        self.observation_space=gym.spaces.Box(LOW,HIGH,dtype=np.float32)
        self._mobile_ready=True
        self.reset(seed=0)

    def reset(self,*,seed=None,options=None):
        if not self._mobile_ready:
            return super().reset(seed=seed,options=options)
        self._mobile_resetting=True
        try:
            super().reset(seed=seed,options=options)
        finally:
            self._mobile_resetting=False
        self.command=np.r_[self.command[:6],0.,0.,0.]
        self.command_before=self.command.copy();self.requested=self.command.copy();self.raw_action=self.command.copy()
        self.data.ctrl[self.wheel_ids]=0.
        self.evaluator=FullTaskEvaluator(self.points(),self.goal,self.half,self.rim,self.model.opt.timestep)
        self.mobile_evaluator=WholeMobileEvaluator(self.evaluator,self.base_pose(),self.points().mean(0))
        self.mobile_substep_trace=[]
        self.phase='mobile_ready'
        return self.observation(),self.info()

    def observation(self):
        if not self._mobile_ready or self._mobile_resetting:
            return super().observation()
        return self.command.astype(np.float32).copy()

    def base_pose(self):
        body=self.data.body('base_link');rotation=body.xmat.reshape(3,3)
        return np.array([body.xpos[0],body.xpos[1],math.atan2(rotation[1,0],rotation[0,0])])

    def _substep_evaluate(self):
        super()._substep_evaluate()
        forbidden=0.
        force=np.zeros(6)
        for index,contact in enumerate(self.data.contact[:self.data.ncon]):
            if any(int(g) in self.kitchen_geom_ids for g in contact.geom):
                mujoco.mj_contactForce(self.model,self.data,index,force)
                forbidden+=max(0.,force[0])
        velocity=np.zeros(6)
        mujoco.mj_objectVelocity(self.model,self.data,mujoco.mjtObj.mjOBJ_BODY,self.base_id,velocity,0)
        upright=float(self.data.body('base_link').xmat.reshape(3,3)[2,2])
        self.mobile_evaluator.observe(self.base_pose(),float(np.linalg.norm(velocity[3:])),upright,forbidden)
        if self.record_substeps:
            self.mobile_substep_trace.append(np.r_[self.data.time,self.base_pose(),velocity,self.data.ctrl[self.wheel_ids],
                self.data.qvel[self.wheel_dofs],forbidden])

    def step(self,action):
        return self.step_command(action)

    def step_command(self,requested):
        raw=np.asarray(requested,float)
        if raw.shape!=(9,) or not np.isfinite(raw).all() or np.any(raw<LOW) or np.any(raw>HIGH):
            raise ValueError('Nine finite native endpoints within the declared simulation ABI required')
        self.raw_action=raw.copy();self.requested=raw.copy()
        start=self.command.copy();self.command_before=start.copy()
        endpoint=start.copy()
        endpoint[:6]+=np.clip(raw[:6]-start[:6],-self.velocity*self.period,self.velocity*self.period)
        endpoint[6:]+=np.clip(raw[6:]-start[6:],-.5*self.period,.5*self.period)
        endpoint=np.clip(endpoint,LOW,HIGH)
        substeps=round(self.period/self.model.opt.timestep)
        for index in range(substeps):
            self.command=np.clip(start+(endpoint-start)*(index+1)/substeps,LOW,HIGH)
            self.data.ctrl[self.servo_ids]=servo_to_joint(self.command[:6])
            self.data.ctrl[self.wheel_ids]=wheel_targets(*self.command[6:])
            mujoco.mj_step(self.model,self.data)
            self._substep_evaluate()
        self.steps+=1
        unstable=not np.isfinite(self.data.qpos).all() or any(w.number for w in self.data.warning)
        result=self.mobile_evaluator.result()
        return self.observation(),float(result['success']),bool(result['success'] or unstable),self.steps>=self.horizon,self.info(unstable)

    def info(self,unstable=False):
        if not self._mobile_ready or self._mobile_resetting:
            return super().info(unstable)
        return dict(task='mobile_wrapped_candy_kitchen_to_tray',steps=self.steps,sim_time_s=float(self.data.time),
            pre_action_command9=self.command_before.tolist(),command9=self.command.tolist(),
            requested_endpoint9=self.requested.tolist(),raw_action9=self.raw_action.tolist(),base_pose=self.base_pose().tolist(),
            evaluator=self.mobile_evaluator.result(),unstable=bool(unstable),physical_run=False)
