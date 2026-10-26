"""ENGINEERING_GLUE: lossless sensor format views for the frozen ACT encoder.

Raw original assets/records remain unchanged and retained. These are source-data
adapters, not training/normalization/control changes or measured new sensors.
"""
from copy import deepcopy
import hashlib
from io import BytesIO
import math
from collections import OrderedDict
import threading


def depth_tiff(raw,width,height):
    import numpy as np
    from PIL import Image
    from .native_sensor_cache import check_rgbd_container
    check_rgbd_container(raw,width,height)
    with np.load(BytesIO(raw),allow_pickle=False) as arrays:depth=arrays['depth'].copy()
    if depth.dtype!=np.float32:raise ValueError('Exact float32 metre source required; no unreported precision/unit conversion')
    stream=BytesIO();Image.fromarray(depth).save(stream,format='TIFF',compression='tiff_adobe_deflate')
    derived=stream.getvalue()
    with Image.open(BytesIO(derived)) as image:restored=np.asarray(image)
    if restored.shape!=depth.shape or restored.dtype!=depth.dtype or not np.array_equal(restored,depth,equal_nan=True):
        raise ValueError('Lossless float32 depth view roundtrip differs')
    return derived


class NativeFeatureViews:
    """Bounded writer-side content cache, never inside a servo publication."""
    def __init__(self,*,max_frames=8,max_bytes=16*1024**2):
        self.frames,self.bytes=max_frames,max_bytes;self.cache=OrderedDict();self.lock=threading.RLock();self.encodes=0
    def _depth(self,raw,width,height):
        key=(sha(raw),width,height)
        with self.lock:
            if key in self.cache:self.cache.move_to_end(key);return self.cache[key]
            encoded=depth_tiff(raw,width,height);self.encodes+=1
            self.cache[key]=encoded
            while len(self.cache)>self.frames or sum(len(value) for value in self.cache.values())>self.bytes:
                self.cache.popitem(last=False)
            return encoded
    def convert(self,observation):
        value=feature_views(observation.get('channels') or {},observation.get('source_payloads') or {},depth_encoder=self._depth)
        result=deepcopy({key:item for key,item in observation.items() if key!='source_payloads'})
        from .contracts import canonical
        metadata=canonical({'schema':'explorer_native_model_format_view_v1','original_channels':value['original_channels'],
            'format_views':value['format_views'],'unsupported_feature_formats':value['unsupported_feature_formats'],
            'new_measurements_created':False})
        name='sources/model-format-view-'+sha(metadata)+'.json'
        result.update(channels=value['channels'],source_payloads={**value['source_payloads'],name:metadata})
        result.setdefault('diagnostics',{})['model_format_view']={'source_file':name,'sha256':sha(metadata),
            'views':value['format_views'],'unsupported':value['unsupported_feature_formats']}
        return result


def sha(value):return hashlib.sha256(value).hexdigest()


def feature_views(channels,source_payloads,*,depth_encoder=depth_tiff):
    output=deepcopy(channels);payloads=dict(source_payloads);views={};unsupported={}
    original=output.get('camera_depth') or {};value=original.get('value') or {}
    if original.get('available') is True and value.get('encoding')=='numpy_npz_depth_m':
        try:
            name=value.get('source_file');raw=payloads.get(name)
            if not isinstance(raw,bytes) or sha(raw)!=value.get('sha256'):raise ValueError('Original depth asset SHA differs')
            width,height=value.get('width'),value.get('height')
            if type(width) is not int or type(height) is not int or not 0<width*height<=1920*1080 or value.get('units')!='m':
                raise ValueError('Original registered metric depth shape/units required')
            derived=depth_encoder(raw,width,height)
            target='sensors/model-view-depth-'+sha(derived)+'.tiff';payloads[target]=derived
            source_record=deepcopy(original)
            output['camera_depth']['value'].update(source_file=target,sha256=sha(derived),encoding='tiff_float32_m',
                model_view={'method':'lossless_float32_metre_TIFF_no_resampling','original_source_file':name,
                    'original_sha256':sha(raw),'original_record':source_record,'added_measurement':False})
            views['camera_depth']={'source_file':target,'sha256':sha(derived),'raw_source_file':name,'raw_sha256':sha(raw),
                'same_shape_units_and_values':True,'invalid_values_preserved':True}
        except (ValueError,TypeError,KeyError,OSError,EOFError) as error:
            unsupported['camera_depth']=type(error).__name__+':'+str(error)
    original=output.get('lidar') or {};value=original.get('value') or {}
    if original.get('available') is True and original.get('mask') is not True:
        ranges=value.get('ranges');valid=value.get('range_valid_mask')
        if (isinstance(ranges,list) and isinstance(valid,list) and len(ranges)==len(valid) and ranges and
                all(type(flag) is bool for flag in valid) and any(valid) and all(
                    (type(distance) in (int,float) and math.isfinite(distance) and distance>0) if flag else distance is None
                    for distance,flag in zip(ranges,valid))):
            # The frozen encoder filters each invalid ray, then marks only
            # populated angular buckets as valid. Channel availability is not a
            # claim that null beams are measurements or observed free space.
            output['lidar']['mask']=True
            output['lidar']['value']['model_view']={'method':'existing_finite_ray_angular_bucket_encoder',
                'original_channel_mask':deepcopy(original['mask']),'per_ray_validity_retained':True,
                'invalid_rays_filled':False,'bucket_mask_computed_from_actual_valid_rays':True}
            views['lidar']={'invalid_rays_retained_as_null':True,'per_ray_valid_mask_retained':True,
                'model_channel_mask_scope':'structure_available_for_existing_per_ray_filter_not_all_rays_valid'}
        else:unsupported['lidar']='PER_RAY_SOURCE_VALIDITY_UNAVAILABLE_OR_INCONSISTENT'
    return {'channels':output,'source_payloads':payloads,'format_views':views,'unsupported_feature_formats':unsupported,
            'original_channels':deepcopy(channels),'new_measurements_created':False}
