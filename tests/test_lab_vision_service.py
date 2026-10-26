"""Cached original images and masks only; no camera/NPU/serial/motor opened."""
from copy import deepcopy
import hashlib
import io
import json
from types import SimpleNamespace

import numpy as np
from PIL import Image
import pytest

from explorer_shared.hailo_segmentation import encode_mask_rle
from explorer_shared.lab_vision_service import LabVisionService


def rig(tmp_path):
    stream=io.BytesIO();Image.new('RGB',(32,24),(60,90,120)).save(stream,format='JPEG')
    original=SimpleNamespace(frame_id='software-frame',jpeg=stream.getvalue(),width=32,height=24,
        monotonic_ns=1_000_000_000,timestamp_basis='host_receive_estimate')
    mask=np.zeros((24,32),bool);mask[2:9,3:7]=True;mask[7:12,3:12]=True
    observation=dict(frame_id=original.frame_id,frame_sha256=hashlib.sha256(original.jpeg).hexdigest(),
        camera_id='pi-overview',model_sha256='a'*64,track_session_id='software-tracks',available=True,
        model={'task':'instance_segmentation'},detections=[dict(track_id=7,label='software-object',score=.8,
            box=[3/32,2/24,12/32,12/24],mask_available=True,mask_rle=encode_mask_rle(mask))])
    source=SimpleNamespace(camera_id='pi-overview',latest=lambda:original,metadata=lambda:{'available':True})
    class Observer:
        pair=(original,observation)
        def snapshot(self):return deepcopy(observation)
        def cached_observation_pair(self,identity):return self.pair if self.pair and identity==original.frame_id else None
    observer=Observer();events=[]
    service=LabVisionService(source,observer,clock_domain='pi:software-boot',boot_id='software-boot',
        config_path=tmp_path/'absent.json',emit=events.append,clock=lambda:1_500_000_000)
    body=dict(frame_id=original.frame_id,frame_sha256=observation['frame_sha256'],pixel=[4,4],purpose='object')
    return service,observer,source,body,events


def test_exact_frozen_mask_selection_has_no_coordinates_or_execution_without_real_calibration(tmp_path):
    service,observer,source,body,events=rig(tmp_path)
    selected=service.select(body)
    assert selected['track_id']==7 and selected['coordinate_confirmed'] is False
    assert selected['physical_execution_allowed'] is False and 'calibration' in selected['grounding_blocker']
    assert service.snapshot()['native_execution_enabled'] is False
    assert events[0]['selection']['frame_sha256']==hashlib.sha256(source.latest().jpeg).hexdigest()
    with pytest.raises(ValueError,match='calibration'):service.grid()
    with pytest.raises(ValueError,match='execution'):service.command('execute',{})


def test_frozen_pair_cannot_silently_change_to_latest_frame_and_survives_observer_eviction(tmp_path):
    service,observer,source,body,_=rig(tmp_path)
    frame,observation=service.frozen();observer.pair=None
    same,copied=service.frozen(frame.frame_id)
    assert same.jpeg==frame.jpeg and copied==observation
    with pytest.raises(ValueError,match='evicted'):service.frozen('new-unknown-frame')
    with pytest.raises(ValueError,match='SHA'):service.select(dict(body,frame_sha256='b'*64))


def test_empty_cell_is_only_placement_not_an_invented_grasp_and_bad_masks_block(tmp_path):
    service,observer,source,body,_=rig(tmp_path)
    empty=dict(body,pixel=[25,20])
    with pytest.raises(ValueError,match='exactly one'):service.select(empty)
    placement=service.select(dict(empty,purpose='placement_cell'))
    assert placement['track_id'] is None and 'NOT_PROOF_OF_EMPTY' in placement['occupancy']
    service.frozen_pairs.clear();observer.pair[1]['detections'][0]['mask_available']=False
    with pytest.raises(ValueError,match='unavailable'):service.select(body)


def test_explicit_semantic_event_uses_frozen_original_and_never_dispatches_actions(tmp_path):
    service,observer,source,body,_=rig(tmp_path);selected=service.select(body)
    submitted=[]
    class Worker:
        def submit(self,*args):submitted.append(args);return 42
        def snapshot(self):return {'state':'QUEUED','receipts':[]}
        def cancel(self):pass
    service.worker=Worker();observer.pair=None
    request=service.semantic({'selection_id':selected['selection_id'],'trigger':'user_selection','prompt':'Describe only.'})
    assert request['request_id']==42 and request['physical_actions_emitted'] is False
    assert submitted[0][0].jpeg==source.latest().jpeg
    assert submitted[0][1]['mask_sha256']==selected['mask_sha256']
    with pytest.raises(ValueError,match='named explicit'):service.semantic({'trigger':'servo_tick'})
    assert service.snapshot()['physical_validation_passed'] is False


def test_bad_optional_configuration_preserves_camera_and_detector_status(tmp_path):
    service,observer,source,body,_=rig(tmp_path)
    path=tmp_path/'bad.json';path.write_text('[]')
    bad=LabVisionService(source,observer,clock_domain='pi:software-boot',boot_id='software-boot',config_path=path)
    status=bad.snapshot()
    assert status['camera']['available'] and status['perception']['available']
    assert status['configuration_error'] and status['vlm']['worker']['state']=='NOT_CONFIGURED'


def test_status_uses_the_actual_shared_camera_source_metadata_interface(tmp_path):
    from explorer_shared.node_agent import CameraSource
    camera=CameraSource('pi-overview','Actual cached camera interface')
    stream=io.BytesIO();Image.new('RGB',(32,24)).save(stream,format='JPEG')
    camera.publish(stream.getvalue(),32,24)
    service=LabVisionService(camera,None,clock_domain='pi:software',boot_id='software',config_path=tmp_path/'absent.json')
    assert service.snapshot()['camera']['available'] is True
    assert service.snapshot()['vlm']['worker']['state']=='NOT_CONFIGURED'
