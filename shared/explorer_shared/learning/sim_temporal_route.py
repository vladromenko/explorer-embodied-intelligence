"""ENGINEERING_GLUE: causal clock/increment features for a NEW SIM candidate.

These are representation helpers, not a phase oracle or controller. They never
query an evaluator or dispatch an action. Old frozen ACT artifacts stay intact.
"""
from collections import deque
from copy import deepcopy
import json
from pathlib import Path

import numpy as np

from explorer_shared.contracts import digest
from .sim_object_route import VisualObjectChunks,actor_observation,LOW,HIGH,sha

BASE_SPEED=np.array([.2,.2,.6],float)
CELLS={'ZERO_ABS':dict(clock='ZERO',labels='ABSOLUTE9'),
    'ELAPSED_ABS':dict(clock='ELAPSED',labels='ABSOLUTE9'),
    'ZERO_INCREMENT':dict(clock='ZERO',labels='DISPATCHED_INCREMENT9'),
    'ELAPSED_INCREMENT':dict(clock='ELAPSED',labels='DISPATCHED_INCREMENT9')}


def _profile(velocity6,period_s):
    velocity=np.asarray(velocity6,float)
    if velocity.shape!=(6,) or not np.isfinite(velocity).all() or np.any(velocity<=0) or type(period_s) not in (float,int) or not np.isfinite(period_s) or not 0<period_s<=1:
        raise ValueError('Actual unchanged native velocity6/period profile required')
    return velocity*period_s


def increment_targets(prior9,dispatched9,*,velocity6,period_s):
    """Labels are actual dispatched deltas, never requested/teacher endpoints."""
    prior=np.asarray(prior9,float);actual=np.asarray(dispatched9,float);scale=_profile(velocity6,period_s)
    if prior.ndim!=2 or prior.shape[1]!=9 or actual.shape!=prior.shape or not len(prior) or not np.isfinite([prior,actual]).all():raise ValueError('Actual finite paired prior9/dispatched9 required')
    delta=actual[:,:6]-prior[:,:6]
    if np.any(abs(delta)>scale+1e-5) or np.any(abs(actual[:,6:])>BASE_SPEED+1e-8):raise ValueError('Dispatched source exceeds original rate/action contract')
    if len(prior)>1 and not np.allclose(prior[1:],actual[:-1],atol=1e-5,rtol=0):raise ValueError('Literal prior9 must follow actual previous dispatch')
    return np.c_[delta/scale,actual[:,6:]/BASE_SPEED].astype(np.float32)


def decode_increment9(predicted9,prior9,*,velocity6,period_s):
    """Decode all9; original executor separately owns clipping/rate constraints."""
    predicted=np.asarray(predicted9,float);prior=np.asarray(prior9,float);scale=_profile(velocity6,period_s)
    if predicted.shape!=(9,) or prior.shape!=(9,) or not np.isfinite([predicted,prior]).all():raise ValueError('Finite learned9 and literal actual prior9 required')
    return np.r_[prior[:6]+predicted[:6]*scale,predicted[6:]*BASE_SPEED]


class ElapsedNativeClock:
    """Only already-observed native timestamps; no phase, future clock or step."""
    def __init__(self,declared_horizon_s):
        if type(declared_horizon_s) not in (int,float) or not 0<declared_horizon_s<=1000:raise ValueError('Explicit finite protocol horizon required')
        self.horizon_s=float(declared_horizon_s);self.first=None;self.last=None;self.boot=None
    def observe(self,stamp):
        tick=stamp.get('monotonic_ns');boot=stamp.get('boot_id')
        if not isinstance(boot,str) or not boot or stamp.get('clock_domain')!=boot or type(tick) is not int or tick<0:raise ValueError('Actual same-domain native monotonic timestamp required')
        if self.first is None:self.first=tick;self.boot=boot
        if boot!=self.boot or self.last is not None and tick<=self.last:raise ValueError('New episode requires new clock; stale/reversed/future-domain timestamps rejected')
        elapsed=(tick-self.first)/1e9
        if elapsed>self.horizon_s+1e-9:raise ValueError('Actual elapsed clock exceeds declared episode horizon')
        self.last=tick
        return np.float32(elapsed/self.horizon_s),dict(elapsed_s=elapsed,source='observed_native_clock_since_first_actual_frame',available=True,mask=True,future_or_phase_input=False)


