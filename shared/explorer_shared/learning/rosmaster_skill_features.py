"""ENGINEERING_GLUE: source sensor fusion for one ROSMASTER with explicit validity.

The zero placeholders below are model tensors paired with explicit validity,
never synthetic measurements or invented prior command coordinates.
"""
from __future__ import annotations
import io
import math

import numpy as np
from PIL import Image

from explorer_shared.contracts import ACTION_NAMES,ACTION_UNITS

FEATURE_GROUPS=(('preceding_command9',9),('joint_feedback',6),('joint_command',6),('odometry_pose',7),('base_velocity',3),('imu',6),('lidar',8),('goal',3),('gripper',1),('camera_depth',4))
FEATURE_NAMES=tuple(group+'.'+str(index) for group,size in FEATURE_GROUPS for index in range(size))
FEATURE_UNITS=tuple(ACTION_UNITS)+('deg',)*12+('m',)*3+('1',)*4+('m/s','m/s','rad/s')+('m/s^2',)*3+('rad/s',)*3+('m',)*16


class PipelineBlocked(ValueError):
    def __init__(self,code,detail='',*,episode_id=None):
        self.code,self.detail,self.episode_id=code,detail,episode_id
        super().__init__(code+(': '+detail if detail else ''))
    def document(self):return dict(code=self.code,detail=self.detail,episode_id=self.episode_id)


def channel(sample,name):
    record=(sample.get('channels') or {}).get(name) or {}
    return record.get('value') if record.get('available') is True and record.get('present') is True and record.get('_model_admitted') is not False else None


def _vector(value,length,mask=True):
    values=np.zeros(length,np.float32);valid=np.zeros(length,bool)
    if isinstance(value,(list,tuple)) and len(value)==length:
        masks=[mask]*length if isinstance(mask,bool) else mask
        if isinstance(masks,list) and len(masks)==length:
            for index,item in enumerate(value):
                if masks[index] is True and isinstance(item,(float,int)) and not isinstance(item,bool) and math.isfinite(item):values[index]=item;valid[index]=True
    return values,valid


def rgb_image(record,read_payload):
    if not isinstance(record,dict):raise PipelineBlocked('RGB_UNAVAILABLE')
    raw=read_payload(record.get('source_file'))
    try:
        if record.get('encoding') in ('rgb8','RGB8') and len(raw)==record['width']*record['height']*3:
            image=Image.frombytes('RGB',(record['width'],record['height']),raw)
        else:image=Image.open(io.BytesIO(raw));image.load()
        if image.mode!='RGB' or image.size!=(record['width'],record['height']):raise ValueError('RGB identity/shape mismatch')
        return np.asarray(image).copy()
    except (OSError,ValueError,KeyError) as error:raise PipelineBlocked('RGB_DECODE_FAILED',str(error)) from error


def depth_image(record,read_payload):
    if not isinstance(record,dict):return None
    try:
        raw=read_payload(record['source_file']);encoding=record.get('encoding','').lower()
        if encoding in ('16uc1','uint16_le','test/u16'):
            depth=np.frombuffer(raw,dtype='<u2').reshape(record['height'],record['width'])
        elif encoding in ('32fc1','float32_le'):depth=np.frombuffer(raw,dtype='<f4').reshape(record['height'],record['width'])
        else:depth=np.asarray(Image.open(io.BytesIO(raw)))
        if depth.shape!=(record['height'],record['width']) or depth.ndim!=2:return None
        return depth.astype(np.float32)*(.001 if record.get('units')=='mm' else 1.)
    except (OSError,KeyError,ValueError):return None


def sensor_features(sample,read_payload):
    groups={};used={}
    for group,length in FEATURE_GROUPS:
        item=channel(sample,group);value=None;mask=((sample.get('channels') or {}).get(group) or {}).get('mask',False)
        if isinstance(item,dict):
            if group in ('preceding_command9','joint_feedback','joint_command','base_velocity'):value=item.get('values')
            elif group=='odometry_pose':value=(item.get('position') or [])+(item.get('orientation_xyzw') or [])
            elif group=='imu':value=(item.get('acceleration') or [])+(item.get('angular_velocity') or [])
            elif group=='goal' and item.get('units')=='m' and item.get('frame_id')==(channel(sample,'odometry_pose') or {}).get('frame_id'):value=item.get('position')
            elif group=='gripper' and item.get('units')=='m':value=[item.get('aperture')]
        vector,valid=_vector(value,length,mask)
        if group=='lidar' and isinstance(item,dict) and mask is True:
            buckets=[[] for _ in range(length)]
            for index,distance in enumerate(item.get('ranges') or []):
                if isinstance(distance,(int,float)) and math.isfinite(distance) and distance>0:
                    angle=(item['angle_min_rad']+index*item['angle_increment_rad'])%(2*math.pi)
                    buckets[min(length-1,int(angle/(2*math.pi)*length))].append(distance)
            for index,bucket in enumerate(buckets):
                if bucket:vector[index]=min(bucket);valid[index]=True
        if group=='camera_depth' and isinstance(item,dict) and mask is True:
            depth=depth_image(item,read_payload)
            if depth is not None:
                for index,tile in enumerate([part for row in np.array_split(depth,2,axis=0) for part in np.array_split(row,2,axis=1)]):
                    measured=tile[np.isfinite(tile)&(tile>0)]
                    if measured.size:vector[index]=float(np.median(measured));valid[index]=True
        groups[group]=(vector,valid);used[group]=int(valid.sum())
    prior=channel(sample,'preceding_command9') or {}
    if prior.get('names')!=list(ACTION_NAMES) or not groups['preceding_command9'][1].all():raise PipelineBlocked('EXPLICIT_PRIOR_COMMAND9_REQUIRED','Never join joint6 and measured odometry to construct this source channel')
    return np.concatenate([groups[group][0] for group,_ in FEATURE_GROUPS]),np.concatenate([groups[group][1] for group,_ in FEATURE_GROUPS]),used


