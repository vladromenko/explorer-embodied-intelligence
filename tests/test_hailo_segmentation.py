"""Recorded-shaped tensor software fixtures only. No Hailo/camera/servo device."""
import ast
import copy
import hashlib
import json
from pathlib import Path
import threading
import time
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from explorer_shared.hailo_observer import HailoObserver
from explorer_shared.hailo_segmentation import (
    HEAD_SHAPES,SegmentationLetterbox,decode_yolov5_segmentation,encode_mask_rle,decode_mask_rle,
)
from explorer_shared._vendor.hailo_yolov5_seg import cnms

ROOT=Path(__file__).resolve().parents[1]
VENDOR=ROOT/'shared/explorer_shared/_vendor/hailo_yolov5_seg'


def tensor_fixture():
    raw={str(shape):np.full((1,*shape),-20,dtype=np.float32) for shape in HEAD_SHAPES}
    raw[str(HEAD_SHAPES[0])].fill(0)
    row=raw[str(HEAD_SHAPES[1])][0,12,10,:117]
    row[:4]=0;row[4]=15;row[5+39]=15;row[85]=10
    raw[str(HEAD_SHAPES[0])][0,:,:80,0]=-2
    raw[str(HEAD_SHAPES[0])][0,:,80:,0]=2
    return raw


def test_pinned_official_source_license_hashes_and_nonempty_raw_head_decode():
    manifest=json.loads((VENDOR/'source-manifest.json').read_text())
    assert manifest['commit']=='7b850e0444a142ada88443d675e3c3ddda18bd74' and manifest['license']=='MIT'
    for name,digest in manifest['sources'].items():assert hashlib.sha256((VENDOR/name).read_bytes()).hexdigest()==digest
    assert 'Copyright (c) 2023 Hailo' in (VENDOR/'LICENSE').read_text()
    raw=tensor_fixture();unchanged={name:array.copy() for name,array in raw.items()}
    detections=decode_yolov5_segmentation(raw,SegmentationLetterbox(640,480))
    assert len(detections)==1 and detections[0]['label']=='bottle' and detections[0]['class_id']==39
    assert detections[0]['score']>.99 and detections[0]['mask_available']
    np.testing.assert_allclose(detections[0]['box'],[278/640,275/480,394/640,365/480],atol=1e-6)
    mask=decode_mask_rle(detections[0]['mask_rle'])
    assert mask.dtype==np.bool_ and mask.shape==(480,640) and mask.sum()==6660
    assert mask[300,350] and not mask[300,290]  # Inside box but outside the prototype mask.
    assert not mask[:275].any() and not mask[365:].any()
    assert all(np.array_equal(raw[name],array) for name,array in unchanged.items())


def test_resize_exactly_matches_pinned_official_rgb_preprocess_for_odd_aspect_ratio():
    # Compile only the primary-source preprocessing function, with no camera imports.
    # Self-contained copy of that exact function is retained beside source hashes.
    primary=VENDOR/'upstream-default-preprocess.py.txt'
    scope={'cv2':cv2,'np':np};exec(compile(primary.read_text(),str(primary),'exec'),scope)
    image=np.arange(479*641*3,dtype=np.uint8).reshape((479,641,3))
    actual=SegmentationLetterbox(641,479).preprocess_bgr(image)
    expected=scope['default_preprocess'](cv2.cvtColor(image,cv2.COLOR_BGR2RGB),640,640)
    assert np.array_equal(actual,expected) and actual.shape==(640,640,3)
    assert SegmentationLetterbox(641,479).resized==(640,478)


def test_numpy_NMS_preserves_official_inclusive_pixel_iou_ties_and_original_index_order():
    same=np.asarray([[0,0,9,9,.9],[5,0,14,9,.8]],np.float32)
    assert cnms(same,np.float32(1/3)).tolist()==[0]
    assert cnms(same,np.float32(.34)).tolist()==[0,1]
    tied=np.asarray([[0,0,9,9,1],[0,0,9,9,1],[50,50,60,60,1]],np.float32)
    assert cnms(tied,.5).tolist()==[1,2]
    assert cnms(np.empty((0,5),np.float32),.5).tolist()==[]


@pytest.mark.parametrize('fault',['quantized','nonfinite','missing','duplicate','wrong_shape'])
def test_wrong_raw_output_contract_is_never_guessed_or_promoted_to_masks(fault):
    raw=tensor_fixture();key=str(HEAD_SHAPES[2])
    if fault=='quantized':raw[key]=raw[key].astype(np.int16)
    elif fault=='nonfinite':raw[key][0,0,0,0]=np.nan
    elif fault=='missing':del raw[key]
    elif fault=='duplicate':raw[key]=raw[str(HEAD_SHAPES[1])].copy()
    else:raw[key]=np.zeros((1,10,10,351),np.float32)
    with pytest.raises(ValueError):decode_yolov5_segmentation(raw,SegmentationLetterbox(640,480))


