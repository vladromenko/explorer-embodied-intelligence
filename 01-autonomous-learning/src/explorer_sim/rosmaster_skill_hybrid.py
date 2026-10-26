"""Internal hybrid components of ONE ROSMASTER9D policy, no hardware transport."""
from __future__ import annotations
import heapq
import math

import numpy as np

from .rosmaster_skill_features import PipelineBlocked,channel,depth_image

NAV_PHASES={'find_object','localize_object','approach_align','find_goal','retained_transport','retreat','reference','navigate','stop','verify_goal'}


def astar(cells,start,goal):
    try:grid=np.asarray(cells)
    except (TypeError,ValueError) as error:raise PipelineBlocked('NAV_GRID_INVALID',str(error)) from error
    if grid.ndim!=2 or grid.size>1024**2 or not np.isin(grid,[-1,0,1]).all():raise PipelineBlocked('NAV_GRID_INVALID')
    if grid.ndim!=2 or not all(0<=point[0]<grid.shape[0] and 0<=point[1]<grid.shape[1] for point in (start,goal)):raise PipelineBlocked('NAV_GRID_BOUNDS')
    if grid[start]!=0 or grid[goal]!=0:raise PipelineBlocked('NAV_UNKNOWN_OR_OCCUPIED_ENDPOINT')
    frontier=[(0.,start)];cost={start:0.};parent={}
    while frontier:
        _,current=heapq.heappop(frontier)
        if current==goal:
            path=[goal]
            while path[-1]!=start:path.append(parent[path[-1]])
            return path[::-1]
        for dx,dy in ((1,0),(-1,0),(0,1),(0,-1)):
            following=(current[0]+dx,current[1]+dy)
            if 0<=following[0]<grid.shape[0] and 0<=following[1]<grid.shape[1] and grid[following]==0:
                proposed=cost[current]+1.
                if proposed<cost.get(following,float('inf')):
                    cost[following]=proposed;parent[following]=current
                    heuristic=abs(goal[0]-following[0])+abs(goal[1]-following[1])
                    heapq.heappush(frontier,(proposed+heuristic,following))
    raise PipelineBlocked('NAV_PATH_UNAVAILABLE','Unknown cells are not silently declared free')


def calibrated_goal(sample,read_payload):
    goal=channel(sample,'goal') or {};pose=channel(sample,'odometry_pose') or {};calibration=channel(sample,'camera_calibration') or {}
    if not calibration.get('calibration_id'):raise PipelineBlocked('GOAL_CAMERA_CALIBRATION_MISSING')
    if goal.get('position') is not None:
        point=np.asarray(goal['position'],float)
        if point.shape!=(3,) or not np.isfinite(point).all() or goal.get('units')!='m' or goal.get('frame_id')!=pose.get('frame_id') or goal.get('calibration_id')!=calibration['calibration_id']:
            raise PipelineBlocked('GOAL_GEOMETRY_FRAME_OR_CALIBRATION_MISMATCH')
        return point,dict(method='source_bound_calibrated_goal_position',calibration_id=calibration['calibration_id'])
    detections=channel(sample,'detections') or {};rgb=channel(sample,'camera_rgb') or {};depth_record=channel(sample,'camera_depth')
    target=next((item for item in detections.get('detections',[]) if item.get('id')==goal.get('detection_id')),None)
    if not target:raise PipelineBlocked('CALIBRATED_GOAL_OR_LOCKED_DETECTION_MISSING')
    if detections.get('frame_id')!=rgb.get('frame_id') or calibration.get('frame_id')!=pose.get('frame_id') or not isinstance(target.get('bbox_xyxy'),list):raise PipelineBlocked('GOAL_DETECTION_FRAME_UNCONFIRMED')
    depth=depth_image(depth_record,read_payload)
    if depth is None:raise PipelineBlocked('GOAL_DEPTH_GEOMETRY_MISSING','RGB/VLM alone is not metric geometry')
    if rgb.get('camera_id')!=calibration.get('camera_id') or depth_record.get('camera_id')!=calibration.get('depth_camera_id') or depth_record.get('aligned_to_rgb_frame_id')!=rgb.get('frame_id'):raise PipelineBlocked('GOAL_RGB_DEPTH_CALIBRATION_ALIGNMENT_UNVERIFIED')
    if any(abs(value)>1e-10 for value in calibration.get('distortion',[])):raise PipelineBlocked('GOAL_DISTORTION_UNDISTORTION_NOT_QUALIFIED','Use a producer-verified undistorted stream or metric goal receipt')
    bounds=np.asarray(target['bbox_xyxy'],float)
    if bounds.shape!=(4,) or not np.isfinite(bounds).all():raise PipelineBlocked('INVALID_LOCKED_DETECTION')
    x0,y0,x1,y1=np.rint(bounds).astype(int)
    if not (0<=x0<x1<=depth.shape[1] and 0<=y0<y1<=depth.shape[0]):raise PipelineBlocked('INVALID_LOCKED_DETECTION')
    distances=depth[y0:y1,x0:x1];distances=distances[np.isfinite(distances)&(distances>0)]
    if not distances.size:raise PipelineBlocked('GOAL_DEPTH_EMPTY')
    z=float(np.median(distances));u,v=(x0+x1)/2,(y0+y1)/2
    K=np.asarray(calibration['intrinsics'],float).reshape(3,3)
    if K[0,0]<=0 or K[1,1]<=0:raise PipelineBlocked('GOAL_INTRINSICS_INVALID')
    camera=np.array([(u-K[0,2])*z/K[0,0],(v-K[1,2])*z/K[1,1],z,1.])
    transform=np.asarray(calibration['extrinsics'],float).reshape(4,4);point=(transform@camera)[:3]
    return point,dict(method='locked_RGB_detection_with_recorded_aligned_depth_and_calibration',calibration_id=calibration['calibration_id'],detection_id=target['id'],depth_samples=int(distances.size))


