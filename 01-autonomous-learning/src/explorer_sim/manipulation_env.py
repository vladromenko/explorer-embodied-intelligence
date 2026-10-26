"""Contact-only CAD candy/sock tasks; EXPLORER_CAD_MUJOCO, ENGINEERING_GLUE.

Simulation parameters are estimates, never physical controller settings. The
separate evaluator sees physics truth; deployable observations are RGB+command9.
"""
from __future__ import annotations

import json
from pathlib import Path
import xml.etree.ElementTree as ET

import gymnasium as gym
import mujoco
import numpy as np

from legacy.simulator.explorer_model import ARM_JOINTS, ROOT, build_mjcf, servo_to_joint
from legacy.simulator.environment import ExplorerEnv

LOW = np.array([0, 0, 0, 0, 0, 30.], float)
HIGH = np.array([180, 180, 180, 180, 270, 170.], float)
HOME = np.array([90, 90, 90, 90, 90, 30.], float)
STATE_SPEC = mujoco.mjtState.mjSTATE_INTEGRATION
TASKS = ('candy_to_tray', 'sock_to_basket')


def task_xml(mesh_dir, task):
    if task not in TASKS:
        raise ValueError('Unknown complete manipulation task')
    root = ET.fromstring(build_mjcf(mesh_dir=mesh_dir, scene='sock' if task == TASKS[1] else 'empty',
                                    sock_position=(.20, 0, .015), bin_position=(.22, .19, 0)))
    # The tested MuJoCo 3.17 rejects the former `internal` XML attribute.
    # The original free-vertex shell, self-contact and elasticity stay intact.
    for contact in root.findall('.//flexcomp/contact'):
        contact.attrib.pop('internal', None)
    ET.SubElement(root.find('equality'), 'weld', name='manipulation_base_fixture', body1='base_link')
    world = root.find('worldbody')
    if task == TASKS[0]:
        candy = ET.SubElement(world, 'body', name='candy', pos='.20 0 .014')
        ET.SubElement(candy, 'freejoint', name='candy_free')
        ET.SubElement(candy, 'inertial', pos='0 0 0', mass='.0126', diaginertia='0.0000011 0.0000025 0.0000028')
        ET.SubElement(candy, 'geom', name='candy_contact', type='box', size='.020 .012 .010',
                      mass='.012', contype='4', conaffinity='7', friction='1.0 .005 .0001', rgba='.85 .08 .45 1')
        # An explicitly modelled wrapped rigid candy, not the legacy cookie.
        for side in (-1, 1):
            ET.SubElement(candy, 'geom', name=f'candy_wrapper_{side}', type='ellipsoid',
                          pos=f'{side*.025} 0 0', size='.007 .006 .006', mass='.0003',
                          contype='4', conaffinity='7', friction='1 .005 .0001', rgba='.95 .65 .12 1')
        tray = ET.SubElement(world, 'body', name='bin', pos='.22 .19 0')
        for name, pos, size in [('bottom', [0,0,.004], [.073,.063,.004]),
                ('left',[0,.067,.022],[.073,.004,.022]), ('right',[0,-.067,.022],[.073,.004,.022]),
                ('front',[.077,0,.022],[.004,.063,.022]), ('back',[-.077,0,.022],[.004,.063,.022])]:
            ET.SubElement(tray, 'geom', name='bin_'+name, type='box', pos=' '.join(map(str,pos)),
                          size=' '.join(map(str,size)), contype='2', conaffinity='5', rgba='.15 .5 .55 1')
    return ET.tostring(root, encoding='unicode')