def test_full_resolution_RLE_is_bounded_and_hash_verified_without_bbox_substitution():
    mask=np.zeros((9,11),bool);mask[1:5,3:7]=True;mask[2,4]=False
    encoded=encode_mask_rle(mask);assert np.array_equal(decode_mask_rle(encoded),mask)
    assert encode_mask_rle(np.ones((2,3),bool))['counts']==[0,6]
    damaged=copy.deepcopy(encoded);damaged['mask_sha256']='0'*64
    with pytest.raises(ValueError,match='SHA'):decode_mask_rle(damaged)
    damaged=copy.deepcopy(encoded);damaged['counts']=[10**12]
    with pytest.raises(ValueError,match='bounded'):decode_mask_rle(damaged)
    with pytest.raises(ValueError,match='RLE exceeds'):encode_mask_rle(np.indices((30,30)).sum(axis=0)%2==0,max_runs=20)
    with pytest.raises(ValueError):decode_mask_rle({'encoding':'box_fill','shape':[9,11]})


def eventually(check,timeout=4):
    deadline=time.monotonic()+timeout
    while not check() and time.monotonic()<deadline:threading.Event().wait(.005)
    assert check()


def test_observer_exact_mask_frame_cache_keeps_last_eight_originals_and_never_falls_back_to_latest():
    class Source:
        camera_id='pi-overview'
        def __init__(self):self.frame=None
        def latest(self):return self.frame
    class Detector:
        model_info={'sha256':'a'*64,'task':'instance_segmentation'}
        def __init__(self,path):pass
        def detect(self,image):return [{'class_id':39,'label':'bottle','box':[.1,.2,.3,.4],'score':.9,
            'mask_rle':encode_mask_rle(np.ones((2,3),bool))}],1.
        def close(self):pass
    source=Source();observer=HailoObserver(source,'no-model-or-device',lambda _:None,detector_factory=Detector,inference_hz=5.)
    frames=[]
    try:
        observer.start()
        for index in range(10):
            frame=SimpleNamespace(frame_id='exact-'+str(index),jpeg=('JPEG-'+str(index)).encode(),bgr=np.zeros((2,3,3),np.uint8),
                wall_utc='software-fixture',monotonic_ns=time.monotonic_ns(),timestamp_basis='software_receive')
            frames.append(frame);source.frame=frame;observer._wake.set()
            eventually(lambda:observer.snapshot().get('frame_id')==frame.frame_id)
        snapshot=observer.snapshot();assert snapshot['frame_sha256']==hashlib.sha256(frames[-1].jpeg).hexdigest()
        assert snapshot['model_sha256']=='a'*64 and snapshot['track_session_id']
        assert observer.cached_observation_frame('exact-0') is None and observer.cached_observation_frame('exact-1') is None
        assert observer.cached_observation_frame('exact-2') is frames[2] and observer.cached_observation_frame('exact-9') is frames[9]
        assert observer.cached_observation_frame('unknown-preview-latest') is None
        pair=observer.cached_observation_pair('exact-2')
        assert pair[0] is frames[2] and pair[1]['frame_id']=='exact-2'
        assert pair[1]['frame_sha256']==hashlib.sha256(frames[2].jpeg).hexdigest()
        assert observer.cached_observation_pair('exact-0') is None and observer.cached_observation('exact-0') is None
        pair[1]['detections'].clear()
        assert observer.cached_observation('exact-2')['detections']
        # A faster preview may advance without replacing the original selected mask frame.
        source.frame=SimpleNamespace(frame_id='uncached-preview-newer',bgr=None)
        assert observer.cached_observation_frame('exact-9') is frames[9]
        snapshot['detections'][0]['mask_rle']['counts'][0]=99
        assert observer.snapshot()['detections'][0]['mask_rle']['counts'][0]==0
    finally:observer.close()


def test_factory_preserves_legacy_detector_and_only_selects_segmentation_for_pinned_artifact(tmp_path,monkeypatch):
    import explorer_shared.hailo_observer as observer
    import explorer_shared.hailo_segmentation as segmentation
    known=tmp_path/'known.hef';known.write_bytes(b'software-model-fixture')
    other=tmp_path/'other.hef';other.write_bytes(b'other-model')
    monkeypatch.setattr(segmentation,'MODEL_SHA256',hashlib.sha256(known.read_bytes()).hexdigest())
    calls=[]
    monkeypatch.setattr(segmentation,'HailoSegmentationDetector',lambda path,threshold: calls.append(('seg',Path(path),threshold)) or 'segmentation-mock')
    monkeypatch.setattr(observer,'HailoDetector',lambda path,threshold: calls.append(('nms',Path(path),threshold)) or 'legacy-nms-mock')
    assert observer.create_hailo_detector(known)=='segmentation-mock'
    assert observer.create_hailo_detector(other)=='legacy-nms-mock'
    assert [item[0] for item in calls]==['seg','nms']