def source_rate_profile(teacher_folder,arm_motion_path):
    """Cross-check every dispatch against the actual captured native profile."""
    from explorer_shared.sim_initial_state import decode
    folder=Path(teacher_folder);result=json.loads((folder/'result.json').read_bytes());trace_raw=(folder/'target-trace.json').read_bytes()
    if sha(trace_raw)!=result['target_trace_sha256'] or not result['eligible_as_simulation_expert']:raise ValueError('Original independently admitted teacher trace required')
    rows=json.loads(trace_raw);initial=folder/'initial-state';manifest_raw=(initial/'manifest.json').read_bytes()
    if sha(manifest_raw)!=result['initial_state']['manifest_sha256']:raise ValueError('Original captured initial manifest differs')
    manifest=json.loads(manifest_raw);profile_raw=(initial/'profile-state.json').read_bytes()
    if sha(profile_raw)!=manifest['payloads']['profile-state.json']['sha256']:raise ValueError('Original native static profile differs')
    static=decode(json.loads(profile_raw)['static']);velocity=np.asarray(static['velocity'],float);period=static['period'];_profile(velocity,period)
    config=json.loads(Path(arm_motion_path).read_bytes())
    if not np.array_equal(velocity,config['streaming']['velocity_deg_s']):raise ValueError('Source original motion velocity differs from captured runtime')
    for row in rows:
        prep=row.get('dispatch_preparation') or {}
        if prep.get('controller_settings_modified') is not False or prep.get('original_velocity_deg_s')!=velocity.tolist() or prep.get('original_period_s')!=period:
            raise ValueError('Every actual dispatch must retain the same unchanged velocity/period')
    return dict(velocity_deg_s=velocity.tolist(),period_s=period,verified_dispatch_rows=len(rows),
        raw_trace_sha256=result['target_trace_sha256'],initial_manifest_sha256=sha(manifest_raw),profile_state_sha256=sha(profile_raw),
        arm_motion_sha256=sha(Path(arm_motion_path).read_bytes()),physical_controller_settings_changed=False)


