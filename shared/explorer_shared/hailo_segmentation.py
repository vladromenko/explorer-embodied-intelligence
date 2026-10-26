"""Pinned official YOLOv5 segmentation decoder plus HailoRT5 single-owner adapter.

Import never opens a camera, VDevice, serial port or robot. Receives cached BGR
from the existing observer. MIT upstream subset lives in _vendor/hailo_yolov5_seg.
"""
from __future__ import annotations
from contextlib import ExitStack
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import subprocess
import time

MODEL_SHA256='edb7f33aa7212a24dca0090cfca9b83ca72cc81b7385a3225ca2cd465092b9ed'
UPSTREAM_COMMIT='7b850e0444a142ada88443d675e3c3ddda18bd74'
HEAD_SHAPES=((160,160,32),(20,20,351),(40,40,351),(80,80,351))
MAX_PIXELS=1920*1080
MAX_RLE_RUNS=100000
MAX_DETECTIONS=20


def encode_mask_rle(mask,*,max_runs=MAX_RLE_RUNS):
    import numpy as np
    mask=np.asarray(mask)
    if mask.dtype!=np.bool_ or mask.ndim!=2 or not mask.size or mask.size>MAX_PIXELS:raise ValueError('Bounded full-resolution Boolean mask required')
    flat=mask.ravel(order='C');changes=np.flatnonzero(flat[1:]!=flat[:-1])+1
    bounds=np.concatenate(([0],changes,[len(flat)]));counts=np.diff(bounds).tolist()
    if bool(flat[0]):counts.insert(0,0)
    if len(counts)>max_runs:raise ValueError('Mask RLE exceeds bounded source-resolution encoding')
    return {'encoding':'row_major_runs_v1','shape':list(mask.shape),'starts_with':0,'counts':counts,
        'mask_sha256':hashlib.sha256(np.packbits(flat).tobytes()).hexdigest()}


def decode_mask_rle(encoded):
    import numpy as np
    if not isinstance(encoded,dict) or encoded.get('encoding')!='row_major_runs_v1' or encoded.get('starts_with')!=0:raise ValueError('Unsupported mask RLE contract')
    shape=encoded.get('shape');counts=encoded.get('counts')
    if not isinstance(shape,list) or len(shape)!=2 or any(type(value) is not int or value<1 for value in shape) or shape[0]*shape[1]>MAX_PIXELS:raise ValueError('Invalid bounded mask shape')
    if not isinstance(counts,list) or not counts or len(counts)>MAX_RLE_RUNS or any(type(value) is not int or value<0 for value in counts) or sum(counts)!=shape[0]*shape[1]:raise ValueError('Invalid bounded mask run lengths')
    mask=np.repeat(np.arange(len(counts))%2,np.asarray(counts,dtype=np.int64)).astype(bool).reshape(shape)
    digest=hashlib.sha256(np.packbits(mask.ravel()).tobytes()).hexdigest()
    if encoded.get('mask_sha256')!=digest:raise ValueError('Decoded source mask SHA mismatch')
    return mask


