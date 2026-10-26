"""Real frozen feature encoder sees actual depth/partial lidar; raw bytes retained."""
from copy import deepcopy
import hashlib
from io import BytesIO

import numpy as np

from explorer_shared.native_model_views import feature_views,NativeFeatureViews
from explorer_shared.learning.rosmaster_skill_features import sensor_features,FEATURE_GROUPS
from explorer_shared.contracts import ACTION_NAMES


def test_original_npz_float32_depth_and_invalid_lidar_rays_reach_frozen_encoder_without_fabricating_values():
    depth=np.array([[1,2,3,4],[2,0,np.nan,4],[5,6,7,np.inf],[5,6,7,8]],np.float32)
    stream=BytesIO();np.savez(stream,rgb=np.zeros((4,4,3),np.uint8),depth=depth,stamp=1.,depth_stamp=1.,frame=np.array('original-optical-frame'))
    raw=stream.getvalue();name='sensors/original.npz'
    channels={'preceding_command9':{'present':True,'available':True,'mask':[True]*9,'value':{'values':[90]*6+[0]*3,'names':list(ACTION_NAMES)}},
        'camera_depth':{'present':True,'available':True,'mask':True,'value':{'source_file':name,'sha256':hashlib.sha256(raw).hexdigest(),
            'encoding':'numpy_npz_depth_m','units':'m','width':4,'height':4,'aligned_to_rgb_frame_id':'original-RGB'}},
        'lidar':{'present':True,'available':True,'mask':{'ranges':False,'range_valid_mask':True},
            'value':{'ranges':[1.,None,3.,None,5.,6.,None,8.],'range_valid_mask':[True,False,True,False,True,True,False,True],
                'raw_ranges':[1.,{'nonfinite':'nan'},3.,{'nonfinite':'+inf'},5.,6.,0.,8.],
                'angle_min_rad':0.,'angle_increment_rad':float(2*np.pi/8),'frame_id':'actual-lidar'}}}
    original=deepcopy(channels);result=feature_views(channels,{name:raw})
    assert channels==original and result['source_payloads'][name]==raw and not result['new_measurements_created']
    values,masks,used=sensor_features({'channels':result['channels']},result['source_payloads'].__getitem__)
    assert used['camera_depth']==4 and used['lidar']==5
    start=sum(size for group,size in FEATURE_GROUPS if group!='camera_depth')
    assert values[start:].tolist()==[2.,4.,5.5,7.]
    assert masks[start:].all() and result['channels']['lidar']['value']['ranges'][1] is None
    assert result['channels']['lidar']['value']['range_valid_mask']==original['lidar']['value']['range_valid_mask']
    assert result['channels']['camera_depth']['value']['aligned_to_rgb_frame_id']=='original-RGB'


def test_unknown_depth_precision_or_inconsistent_lidar_keeps_raw_available_and_reports_format_blocker():
    stream=BytesIO();np.savez(stream,rgb=np.zeros((2,2,3),np.uint8),depth=np.ones((2,2),np.float64),stamp=1.,depth_stamp=1.,frame=np.array('frame'))
    raw=stream.getvalue();channels={'camera_depth':{'available':True,'mask':True,'value':{'source_file':'raw.npz',
        'sha256':hashlib.sha256(raw).hexdigest(),'encoding':'numpy_npz_depth_m','units':'m','width':2,'height':2}},
        'lidar':{'available':True,'mask':{'ranges':False},'value':{'ranges':[None,1.],'range_valid_mask':[True,True]}}}
    result=feature_views(channels,{'raw.npz':raw})
    assert result['channels']==channels and set(result['unsupported_feature_formats'])=={'camera_depth','lidar'}
    assert result['channels']['camera_depth']['available'] and result['source_payloads']['raw.npz']==raw


def test_writer_content_cache_reuses_original_depth_and_retains_source_view_metadata():
    stream=BytesIO();np.savez(stream,rgb=np.zeros((2,2,3),np.uint8),depth=np.ones((2,2),np.float32),stamp=1.,depth_stamp=1.,frame=np.array('frame'))
    raw=stream.getvalue();observation={'channels':{'camera_depth':{'available':True,'mask':True,'value':{'source_file':'raw.npz',
        'sha256':hashlib.sha256(raw).hexdigest(),'encoding':'numpy_npz_depth_m','units':'m','width':2,'height':2}}},
        'source_payloads':{'raw.npz':raw},'diagnostics':{'source_clock_alignment_verified':False}}
    encoder=NativeFeatureViews()
    for _ in range(100):view=encoder.convert(observation)
    assert encoder.encodes==1 and len(encoder.cache)==1
    assert view['source_payloads']['raw.npz']==raw and view['diagnostics']['model_format_view']['views']['camera_depth']
    assert view['channels']['camera_depth']['value']['model_view']['added_measurement'] is False