class FullTaskEvaluator:
    """Independent substep criteria, unrelated to actor/reward phase or IK plan."""
    def __init__(self, initial_points, goal, half, rim, dt):
        self.initial_height = float(initial_points[:,2].mean())
        self.goal, self.half, self.rim, self.dt = np.array(goal), np.array(half), rim, dt
        self.grasped = self.lifted = self.carried = self.success = False
        self.grasp_seconds = self.lift_seconds = self.settle_seconds = 0.
        self.max_height_gain = 0.
        self.substeps = self.contact_substeps = self.bilateral_substeps = 0
        self.last = {}

    def observe(self, points, speeds, pad_forces, support_force, command_open):
        self.substeps += 1
        bilateral = bool(np.all(np.asarray(pad_forces) > .0001))
        self.contact_substeps += int(any(f > .0001 for f in pad_forces))
        self.bilateral_substeps += int(bilateral)
        self.grasp_seconds = self.grasp_seconds + self.dt if bilateral else 0.
        self.grasped |= self.grasp_seconds >= .08
        gain = float(points[:,2].mean()) - self.initial_height
        self.max_height_gain = max(self.max_height_gain, gain)
        self.lift_seconds = self.lift_seconds + self.dt if bilateral and gain >= .045 else 0.
        self.lifted |= self.grasped and self.lift_seconds >= .20
        inside = bool(np.all(np.abs(points[:,:2] - self.goal[:2]) < self.half))
        above_rim = bool(float(points[:,2].min()) > self.goal[2] + self.rim + .005)
        self.carried |= self.lifted and inside and above_rim and bilateral
        on_support = bool(self.goal[2]+.003 <= float(points[:,2].min()) <= self.goal[2]+.020)
        within_rim = bool(float(points[:,2].max()) < self.goal[2]+self.rim+.025)
        free = not any(f > .0001 for f in pad_forces)
        settled = float(np.max(speeds)) < .05
        placed = self.carried and inside and on_support and within_rim and free and command_open and settled and support_force > .0001
        self.settle_seconds = self.settle_seconds + self.dt if placed else 0.
        self.success |= self.settle_seconds >= .5
        self.last = dict(inside_all_points=inside, above_rim=above_rim, bottom_supported=on_support,
                         within_rim=within_rim, free_of_fingers=free, settled=settled,
                         support_force_N=float(support_force), pad_force_N=list(map(float,pad_forces)),
                         height_bounds_m=[float(points[:,2].min()),float(points[:,2].max())])

    def result(self):
        return dict(success=bool(self.success), grasped=bool(self.grasped), lifted=bool(self.lifted),
                    carried_above_target=bool(self.carried), max_height_gain_m=self.max_height_gain,
                    settled_seconds=self.settle_seconds, substeps=self.substeps,
                    contact_substeps=self.contact_substeps, bilateral_substeps=self.bilateral_substeps, final=self.last)