def fake_hailo(raw):
    class Context:
        def __init__(self,name):self.name=name
        def __enter__(self):owners.append(self.name);return self
        def __exit__(self,*_):owners.remove(self.name)
    class Stream:
        def __init__(self,name,shape):self.name=name;self.shape=shape;self.format=SimpleNamespace(order='NHWC');self.types=[]
        def set_format_type(self,value):self.types.append(value)
    class Buffer:
        def __init__(self,value):self.value=value
        def get_buffer(self):return self.value
        def set_buffer(self,value):self.value=value;inputs.append(value.copy())
    class Bindings:
        def input(self):return input_buffer
        def output(self,name):return Buffer(raw[name])
    class Configured(Context):
        def create_bindings(self,output_buffers):
            assert all(value.dtype==np.float32 for value in output_buffers.values());return Bindings()
        def run(self,bindings,timeout):assert owners==['device','configured'] and timeout==2000
    class Model:
        def __init__(self):self.inputs=[Stream('input',(640,640,3))];self.outputs=[Stream(name,tuple(value.shape[-3:])) for name,value in raw.items()]
        def input(self):return self.inputs[0]
        def set_batch_size(self,value):assert value==1
        def configure(self):return Configured('configured')
    class Device(Context):
        @staticmethod
        def create_params():return SimpleNamespace(scheduling_algorithm=None)
        def __init__(self,params):super().__init__('device')
        def create_infer_model(self,path):return model
    owners=[];inputs=[];input_buffer=Buffer(None);model=Model()
    api=SimpleNamespace(__version__='5.1.1',VDevice=Device,HailoSchedulingAlgorithm=SimpleNamespace(ROUND_ROBIN='round_robin'),
        FormatType=SimpleNamespace(UINT8='UINT8',FLOAT32='FLOAT32'),FormatOrder=SimpleNamespace(NHWC='NHWC'))
    return api,model,owners,inputs


def test_HailoRT5_adapter_contract_float_dequantized_heads_and_cleanup_using_strict_memory_fake(tmp_path,monkeypatch):
    import sys
    import explorer_shared.hailo_segmentation as module
    path=tmp_path/'pinned.hef';path.write_bytes(b'FAKE-HEF-not-loaded-on-device')
    monkeypatch.setattr(module,'MODEL_SHA256',hashlib.sha256(path.read_bytes()).hexdigest())
    api,model,owners,inputs=fake_hailo(tensor_fixture())
    monkeypatch.setitem(sys.modules,'hailo_platform',api)
    monkeypatch.setattr(module.subprocess,'run',lambda *args,**kwargs:SimpleNamespace(stdout='HEF Compatible for: HAILO10H'))
    detector=module.HailoSegmentationDetector(path)
    try:
        assert owners==['device','configured']
        assert model.input().types==['UINT8'] and all(output.types==['FLOAT32'] for output in model.outputs)
        result,elapsed=detector.detect(np.zeros((480,640,3),np.uint8))
        assert result[0]['mask_available'] and elapsed>=0
        assert inputs[0].shape==(640,640,3) and inputs[0].dtype==np.uint8
        assert detector.model_info['runtime_version']=='5.1.1' and detector.model_info['task']=='instance_segmentation'
    finally:detector.close()
    assert owners==[]


def test_partial_context_failure_and_unqualified_runtime_never_leak_mock_accelerator_owner(tmp_path,monkeypatch):
    import sys
    import explorer_shared.hailo_segmentation as module
    path=tmp_path/'pinned.hef';path.write_bytes(b'FAKE-HEF')
    monkeypatch.setattr(module,'MODEL_SHA256',hashlib.sha256(path.read_bytes()).hexdigest())
    api,model,owners,_=fake_hailo(tensor_fixture());model.outputs[0].shape=(1,2,3)
    monkeypatch.setitem(sys.modules,'hailo_platform',api)
    monkeypatch.setattr(module.subprocess,'run',lambda *args,**kwargs:SimpleNamespace(stdout='HAILO10H'))
    with pytest.raises(ValueError,match='shape/order'):module.HailoSegmentationDetector(path)
    assert owners==[]
    api.__version__='5.3.0'
    with pytest.raises(ValueError,match='5.1.1'):module.HailoSegmentationDetector(path)
    assert owners==[]


def test_valid_empty_raw_scene_returns_no_instances_and_never_box_masks():
    raw=tensor_fixture()
    for shape in HEAD_SHAPES[1:]:raw[str(shape)].fill(-20)
    assert decode_yolov5_segmentation(raw,SegmentationLetterbox(640,480))==[]