class TrainNormalizer:
    def __init__(self,values,masks,actions):
        values=np.asarray(values,np.float64);masks=np.asarray(masks,bool);actions=np.asarray(actions,np.float64)
        count=masks.sum(axis=0);self.count=count
        self.mean=(values*masks).sum(axis=0)/np.maximum(count,1)
        variance=(((values-self.mean)*masks)**2).sum(axis=0)/np.maximum(count,1)
        self.scale=np.where(variance>1e-8,np.sqrt(variance),1.)
        self.action_mean=actions.mean(axis=0);variation=actions.std(axis=0);self.action_scale=np.where(variation>1e-4,variation,1.)
    def state(self,values,masks):
        masks=np.asarray(masks,bool)
        normalized=np.where(masks,(np.asarray(values)-self.mean)/self.scale,0.)
        return np.concatenate([normalized,masks.astype(np.float32)]).astype(np.float32)
    def runtime_state(self,values,masks):
        values=np.asarray(values);validity=np.asarray(masks)
        if values.shape!=(len(FEATURE_NAMES),) or validity.shape!=values.shape or validity.dtype.kind!='b':raise PipelineBlocked('RUNTIME_FEATURE_VALUES_OR_VALIDITY_SCHEMA_INVALID')
        if not np.isfinite(values[validity]).all():raise PipelineBlocked('RUNTIME_VALID_FEATURE_NONFINITE')
        unseen=validity&(self.count==0)
        if unseen.any():raise PipelineBlocked('UNTRAINED_VALID_SENSOR_FEATURES',','.join(np.asarray(FEATURE_NAMES)[unseen].tolist()))
        return self.state(values,validity)
    def action(self,values):return ((np.asarray(values)-self.action_mean)/self.action_scale).astype(np.float32)
    def native_action(self,values):return np.asarray(values)*self.action_scale+self.action_mean
    @classmethod
    def from_document(cls,document):
        if document.get('feature_names')!=list(FEATURE_NAMES) or document.get('feature_units')!=list(FEATURE_UNITS) or document.get('fit_partition')!='train':raise PipelineBlocked('MODEL_FEATURE_NORMALIZATION_SCHEMA_MISMATCH')
        instance=cls.__new__(cls)
        for name,key,size in (('mean','means',len(FEATURE_NAMES)),('scale','scales',len(FEATURE_NAMES)),('count','valid_training_counts',len(FEATURE_NAMES)),('action_mean','action_mean',9),('action_scale','action_scale',9)):
            value=np.asarray(document.get(key),float)
            if value.shape!=(size,) or not np.isfinite(value).all():raise PipelineBlocked('MODEL_NORMALIZATION_VALUES_INVALID',key)
            setattr(instance,name,value)
        if np.any(instance.scale<=0) or np.any(instance.action_scale<=0):raise PipelineBlocked('MODEL_NORMALIZATION_SCALE_INVALID')
        if np.any(instance.count<0) or np.any(instance.count!=np.floor(instance.count)):raise PipelineBlocked('MODEL_NORMALIZATION_COUNTS_INVALID')
        return instance
    def document(self):
        return dict(schema='rosmaster_train_only_sensor_normalization_v1',feature_names=list(FEATURE_NAMES),feature_units=list(FEATURE_UNITS),mask_names=[name+'.valid' for name in FEATURE_NAMES],
            means=self.mean.tolist(),scales=self.scale.tolist(),valid_training_counts=self.count.tolist(),action_mean=self.action_mean.tolist(),action_scale=self.action_scale.tolist(),
            fit_partition='train',masks_normalized=False,missing_numeric_placeholders_are_not_measurements=True,prior9_manufactured=False)