class ManipulationEnv(gym.Env):
    """Finite torque CAD physics; PPO is a privileged teacher, never a visual actor."""
    metadata = {'render_modes':['rgb_array'], 'render_fps':20}

    def __init__(self, mesh_dir, task='candy_to_tray', horizon=500):
        super().__init__()
        self.task, self.horizon, self.period = task, horizon, .05
        self.xml = task_xml(mesh_dir, task)
        self.model = mujoco.MjModel.from_xml_string(self.xml)
        self.data = mujoco.MjData(self.model)
        self.qids = np.array([self.model.joint(n).qposadr[0] for n in ARM_JOINTS])
        self.dids = np.array([self.model.joint(n).dofadr[0] for n in ARM_JOINTS])
        self.joint_q = self.qids
        self.servo_ids = np.array([self.model.actuator(f'servo{i}').id for i in range(1,7)])
        self.pad_ids = [self.model.geom(n).id for n in ('finger_left','finger_right')]
        self.goal = self.model.body('bin').pos.copy()
        self.half = self.model.geom('bin_bottom').size[:2].copy()
        self.half -= .0 if task == TASKS[0] else .008
        self.rim = .044 if task == TASKS[0] else .12
        motion = json.loads((ROOT/'config/arm-motion.json').read_text())
        self.velocity = np.array(motion['streaming']['velocity_deg_s'])
        self.action_space = gym.spaces.Box(-1,1,(6,),np.float32)
        self.observation_space = gym.spaces.Box(-np.inf,np.inf,(36,),np.float32)
        self.renderer = None
        self.record_substeps = False
        self.substep_trace = []
        self.options = mujoco.MjvOption()
        self.options.flags[mujoco.mjtVisFlag.mjVIS_FLEXFACE] = True
        self.options.flags[mujoco.mjtVisFlag.mjVIS_FLEXEDGE] = False
        self.object_geom_ids = set()
        if task == TASKS[0]:
            self.object_id = self.model.body('candy').id
            self.object_geom_ids = {self.model.geom(n).id for n in ('candy_contact','candy_wrapper_-1','candy_wrapper_1')}
        else:
            self.object_id = None
        self.support_ids = {self.model.geom('bin_bottom').id}
        self.reset(seed=0)

    def points(self):
        if self.object_id is None:
            return self.data.flexvert_xpos.copy()
        result = []
        for gid in self.object_geom_ids:
            half = self.model.geom_size[gid]
            corners = np.array([[x,y,z] for x in (-1,1) for y in (-1,1) for z in (-1,1)])*half
            result.extend(corners @ self.data.geom_xmat[gid].reshape(3,3).T+self.data.geom_xpos[gid])
        return np.asarray(result)

    def speeds(self):
        if self.object_id is not None:
            velocity = np.zeros(6)
            mujoco.mj_objectVelocity(self.model,self.data,mujoco.mjtObj.mjOBJ_BODY,self.object_id,velocity,0)
            point_velocity = velocity[3:]+np.cross(velocity[:3],self.points()-self.data.xpos[self.object_id])
            return np.linalg.norm(point_velocity,axis=1)
        ids = self.model.flex_vertbodyid
        return np.linalg.norm(self.data.cvel[ids,3:],axis=1)

    def jaw_center(self, data=None):
        return np.mean((self.data if data is None else data).geom_xpos[self.pad_ids],axis=0)

    solve_ik = ExplorerEnv.solve_ik

    def reset(self, *, seed=None, options=None):
        super().reset(seed=seed)
        mujoco.mj_resetData(self.model,self.data)
        self.command = HOME.copy()
        self.command_before = self.command.copy()
        self.data.qpos[self.qids] = servo_to_joint(self.command)
        self.data.ctrl[self.servo_ids] = servo_to_joint(self.command)
        if self.object_id is not None:
            adr = self.model.joint('candy_free').qposadr[0]
            self.data.qpos[adr:adr+2] += self.np_random.uniform(-.006,.006,2)
        else:
            offset = self.np_random.uniform(-.006,.006,2)
            for bid in self.model.flex_vertbodyid:
                adr = self.model.jnt_qposadr[self.model.body_jntadr[bid]]
                self.data.qpos[adr:adr+2] += offset
        mujoco.mj_forward(self.model,self.data)
        for _ in range(100):
            mujoco.mj_step(self.model,self.data)
        self.evaluator = FullTaskEvaluator(self.points(),self.goal,self.half,self.rim,self.model.opt.timestep)
        self.substep_trace = []
        self.steps = 0
        self.raw_action = np.zeros(6)
        self.requested = self.command.copy()
        self.phase = 'uncontrolled'
        self.last_distance = np.linalg.norm(self.jaw_center()-self.points().mean(0))
        return self.observation(), self.info()

    def observation(self):
        points = self.points()
        center = points.mean(0)
        stages = [self.evaluator.grasped,self.evaluator.lifted,self.evaluator.carried]
        return np.r_[self.data.qpos[self.qids],self.data.qvel[self.dids],
                     2*(self.command-LOW)/(HIGH-LOW)-1,center,self.goal,
                     self.jaw_center(),center-self.jaw_center(),self.goal-center,stages].astype(np.float32)

    def integration_state(self):
        output = np.empty(mujoco.mj_stateSize(self.model,STATE_SPEC))
        mujoco.mj_getState(self.model,self.data,output,STATE_SPEC)
        return output

    def _substep_evaluate(self):
        pads, support = np.zeros(2), 0.
        force = np.zeros(6)
        for index, contact in enumerate(self.data.contact[:self.data.ncon]):
            is_object = any(g in self.object_geom_ids for g in contact.geom) if self.object_id is not None else any(f >= 0 for f in contact.flex)
            if is_object:
                mujoco.mj_contactForce(self.model,self.data,index,force)
                for j,pad in enumerate(self.pad_ids):
                    if pad in contact.geom:
                        pads[j] += max(0.,force[0])
                if any(g in self.support_ids for g in contact.geom):
                    support += max(0.,force[0])
        self.evaluator.observe(self.points(),self.speeds(),pads,support,self.command[5] <= 50)
        if self.record_substeps:
            points = self.points()
            self.substep_trace.append(np.r_[self.data.time,pads,support,points.mean(0),
                                           points[:,2].min(),points[:,2].max(),max(self.speeds())])

    def step(self, action):
        raw = np.asarray(action,float)
        if raw.shape != (6,) or not np.isfinite(raw).all():
            raise ValueError('Six finite normalized increments required')
        self.raw_action = raw.copy()
        self.requested = self.command + raw*self.velocity*self.period
        bounded = np.clip(self.command+np.clip(raw,-1,1)*self.velocity*self.period,LOW,HIGH)
        return self.step_command(bounded, preserve_request=True)

    def step_command(self, requested, *, preserve_request=False):
        requested = np.asarray(requested,float)
        if requested.shape != (6,) or not np.isfinite(requested).all() or np.any(requested<LOW) or np.any(requested>HIGH):
            raise ValueError('Six endpoint degrees within declared model ranges required')
        if not preserve_request:
            self.requested = requested.copy()
            self.raw_action = (requested-self.command)/(self.velocity*self.period)
        start = self.command.copy()
        self.command_before = start.copy()
        endpoint = start+np.clip(requested-start,-self.velocity*self.period,self.velocity*self.period)
        substeps = round(self.period/self.model.opt.timestep)
        for index in range(substeps):
            self.command = start+(endpoint-start)*(index+1)/substeps
            self.data.ctrl[self.servo_ids] = servo_to_joint(np.clip(self.command,LOW,HIGH))
            mujoco.mj_step(self.model,self.data)
            self._substep_evaluate()
        self.steps += 1
        distance = np.linalg.norm(self.jaw_center()-self.points().mean(0))
        # Shaping uses truth but never defines acceptance. No scripted actions.
        stages = self.evaluator.result()
        reward = 10*(self.last_distance-distance)-distance
        reward += .3*stages['grasped']+1.*stages['lifted']+2.*stages['carried_above_target']+20.*stages['success']
        if stages['lifted']:
            reward -= 2*np.linalg.norm(self.points().mean(0)[:2]-self.goal[:2])
        self.last_distance = distance
        unstable = not np.isfinite(self.data.qpos).all() or any(w.number for w in self.data.warning)
        return self.observation(),float(reward),bool(stages['success'] or unstable),self.steps>=self.horizon,self.info(unstable)

    def info(self, unstable=False):
        return dict(task=self.task,steps=self.steps,sim_time_s=float(self.data.time),
                    pre_action_command6=self.command_before.tolist(),
                    command9=np.r_[self.command,0,0,0].tolist(),requested_endpoint6=self.requested.tolist(),
                    raw_action6=self.raw_action.tolist(),evaluator=self.evaluator.result(),
                    unstable=bool(unstable),physical_run=False)

    def render(self):
        if self.renderer is None:
            self.renderer = mujoco.Renderer(self.model,height=240,width=320)
        self.renderer.update_scene(self.data,camera='overview',scene_option=self.options)
        return self.renderer.render().copy()

    def close(self):
        if self.renderer is not None:
            self.renderer.close()
            self.renderer = None