def navigation_action(sample,goal,act_action):
    pose=channel(sample,'odometry_pose') or {};lidar=channel(sample,'lidar') or {};prior=channel(sample,'preceding_command9') or {}
    if not pose.get('position') or not pose.get('orientation_xyzw'):raise PipelineBlocked('NAV_ODOMETRY_MISSING')
    grid=lidar.get('occupancy_grid')
    if not isinstance(grid,dict):raise PipelineBlocked('NAV_SOURCE_BOUND_OCCUPANCY_GRID_MISSING','The baseline consumes observed mapped free/occupied/unknown space, not fabricated empty room geometry')
    if grid.get('frame_id')!=pose.get('frame_id'):raise PipelineBlocked('NAV_MAP_ODOMETRY_FRAME_MISMATCH')
    resolution=grid.get('resolution_m');origin=np.asarray(grid.get('origin_xy_m'),float)
    if not isinstance(resolution,(int,float)) or isinstance(resolution,bool) or not math.isfinite(resolution) or resolution<=0 or origin.shape!=(2,) or not np.isfinite(origin).all():raise PipelineBlocked('NAV_MAP_GEOMETRY_INVALID')
    robot=np.asarray(pose['position'][:2],float);goal=np.asarray(goal,float)
    cell=lambda point:tuple(np.floor((point[:2]-origin)/resolution).astype(int)[::-1])
    radius=grid.get('robot_radius_m')
    if not isinstance(radius,(int,float)) or not 0<radius<2 or not isinstance(grid.get('robot_geometry_id'),str) or not grid['robot_geometry_id'].strip():raise PipelineBlocked('NAV_ROBOT_FOOTPRINT_GEOMETRY_MISSING')
    try:cells=np.asarray(grid.get('cells'))
    except (ValueError,TypeError) as error:raise PipelineBlocked('NAV_GRID_INVALID',str(error)) from error
    margin=int(math.ceil(radius/resolution))
    if cells.ndim!=2 or cells.size>1024**2 or not np.isin(cells,[-1,0,1]).all() or margin>64 or 2*margin>=min(cells.shape):raise PipelineBlocked('NAV_GRID_OR_FOOTPRINT_INVALID')
    blocked=cells!=0;inflated=blocked.copy()
    for dx in range(-margin,margin+1):
        for dy in range(-margin,margin+1):
            if dx*dx+dy*dy<=margin*margin:
                target_x=slice(max(dx,0),min(cells.shape[0]+dx,cells.shape[0]));source_x=slice(max(-dx,0),min(cells.shape[0]-dx,cells.shape[0]))
                target_y=slice(max(dy,0),min(cells.shape[1]+dy,cells.shape[1]));source_y=slice(max(-dy,0),min(cells.shape[1]-dy,cells.shape[1]))
                inflated[target_x,target_y]|=blocked[source_x,source_y]
    inflated[:margin,:]=True;inflated[-margin:,:]=True;inflated[:,:margin]=True;inflated[:,-margin:]=True
    path=astar(np.where(inflated,1,cells),cell(robot),cell(goal));waypoint=path[min(2,len(path)-1)]
    world=origin+(np.array(waypoint[::-1])+.5)*resolution;error=world-robot
    q=np.asarray(pose['orientation_xyzw'],float)
    if q.shape!=(4,) or not .9<np.linalg.norm(q)<1.1:raise PipelineBlocked('NAV_ORIENTATION_INVALID')
    q=q/np.linalg.norm(q);yaw=math.atan2(2*(q[3]*q[2]+q[0]*q[1]),1-2*(q[1]**2+q[2]**2))
    velocity=np.array([[math.cos(yaw),math.sin(yaw)],[-math.sin(yaw),math.cos(yaw)]])@error
    speed=np.linalg.norm(velocity)
    if speed>.15:velocity*=.15/speed
    if np.linalg.norm(goal[:2]-robot)<resolution*.5:velocity[:]=0.
    result=np.asarray(act_action,float).copy()
    if result.shape!=(9,) or len(prior.get('values',[]))!=9:raise PipelineBlocked('SIMULTANEOUS_ACTION9_REQUIRED')
    result[:6]=prior['values'][:6];result[6:9]=[velocity[0],velocity[1],0.]
    return result,dict(method='A_star_4_connected_observed_grid_with_body_footprint_and_trajectory_tracking',path_cells=[[int(value) for value in point] for point in path],robot_radius_m=radius,robot_geometry_id=grid['robot_geometry_id'],full9d=True,hardware_io=False)