@dataclass(frozen=True)
class SegmentationLetterbox:
    width:int
    height:int
    target:int=640
    def __post_init__(self):
        if type(self.width) is not int or type(self.height) is not int or min(self.width,self.height)<1 or self.width*self.height>MAX_PIXELS or self.target!=640:raise ValueError('Supported bounded source dimensions and pinned640 model required')
    @property
    def resized(self):
        scale=min(self.target/self.width,self.target/self.height)
        width,height=int(self.width*scale),int(self.height*scale)
        if min(width,height)<1:raise ValueError('Source aspect ratio is unsupported by the pinned resize')
        return width,height
    @property
    def padding(self):
        width,height=self.resized
        return (self.target-width)//2,(self.target-height)//2
    def preprocess_bgr(self,image):
        import cv2
        import numpy as np
        if image.dtype!=np.uint8 or image.shape!=(self.height,self.width,3):raise ValueError('Cached source must be uint8 BGR matching geometry')
        rgb=cv2.cvtColor(image,cv2.COLOR_BGR2RGB)
        resized=cv2.resize(rgb,self.resized,interpolation=cv2.INTER_CUBIC)
        padded=np.full((self.target,self.target,3),114,dtype=np.uint8)
        left,top=self.padding;width,height=self.resized
        padded[top:top+height,left:left+width]=resized
        return padded
    def restore_box_xyxy(self,box):
        import numpy as np
        width,height=self.resized;left,top=self.padding
        x1,y1,x2,y2=[float(value)*self.target for value in box]
        return np.clip([(x1-left)/width,(y1-top)/height,(x2-left)/width,(y2-top)/height],0,1).tolist()
    def restore_mask(self,mask,threshold):
        import cv2
        import numpy as np
        if mask.shape!=(self.target,self.target) or not np.isfinite(mask).all():raise ValueError('Pinned decoder must return finite640 mask probabilities')
        width,height=self.resized;left,top=self.padding
        unpadded=mask[top:top+height,left:left+width]
        return cv2.resize(unpadded,(self.width,self.height),interpolation=cv2.INTER_LINEAR)>threshold


def decode_yolov5_segmentation(raw_outputs,geometry,*,score_threshold=.35,mask_threshold=.4,max_detections=MAX_DETECTIONS):
    """Dequantized Hailo FLOAT32 four-head tensors; official v5 decoder arithmetic."""
    import numpy as np
    from ._vendor.hailo_yolov5_seg import yolov5_seg_postprocess
    from .hailo_observer import COCO_CLASSES
    if not 0<score_threshold<1 or not 0<mask_threshold<1 or type(max_detections) is not int or not 1<=max_detections<=MAX_DETECTIONS:raise ValueError('Explicit bounded segmentation thresholds required')
    if not isinstance(raw_outputs,dict) or len(raw_outputs)!=4:raise ValueError('Exactly four raw heads required; NMS detection output is not an instance mask')
    heads={}
    for name,raw in raw_outputs.items():
        tensor=np.asarray(raw)
        if tensor.ndim==3:tensor=tensor[None]
        shape=tuple(tensor.shape[1:])
        if tensor.shape[0]!=1 or shape not in HEAD_SHAPES or shape in heads or tensor.dtype!=np.float32 or not np.isfinite(tensor).all():raise ValueError('Wrong, duplicate, quantized or nonfinite raw segmentation head')
        heads[shape]=tensor
    config=json.loads((Path(__file__).parent/'_vendor/hailo_yolov5_seg/upstream-config.json').read_text())['v5']
    config.update(score_threshold=score_threshold,max_detections=max_detections,multi_label=False)
    outputs=yolov5_seg_postprocess([heads[shape] for shape in HEAD_SHAPES],**config)[0]
    result=[];runs=0
    for box,score,cls,probability in zip(outputs['detection_boxes'],outputs['detection_scores'],outputs['detection_classes'],outputs['mask']):
        cls=int(cls);score=float(score)
        if cls<0 or cls>=80 or not np.isfinite(score) or not 0<=score<=1:raise ValueError('Invalid decoded instance class/confidence')
        restored=geometry.restore_box_xyxy(box)
        if restored[2]>restored[0] and restored[3]>restored[1]:
            mask=geometry.restore_mask(probability,mask_threshold)
            item={'class_id':cls,'label':COCO_CLASSES[cls],'score':score,'box':restored,
                'mask_rle':None,'mask_available':False,'mask_source':'official_yolov5_proto_coefficients',
                'mask_shape':[geometry.height,geometry.width],
                'mask_sha256':hashlib.sha256(np.packbits(mask.ravel()).tobytes()).hexdigest()}
            if mask.any():
                try:
                    rle=encode_mask_rle(mask)
                    if runs+len(rle['counts'])>200000:raise ValueError('Total encoded mask observation budget exceeded')
                    runs+=len(rle['counts']);item.update(mask_rle=rle,mask_available=True)
                except ValueError:
                    item['mask_unavailable_reason']='mask_encoding_budget_exceeded'
            else:item['mask_unavailable_reason']='no_pixels_above_source_mask_threshold'
            result.append(item)
    return result


