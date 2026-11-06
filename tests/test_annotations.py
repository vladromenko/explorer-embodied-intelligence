"""Technical fixtures are NOT real SAM inference or hardware verification."""
import copy
import hashlib
import json
from pathlib import Path

import numpy as np
from PIL import Image
import pytest

from so101_lab.annotations import (AnnotationError, CausalAssociator, annotate_dataset,
    annotate_observation, load_frame_annotations, measure_color, sha256_file,
    source_frame_digest, validate_frame_annotations)
from so101_lab.geometry import check_applicability, estimate_point_on_plane, forward_kinematics


class TechnicalSegmenter:
    backend = 'technical_test'
    provenance = {'backend':'technical_test','warning':'Fixture mask, not SAM'}
    def __init__(self):
        self.calls = 0
    def segment(self,rgb):
        self.calls += 1
        mask = rgb[:,:,0] > 200
        return [{'category':'cube','score':.9,'bbox_xyxy':[2.,3.,8.,9.], 'mask':mask}]


def make_dataset(root,frames=3):
    root.mkdir()
    (root/'dataset.json').write_text(json.dumps({'source':'synthetic_test','task':'technical fixture'}))
    episode = root/'episodes'/'ep001'
    episode.mkdir(parents=True)
    (episode/'episode.json').write_text(json.dumps({'status':'complete'}))
    rows=[]
    for i in range(frames):
        raw={'frame_id':f'{i:08d}','timestamp_ns':1000+i*100,'observation_timestamp_ns':1000+i*100,
             'observation.state':[0]*6,'action':[0]*6,'cameras':{}}
        for camera in ('top','wrist'):
            p=episode/'images'/camera/f'{i:08d}.png'
            p.parent.mkdir(parents=True,exist_ok=True)
            rgb=np.zeros((12,16,3),dtype=np.uint8)
            rgb[3:9,2:8,0]=255
            Image.fromarray(rgb).save(p)
            raw['cameras'][camera]={'timestamp_ns':raw['timestamp_ns']-1,'path':p.relative_to(episode).as_posix(),'sha256':sha256_file(p),'valid':True}
        rows.append(raw)
    (episode/'frames.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in rows))
    return episode,rows


def test_roundtrip_lossless_exact_lineage_and_resume(tmp_path):
    dataset=tmp_path/'dataset'
    ep,rows=make_dataset(dataset)
    fake=TechnicalSegmenter()
    result=annotate_dataset(dataset,{'allow_test':True,'_test_segmenter':fake})
    assert result['status']=='complete' and fake.calls==6
    for raw in rows:
        ann=load_frame_annotations(ep,raw,allow_test=True)
        assert ann['cameras']['top']['objects'][0]['center_xy']==[4.5,5.5]
        mask=ep/ann['cameras']['top']['objects'][0]['mask_path']
        assert set(np.unique(Image.open(mask)))=={0,255}
        assert ann['cameras']['top']['objects'][0]['geometry']['valid'] is False
    with pytest.raises(AnnotationError,match='Технические'):
        load_frame_annotations(ep,rows[0])
    rerun=TechnicalSegmenter()
    annotate_dataset(dataset,{'allow_test':True,'_test_segmenter':rerun})
    assert rerun.calls==0


def test_resume_after_stop_and_torn_last_line(tmp_path):
    dataset=tmp_path/'dataset'; ep,rows=make_dataset(dataset)
    fake=TechnicalSegmenter()
    result=annotate_dataset(dataset,{'allow_test':True,'_test_segmenter':fake},stop=lambda:fake.calls>=2)
    assert result['status']=='stopped' and result['completed_frames']==1
    out=ep/'annotations'/'sam3-v1'/'frames.jsonl'
    with out.open('a') as f:
        f.write('{"torn":')
    rerun=TechnicalSegmenter()
    result=annotate_dataset(dataset,{'allow_test':True,'_test_segmenter':rerun})
    assert result['status']=='complete' and rerun.calls==4
    assert len(out.read_text().splitlines())==3


def test_temporal_hash_missing_and_mask_tamper_rejected(tmp_path):
    dataset=tmp_path/'dataset'; ep,rows=make_dataset(dataset)
    annotate_dataset(dataset,{'allow_test':True,'_test_segmenter':TechnicalSegmenter()})
    altered=copy.deepcopy(rows[0]); altered['cameras']['top']['timestamp_ns']+=1
    with pytest.raises(AnnotationError,match='Stale'):
        load_frame_annotations(ep,altered,allow_test=True)
    altered=copy.deepcopy(rows[0]); altered['frame_id']='missing'
    with pytest.raises(AnnotationError,match='Missing'):
        load_frame_annotations(ep,altered,allow_test=True)
    ann=load_frame_annotations(ep,rows[0],allow_test=True)
    mask=ep/ann['cameras']['top']['objects'][0]['mask_path']; mask.write_bytes(b'corrupt')
    with pytest.raises(AnnotationError,match='mask'):
        load_frame_annotations(ep,rows[0],allow_test=True)


def test_future_observation_and_label_leakage(tmp_path):
    dataset=tmp_path/'dataset';ep,rows=make_dataset(dataset,1)
    raw=rows[0]
    images={c:np.asarray(Image.open(ep/x['path'])) for c,x in raw['cameras'].items()}
    a=annotate_observation(raw,images,TechnicalSegmenter())
    poisoned=copy.deepcopy(raw);poisoned.update(action=[999]*6,success=True,done=True,phase='future')
    b=annotate_observation(poisoned,images,TechnicalSegmenter())
    assert source_frame_digest(poisoned)==source_frame_digest(raw)
    assert a['cameras']['top']['objects'][0]['center_xy']==b['cameras']['top']['objects'][0]['center_xy']
    assert not any(k in a for k in ('action','success','done','phase'))
    poisoned['cameras']['top']['timestamp_ns']=2000
    with pytest.raises(AnnotationError,match='будущего'):
        annotate_observation(poisoned,images,TechnicalSegmenter())


def test_tracking_is_causal_loses_id_and_reports_ambiguity():
    tracker=CausalAssociator('top')
    obj=lambda bbox:{'category':'cube','score':.9,'bbox_xyxy':bbox}
    first,_=tracker.update([obj([0,0,10,10])],100)
    same,_=tracker.update([obj([1,0,11,10])],200)
    assert same[0]['tracking']['id']==first[0]['tracking']['id']
    empty,lost=tracker.update([],300)
    assert lost==[same[0]['tracking']['id']]
    new,_=tracker.update([obj([1,0,11,10])],400)
    assert new[0]['tracking']['id']!=same[0]['tracking']['id']
    with pytest.raises(AnnotationError,match='Немонотонное'):
        tracker.update([],399)


def test_color_separate_from_sam_and_geometry_unknown():
    rgb=np.zeros((10,10,3),np.uint8);rgb[:,:,0]=255
    color=measure_color(rgb,np.ones((10,10),bool))
    assert color['label']=='red' and color['method'].startswith('visible_mask')
    assert measure_color(rgb,np.zeros((10,10),bool))['valid'] is False
    assert estimate_point_on_plane([1,1],None,{})['valid'] is False
    assert forward_kinematics([0]*6,None,timestamp_ns=1,joint_calibration_sha256=None)['valid'] is False


def calibration():
    return {'verified_current':True,'verification':{'by':'test','at':'2026-11-03','method':'synthetic_math_only'},
      'camera_serial':'test','resolution':[640,480], 'intrinsics':[[100,0,320],[0,100,240],[0,0,1]],
      'distortion':[0,0,0,0,0], 'camera_to_world':[[1,0,0,0],[0,1,0,0],[0,0,1,0],[0,0,0,1]],
      'mount_fingerprint':'fixture','joint_calibration_sha256':'fixture','position_sigma_m':.001,
      'planes':{'table':{'normal_world':[0,0,1],'offset_m':-1.,'sigma_m':.001,'measured':True,'provenance':'synthetic_math_only'}}}


def test_conditional_plane_geometry_requires_all_applicability_fields():
    cal=calibration(); camera={k:cal[k] for k in ('camera_serial','resolution','mount_fingerprint','joint_calibration_sha256')}
    estimate=estimate_point_on_plane([320,240],cal,camera)
    assert estimate['valid'] and np.allclose(estimate['xyz_m'],[0,0,1])
    assert estimate['depth_m'] is None and estimate['size_m'] is None
    assert estimate_point_on_plane([320,240],cal,camera,surface='cube_top')['valid'] is False
    camera['mount_fingerprint']='moved'
    assert not check_applicability(cal,camera)[0]
    assert estimate_point_on_plane([320,240],cal,camera)['valid'] is False


def test_test_source_cannot_enter_production_annotation(tmp_path):
    dataset=tmp_path/'dataset';make_dataset(dataset)
    with pytest.raises(AnnotationError,match='Test source'):
        annotate_dataset(dataset,{})


def test_reused_capture_and_resume_counter_do_not_invent_new_identity():
    tracker=CausalAssociator('top')
    obj=lambda:{'category':'cube','score':.9,'bbox_xyxy':[0,0,10,10]}
    a,_=tracker.update([obj()],100,'sha')
    b,_=tracker.update([obj()],100,'sha')
    assert b[0]['tracking']['id']==a[0]['tracking']['id']
    assert b[0]['tracking']['status']=='reused_capture'
    tracker.update([],200,'empty')
    restored=CausalAssociator('top')
    restored.restore({'objects':[],'timestamp_ns':200,'raw_sha256':'empty','tracking_counter':tracker.counter})
    c,_=restored.update([obj()],300,'next')
    assert c[0]['tracking']['id']!=a[0]['tracking']['id']


def test_option_change_reprocesses_resumed_sidecars(tmp_path):
    dataset=tmp_path/'dataset';ep,rows=make_dataset(dataset,2)
    fake=TechnicalSegmenter()
    annotate_dataset(dataset,{'allow_test':True,'_test_segmenter':fake})
    changed=TechnicalSegmenter()
    changed.provenance={'backend':'technical_test','revision':'different_fixture'}
    annotate_dataset(dataset,{'allow_test':True,'_test_segmenter':changed})
    assert changed.calls==4
    ann=load_frame_annotations(ep,rows[-1],allow_test=True)
    assert ann['provenance']['revision']=='different_fixture'


def test_busy_guard_runs_before_model_import_or_construction(monkeypatch):
    from so101_lab import resources
    from so101_lab.annotations import SAM3Segmenter
    seen=[]
    def busy(device,minimum_available_gb):
        seen.append((device,minimum_available_gb))
        raise resources.ResourceBusy('technical busy fixture')
    monkeypatch.setattr(resources,'assert_compute_available',busy)
    segmenter=SAM3Segmenter({'minimum_available_gb':0})
    with pytest.raises(resources.ResourceBusy,match='busy'):
        segmenter.load()
    assert seen==[('cuda',8.)] and segmenter.model is None


def test_progress_counts_are_durable_and_include_resumed_objects(tmp_path):
    dataset = tmp_path/'dataset'
    make_dataset(dataset)
    events = []
    first = annotate_dataset(dataset, {'allow_test': True, '_test_segmenter': TechnicalSegmenter()}, progress=events.append)
    assert first['completed_frames'] == 3 and first['completed_images'] == 6
    assert first['detected_objects'] == 6 and first['objects_by_category'] == {'cube': 6, 'box': 0}
    assert first['objects_by_camera'] == {'top': 3, 'wrist': 3}
    assert first['new_frames'] == 3 and first['resumed_frames'] == 0
    assert events[0]['completed_frames'] == 0
    again = annotate_dataset(dataset, {'allow_test': True, '_test_segmenter': TechnicalSegmenter()})
    assert again['completed_frames'] == 3 and again['completed_images'] == 6
    assert again['detected_objects'] == 6 and again['new_frames'] == 0 and again['resumed_frames'] == 3
    assert json.loads((dataset/'annotation_status.json').read_text()) == again


def test_missing_middle_annotation_replays_tracking_suffix(tmp_path):
    dataset = tmp_path/'dataset'
    episode, rows = make_dataset(dataset)
    annotate_dataset(dataset, {'allow_test': True, '_test_segmenter': TechnicalSegmenter()})
    output = episode/'annotations/sam3-v1/frames.jsonl'
    lines = output.read_text().splitlines()
    output.write_text(lines[0]+'\n'+lines[2]+'\n')
    segmenter = TechnicalSegmenter()
    result = annotate_dataset(dataset, {'allow_test': True, '_test_segmenter': segmenter})
    assert segmenter.calls == 4  # middle and later frames, both cameras
    assert result['resumed_frames'] == 1 and result['new_frames'] == 2
    assert load_frame_annotations(episode, rows[-1], allow_test=True)['frame_id'] == rows[-1]['frame_id']


def test_mask_statistics_are_checked_against_saved_pixels(tmp_path):
    dataset = tmp_path/'dataset'
    episode, rows = make_dataset(dataset, 1)
    annotate_dataset(dataset, {'allow_test': True, '_test_segmenter': TechnicalSegmenter()})
    record = load_frame_annotations(episode, rows[0], allow_test=True)
    changed = copy.deepcopy(record)
    changed['cameras']['top']['objects'][0]['center_xy'][0] += 1
    with pytest.raises(AnnotationError, match='Mask center'):
        validate_frame_annotations(changed, rows[0], episode, allow_test=True)
    changed = copy.deepcopy(record)
    changed['cameras']['top']['objects'][0]['area_px'] += 1
    with pytest.raises(AnnotationError, match='Mask area'):
        validate_frame_annotations(changed, rows[0], episode, allow_test=True)


def sam_provenance_fixture():
    from so101_lab.annotations import PREPROCESSING, PROCESSING_CONTRACT, TRACKING_METHOD
    return {'backend': 'sam3', 'checkpoint_sha256': 'a'*64,
            'checkpoint_path': '/technical/fixture/only',
            'source': {'commit': 'b'*40, 'runtime_sha256': 'c'*64},
            'prompts': {'cube': 'cube', 'box': 'box'}, 'confidence_threshold': .5,
            'preprocessing': PREPROCESSING, 'tracking': TRACKING_METHOD,
            'processing_contract': copy.deepcopy(PROCESSING_CONTRACT)}


def test_production_provenance_complete_and_portable_identity():
    from so101_lab.annotations import validate_annotation_provenance, annotation_provenance_identity
    provenance = sam_provenance_fixture()
    validate_annotation_provenance(provenance)
    relocated = copy.deepcopy(provenance)
    relocated.update(checkpoint_path='/different/cache/model.pt', device='cpu', configuration_hash='different_resume_hash')
    validate_annotation_provenance(relocated, expected=provenance)
    assert annotation_provenance_identity(relocated) == annotation_provenance_identity(provenance)
    changed = copy.deepcopy(provenance)
    changed['source']['runtime_sha256'] = 'd'*64
    with pytest.raises(AnnotationError, match='differs'):
        validate_annotation_provenance(changed, expected=provenance)
    with pytest.raises(AnnotationError, match='SHA256'):
        validate_annotation_provenance({'backend': 'sam3'})


def test_configuration_identity_uses_content_not_checkpoint_path_or_mtime(tmp_path, monkeypatch):
    from so101_lab import annotations
    first = tmp_path/'model_a.pt'; first.write_bytes(b'fixture weights: not a real model')
    second = tmp_path/'model_b.pt'; second.write_bytes(first.read_bytes())
    monkeypatch.setattr(annotations, '_sam_source_provenance', lambda: sam_provenance_fixture()['source'])
    a = annotations.SAM3Segmenter({'checkpoint_path': str(first)}).configuration_hash()
    b = annotations.SAM3Segmenter({'checkpoint_path': str(second)}).configuration_hash()
    assert a == b
    second.write_bytes(b'different fixture weights')
    assert annotations.SAM3Segmenter({'checkpoint_path': str(second)}).configuration_hash() != a
    with pytest.raises(AnnotationError, match='device'):
        annotations.SAM3Segmenter({'device': 'cuda:0'})


def test_long_tracking_gap_invalidates_prior_identity():
    tracker = CausalAssociator('top', max_gap_ns=100)
    obj = lambda: {'category': 'cube', 'score': .9, 'bbox_xyxy': [0, 0, 10, 10]}
    first, _ = tracker.update([obj()], 100, 'first')
    after, lost = tracker.update([obj()], 201, 'after')
    assert lost == [first[0]['tracking']['id']]
    assert after[0]['tracking']['id'] != first[0]['tracking']['id']
    assert after[0]['tracking']['status'] == 'new'


def test_float_rgb_and_capture_time_reordering_fail_closed(tmp_path):
    dataset = tmp_path/'dataset'
    episode, rows = make_dataset(dataset)
    images = {camera: np.asarray(Image.open(episode/info['path'])).astype(np.float32)/255 for camera, info in rows[0]['cameras'].items()}
    with pytest.raises(AnnotationError, match='uint8'):
        annotate_observation(rows[0], images, TechnicalSegmenter())
    rows[1]['cameras']['top']['timestamp_ns'] = rows[0]['cameras']['top']['timestamp_ns']-1
    (episode/'frames.jsonl').write_text(''.join(json.dumps(row)+'\n' for row in rows))
    with pytest.raises(AnnotationError, match='Немонотонный'):
        annotate_dataset(dataset, {'allow_test': True, '_test_segmenter': TechnicalSegmenter()})


def test_geometry_nan_pixel_bad_intrinsics_and_nonrigid_tcp_invalid():
    cal = calibration()
    camera = {key: cal[key] for key in ('camera_serial', 'resolution', 'mount_fingerprint', 'joint_calibration_sha256')}
    assert not estimate_point_on_plane([float('nan'), 1], cal, camera)['valid']
    bad = copy.deepcopy(cal); bad['intrinsics'][2] = [0, 0, 0]
    assert not check_applicability(bad, camera)[0]
    fk = {'verified_current': True, 'joint_calibration_sha256': 'fixture', 'joint_units': 'rad',
          'dh': [{'a_m': 0, 'alpha_rad': 0, 'd_m': 0, 'theta_offset_rad': 0}],
          'tcp_transform': [[2,0,0,0], [0,1,0,0], [0,0,1,0], [0,0,0,1]], 'tcp_sigma_m': .01}
    assert not forward_kinematics([0], fk, timestamp_ns=1, joint_calibration_sha256='fixture')['valid']


def test_dark_saturated_color_is_unknown_not_confident_black():
    rgb = np.zeros((10, 10, 3), np.uint8)
    rgb[:] = [3, 4, 11]  # actual acceptance blue mask median under low exposure
    color = measure_color(rgb, np.ones((10, 10), bool))
    assert color['valid'] is False and color['label'] == 'unknown'
    assert color['rgb'] == [3., 4., 11.] and color['hsv'][1] > .7
    assert 'яркость' in color['reason']


def test_cross_category_duplicate_masks_preserve_raw_detections_but_flag_ambiguity(tmp_path):
    class ConflictingSegmenter(TechnicalSegmenter):
        def segment(self, rgb):
            original = super().segment(rgb)
            return original + [{**original[0], 'category': 'box', 'score': .8}]
    dataset = tmp_path/'dataset'
    episode, rows = make_dataset(dataset, 1)
    result = annotate_dataset(dataset, {'allow_test': True, '_test_segmenter': ConflictingSegmenter()})
    assert result['ambiguous_objects'] == 4 and result['usable_category_objects'] == 0
    record = load_frame_annotations(episode, rows[0], allow_test=True)
    objects = record['cameras']['top']['objects']
    assert len(objects) == 2 and {o['category'] for o in objects} == {'cube', 'box'}
    assert all(o['category_ambiguous'] for o in objects)
    assert all(o['category_candidates'] == ['box', 'cube'] for o in objects)
    assert objects[0]['cross_category_overlaps'][0]['mask_iou'] == 1.
    changed = copy.deepcopy(record)
    changed['cameras']['top']['objects'][0]['category_ambiguous'] = False
    with pytest.raises(AnnotationError, match='ambiguity'):
        validate_frame_annotations(changed, rows[0], episode, allow_test=True)