class PhaseModel:
    def __init__(self,phases):self.phases=list(phases);self.centroids={}
    def fit(self,features,labels):
        for phase in self.phases:
            selected=[row for row,label in zip(features,labels) if label==phase]
            if selected:self.centroids[phase]=np.asarray(selected,float).mean(axis=0)
        return self
    def predict(self,features):
        scores=sorted((float(np.linalg.norm(np.asarray(features)-value)),phase) for phase,value in self.centroids.items())
        if not scores:raise PipelineBlocked('PHASE_MODEL_NOT_FITTED')
        if len(scores)>1 and abs(scores[1][0]-scores[0][0])<1e-8:raise PipelineBlocked('PHASE_AMBIGUOUS_FROM_OBSERVATIONS')
        return scores[0][1]
    def document(self):return dict(method='supervised_observation_prototype_phase_classifier',phases=self.phases,centroids={name:value.tolist() for name,value in self.centroids.items()},source_phase_labels_are_training_targets_not_actor_inputs=True)


class PhaseCoordinator:
    """Measured progress guards; labels/timers alone cannot complete a phase."""
    def __init__(self,phases,max_unconfirmed_steps=30):
        self.phases=list(phases);self.index=0;self.waited=0;self.max_unconfirmed_steps=max_unconfirmed_steps;self.completed=[];self.recovery_count=0
    def observe(self,predicted_phase,evidence=None,*,verify_evidence=None):
        if self.index==len(self.phases):return dict(state='COMPLETE',full_task_success=True,completed=list(self.completed))
        current=self.phases[self.index];evidence=evidence or {};self.waited+=1
        accepted=(predicted_phase==current and evidence.get('phase_id')==current and evidence.get('status')=='success' and evidence.get('independent_of_action_commands') is True and callable(verify_evidence) and verify_evidence(evidence) is True)
        if accepted:
            self.completed.append(current);self.index+=1;self.waited=0
        state='COMPLETE' if self.index==len(self.phases) else 'RECOVERY_REQUIRED' if self.waited>=self.max_unconfirmed_steps else 'ACTIVE'
        return dict(state=state,full_task_success=state=='COMPLETE',completed=list(self.completed),next_phase=self.phases[self.index] if self.index<len(self.phases) else None,
            automatic_release=False,hardware_io=False)
    def recover(self,receipt,*,verify_evidence=None):
        if not callable(verify_evidence) or verify_evidence(receipt) is not True or receipt.get('kind')!='measured_relocalization':raise PipelineBlocked('RECOVERY_RELOCALIZATION_EVIDENCE_REQUIRED')
        self.waited=0;self.recovery_count+=1
        return dict(state='ACTIVE',phase=self.phases[self.index] if self.index<len(self.phases) else None,recovery_count=self.recovery_count,policy_reset_required=True,automatic_motor_action=False)