class HailoSegmentationDetector:
    def __init__(self,path,threshold=.35):
        import hailo_platform as hailo
        import numpy as np
        if getattr(hailo,'__version__',None)!='5.1.1':raise ValueError('This decoder/runtime qualification is pinned to HailoRT5.1.1')
        path=Path(path)
        with path.open('rb') as stream:digest=hashlib.file_digest(stream,'sha256').hexdigest()
        if digest!=MODEL_SHA256:raise ValueError('Only the pinned H10 YOLOv5n segmentation artifact is qualified by this adapter')
        parsed=subprocess.run(['hailortcli','parse-hef',str(path)],check=True,capture_output=True,text=True,timeout=10)
        if 'HAILO10H' not in parsed.stdout:raise ValueError('Pinned model must be HAILO10H compatible')
        self.threshold=threshold;self._resources=ExitStack()
        try:
            params=hailo.VDevice.create_params();params.scheduling_algorithm=hailo.HailoSchedulingAlgorithm.ROUND_ROBIN
            self.device=self._resources.enter_context(hailo.VDevice(params));self.model=self.device.create_infer_model(str(path));self.model.set_batch_size(1)
            if len(self.model.inputs)!=1 or tuple(self.model.input().shape)!=(640,640,3) or len(self.model.outputs)!=4:raise ValueError('Pinned segmentation HEF shape contract differs')
            self.model.input().set_format_type(hailo.FormatType.UINT8)
            outputs={};shapes=[]
            for output in self.model.outputs:
                shape=tuple(output.shape);shapes.append(shape)
                if shape not in HEAD_SHAPES or output.format.order!=hailo.FormatOrder.NHWC:raise ValueError('Unexpected segmentation raw head shape/order')
                output.set_format_type(hailo.FormatType.FLOAT32);outputs[output.name]=np.empty(output.shape,dtype=np.float32)
            if set(shapes)!=set(HEAD_SHAPES):raise ValueError('Duplicate/missing pinned segmentation head')
            self.configured=self._resources.enter_context(self.model.configure());self.bindings=self.configured.create_bindings(output_buffers=outputs)
            self.output_names=list(outputs)
        except Exception:
            self._resources.close();raise
        source=json.loads((Path(__file__).parent/'_vendor/hailo_yolov5_seg/source-manifest.json').read_text())
        self.model_info={'name':path.name,'sha256':digest,'architecture':'HAILO10H','runtime_api':'HailoRT5 InferModel',
            'runtime_version':hailo.__version__,'task':'instance_segmentation','host_decoder_upstream_commit':UPSTREAM_COMMIT,'host_decoder_sources':source['sources'],
            'raw_head_shapes':[list(shape) for shape in HEAD_SHAPES],'dequantized_host_output':'FLOAT32',
            'score_threshold':threshold,'mask_threshold':.4,'max_detections':MAX_DETECTIONS,'multi_label':False,
            'source_resolution_masks':True,'physical_contact_verified':False}
    def detect(self,frame):
        geometry=SegmentationLetterbox(int(frame.shape[1]),int(frame.shape[0]))
        self.bindings.input().set_buffer(geometry.preprocess_bgr(frame));begin=time.monotonic()
        self.configured.run([self.bindings],2000)
        raw={name:self.bindings.output(name).get_buffer() for name in self.output_names}
        detections=decode_yolov5_segmentation(raw,geometry,score_threshold=self.threshold)
        return detections,(time.monotonic()-begin)*1000
    def close(self):self._resources.close()