class TemporalObjectChunks:
    """Identical source/sampler architecture across the four experimental cells."""
    def __init__(self,manifest_path,*,cell,chunk_size,image_size,rate_profile,horizon_s):
        if cell not in CELLS:raise ValueError('Preregistered factorial cell required')
        self.source=VisualObjectChunks(manifest_path,chunk_size,image_size);self.cell=cell;self.config=CELLS[cell]
        self.chunk_size=chunk_size;self.image_size=image_size;self.rate=deepcopy(rate_profile);self.horizon_s=float(horizon_s);self.derived={}
        ElapsedNativeClock(horizon_s)
        for index in range(len(self.source.episodes)):
            data=self.source._load(index);ticks=data['frame_monotonic_ns'];elapsed=(ticks-ticks[0])/1e9
            if np.any(elapsed<0) or elapsed[-1]>horizon_s:raise ValueError('Actual recorded elapsed clock exceeds declared protocol horizon')
            clock=(elapsed/horizon_s).astype(np.float32)
            labels=(data['action_native']-LOW)*2/(HIGH-LOW)-1 if self.config['labels']=='ABSOLUTE9' else increment_targets(data['state_native'],data['action_native'],velocity6=self.rate['velocity_deg_s'],period_s=self.rate['period_s'])
            self.derived[index]=dict(clock=clock,labels=labels.astype(np.float32))
        self.resume_context=deepcopy(self.source.resume_context)
        self.resume_context.update(source_signature=digest(dict(original_source_signature=self.source.resume_context['source_signature'],cell=cell,rate=self.rate,horizon_s=horizon_s)),
            factorial_cell=cell,actual_recorded_clock_available=True,clock_value_used=self.config['clock']=='ELAPSED',
            clock_formula='observed_elapsed_native_s/declared_protocol_horizon_s',clock_zero_is_model_ablation_not_unavailability=True,
            action_label_representation=self.config['labels'],state_dimension=10,action_dimension=9,phase_or_truth_input=False)
        for path in (Path(__file__),Path(__file__).with_name('sim_object_route.py')):
            self.resume_context['source_files'].append(dict(path=str(path.resolve()),sha256=sha(path.read_bytes())))
    def __len__(self):return len(self.source)
    def __getitem__(self,index):
        import torch
        from .act_backend import IMAGE_KEY,prepare_rgb
        episode,position=self.source.indices[index];data=self.source._load(episode);derived=self.derived[episode]
        _,state=actor_observation(data['rgb'][position],data['state_native'][position],self.image_size)
        clock=derived['clock'][position] if self.config['clock']=='ELAPSED' else np.float32(0)
        positions=position+np.arange(self.chunk_size);pad=positions>=len(data['rgb']);positions=np.minimum(positions,len(data['rgb'])-1)
        return {IMAGE_KEY:prepare_rgb(data['rgb'][position][None])[0],
            'observation.state':torch.from_numpy(np.r_[state,clock].astype(np.float32)),
            'action':torch.from_numpy(derived['labels'][positions]),'action_is_pad':torch.from_numpy(pad)}


class TemporalChunkDispatcher:
    """Fixed chunk8; the network sees RGB/prior9/observed elapsed clock only."""
    def __init__(self,predict_chunk,*,cell,image_size,rate_profile,horizon_s):
        if cell not in CELLS:raise ValueError('Preregistered factorial cell required')
        self.predict_chunk=predict_chunk;self.cell=cell;self.config=CELLS[cell];self.image_size=image_size;self.rate=deepcopy(rate_profile);self.horizon_s=horizon_s;self.reset()
    def reset(self):self.pending=deque();self.clock=ElapsedNativeClock(self.horizon_s);self.origin=None
    def next(self,rgb,prior9,stamp):
        image,state=actor_observation(rgb,prior9,self.image_size);clock,clock_receipt=self.clock.observe(stamp)
        value=clock if self.config['clock']=='ELAPSED' else np.float32(0);state10=np.r_[state,value].astype(np.float32)
        predicted=False;full_chunk=None
        if not self.pending:
            chunk=np.asarray(self.predict_chunk(image,state10),float)
            if chunk.ndim!=2 or chunk.shape[1]!=9 or len(chunk)<8 or not np.isfinite(chunk).all():raise ValueError('Actual finite neural chunk[K>=8,9] required')
            self.pending.extend(row.copy() for row in chunk[:8]);self.origin=deepcopy(stamp);predicted=True;full_chunk=chunk.tolist()
        selected=self.pending.popleft()
        raw=LOW+(selected+1)*(HIGH-LOW)/2 if self.config['labels']=='ABSOLUTE9' else decode_increment9(selected,prior9,velocity6=self.rate['velocity_deg_s'],period_s=self.rate['period_s'])
        return raw,dict(cell=self.cell,strategy='fixed_chunk8',new_neural_prediction=predicted,prediction_origin=deepcopy(self.origin),
            dispatch_observation=deepcopy(stamp),actual_clock=clock_receipt,clock_model_value=float(value),clock_value_used=self.config['clock']=='ELAPSED',
            clock_sensor_available=True,clock_availability_mask=True,selected_normalized_output9=selected.tolist(),full_normalized_neural_chunk9=full_chunk,
            pending_actions=len(self.pending),action_representation=self.config['labels'],source_waypoints_or_phase_truth_used=False)
