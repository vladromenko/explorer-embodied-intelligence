"""Actual OpenCV solves/detection over synthetic inputs only; no hardware owners."""
import copy
import hashlib
import json
import math
import re
from pathlib import Path
import xml.etree.ElementTree as ET

import cv2
import numpy as np
import pytest

from explorer_shared.tabletop_grounding import CachedFrame,TableCalibration
from explorer_shared.tabletop_calibrate import (
    DEFAULT_BOARD,CalibrationBlocked,calibrate_from_cached_frames,calibrate_from_synthetic_observations,
    canonical,observe_charuco,opencv_capabilities,printable_board,sha,write_printable_board,
)


def board():
    result=cv2.aruco.CharucoBoard((6,8),.03,.0225,cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50))
    result.setLegacyPattern(False);return result


def jpeg(image):
    okay,encoded=cv2.imencode('.jpg',image,[cv2.IMWRITE_JPEG_QUALITY,95]);assert okay
    return encoded.tobytes()


def projected_fixture():
    K=np.asarray([[700.,0,320.],[0,710.,240.],[0,0,1.]])
    distortion=np.asarray([-.08,.015,.001,-.002,.003])
    points=board().getChessboardCorners();frames=[];observations=[];poses=[]
    for index in range(12):
        rx=[-.26,.02,.24][index%3];ry=[-.24,.22,-.02][index//3%3]
        rotation=np.asarray([rx,ry,(index-5)*.015])
        centroid=np.asarray([[190,320,450][index%3],[140,240,340][index//3%3]],float)
        z=[.59,.68,.76][index%3]
        R=cv2.Rodrigues(rotation)[0];middle=R@points.mean(axis=0)
        translation=np.asarray([(centroid[0]-320)*z/700-middle[0],(centroid[1]-240)*z/710-middle[1],z-middle[2]])
        pixels=cv2.projectPoints(points,rotation,translation,K,distortion)[0].reshape(-1,2)
        image=np.full((480,640,3),index*13,np.uint8)
        frame=CachedFrame('software-camera',f'view-{index}',jpeg(image),640,480,'software-node','software-boot',1000+index)
        observation={'schema':'explorer_charuco_view_v1','source_frame':frame.metadata(),'board_spec_sha256':sha(canonical(DEFAULT_BOARD)),
            'charuco_ids':list(range(len(points))),'corners_px':pixels.astype(float).tolist(),
            'detector':'SYNTHETIC_cv2.projectPoints_not_camera_evidence'}
        frames.append(frame);observations.append(observation);poses.append((rotation,translation))
    base_from_board=np.diag([1.,-1.,-1.,1.])
    board_points=np.asarray([[0,0,0],[.18,0,0],[.18,.24,0],[0,.24,0],[.09,.12,0]])
    base_points=(base_from_board@np.column_stack((board_points,np.ones(len(board_points)))).T).T[:,:3]
    context={'calibration_id':'synthetic-solve-only','camera_id':'software-camera','robot_id':'software-robot','base_frame':'software-base',
        'clock_domain':'software-node','boot_id':'software-boot','mount_id':'software-mount','valid_from_ns':0,'valid_until_ns':100000}
    measurement={'schema':'explorer_board_to_robot_measurement_v1','units':'m','provenance':'synthetic',
        **{key:context[key] for key in ('camera_id','robot_id','base_frame','clock_domain','boot_id','mount_id')},
        'measured_by':'synthetic-fixture-no-person-or-robot','measurement_method':'synthetic_metric_correspondences',
        'printed_scale_checked':True,'board_spec_sha256':sha(canonical(DEFAULT_BOARD)),
        'printed_board_pdf_sha256':sha(printable_board()['pdf']),'printed_ruler_length_m':.1,'printed_ruler_sigma_m':.0002,'measurement_sigma_m':.0005,'rotation_sigma_rad':.001,'board_thickness_m':0.,
        'reference_frame_id':frames[0].frame_id,'reference_frame_sha256':frames[0].sha256,
        'board_to_base':{'board_points_m':board_points.tolist(),'base_points_m':base_points.tolist()},
        'table_up_direction_base':[0,0,1.],
        'workspace_polygon_base_m':[[0,-.24,0],[.18,-.24,0],[.18,0,0],[0,0,0]]}
    camera_from_board=np.eye(4);camera_from_board[:3,:3]=cv2.Rodrigues(poses[0][0])[0];camera_from_board[:3,3]=poses[0][1]
    expected_base_from_camera=base_from_board@np.linalg.inv(camera_from_board)
    return frames,observations,canonical(measurement),context,K,distortion,expected_base_from_camera


def solve(fixture):return calibrate_from_synthetic_observations(*fixture[:4])


def test_actual_OpenCV_intrinsics_pose_registration_and_metric_plane_roundtrip_remains_synthetic():
    fixture=projected_fixture();frames,observations,measurement,context,K,distortion,expected=fixture
    before=[frame.jpeg for frame in frames];bundle=solve(fixture);body=bundle.document;calibration=bundle.validate()
    assert body['provenance']=='synthetic' and not body['physical_execution_allowed'] and not body['real_robot_execution_tested']
    np.testing.assert_allclose(body['intrinsics'],K,rtol=0,atol=.015)
    np.testing.assert_allclose(body['distortion_k1_k2_p1_p2_k3'],distortion,rtol=0,atol=.003)
    np.testing.assert_allclose(body['base_from_camera'],expected,atol=3e-5)
    assert body['intrinsics_residual_rmse_px']<.001 and body['residual_rmse_m']<1e-8
    point=np.asarray([.09,-.12,0.]);pixel=calibration.project(point)
    grounded=calibration.ground(pixel,purpose='placement_cell')
    np.testing.assert_allclose(grounded['point_table_m'],point,atol=1e-6)
    assert grounded['uncertainty_bound_m']>0 and grounded['provenance']=='synthetic'
    assert all(frame.jpeg==old for frame,old in zip(frames,before))
    assert bundle.source_payloads['robot-measurements.json']==measurement
    assert len([name for name in body['source_files_sha256'] if name.startswith('views/')])==12
    calibration.check_frame(frames[0],now_ns=2000,clock_domain=context['clock_domain'],boot_id=context['boot_id'],max_age_ns=2000)
    with pytest.raises(ValueError,match='height'):calibration.ground(pixel,purpose='object')


def test_real_aruco_corner_detection_on_original_generated_board_JPEG_and_blank_blocks_calibration():
    canvas=np.full((480,640),255,np.uint8);canvas[40:440,170:470]=board().generateImage((300,400),marginSize=0,borderBits=1)
    frame=CachedFrame('software-camera','rendered-board',jpeg(canvas),640,480,'software-node','software-boot',1000)
    detection=observe_charuco(frame,expected_frame_sha256=frame.sha256)
    assert detection['detector']=='cv2.aruco.CharucoDetector.detectBoard'
    assert len(detection['charuco_ids'])==35 and detection['detected_marker_count']==24
    assert detection['source_frame']['frame_sha256']==frame.sha256 and not detection['physical_board_placement_verified']
    with pytest.raises(CalibrationBlocked,match='hash'):observe_charuco(frame,expected_frame_sha256='0'*64)
    fixture=projected_fixture();frames=fixture[0];measurement=json.loads(fixture[2]);measurement['provenance']='measured'
    with pytest.raises(CalibrationBlocked,match='distinct_good_board_views'):
        calibrate_from_cached_frames(frames,canonical(measurement),fixture[3],expected_frame_sha256={f.frame_id:f.sha256 for f in frames})


def test_printable_A4_SVG_PDF_exact_scale_and_vector_pattern_matches_official_board(tmp_path):
    generated=printable_board();metadata=json.loads(generated['metadata'])
    assert metadata['board_size_mm']==[180.,240.] and metadata['square_length_mm']==30.
    assert metadata['marker_length_mm']==22.5 and not metadata['metric_scale_physical_verified']
    assert metadata['svg_sha256']==sha(generated['svg']) and metadata['pdf_sha256']==sha(generated['pdf'])
    root=ET.fromstring(generated['svg']);assert root.attrib['width']=='210mm' and root.attrib['height']=='297mm'
    reconstructed=np.full_like(generated['raster'],255);origin=metadata['board_origin_on_page_mm']
    for node in root:
        if node.attrib.get('class')=='board-bit':
            x,y,w,h=[float(node.attrib[key]) for key in ('x','y','width','height')]
            x,y,w,h=round((x-origin[0])*8),round((y-origin[1])*8),round(w*8),round(h*8)
            reconstructed[y:y+h,x:x+w]=0
    assert np.array_equal(reconstructed,generated['raster'])
    box=re.search(rb'/MediaBox \[0 0 ([\d.]+) ([\d.]+)\]',generated['pdf']);assert box
    np.testing.assert_allclose([float(box[1]),float(box[2])],[210*72/25.4,297*72/25.4],atol=1e-6)
    paths=write_printable_board(tmp_path);before={name:Path(path).read_bytes() for name,path in paths.items()}
    assert paths==write_printable_board(tmp_path) and all(Path(paths[name]).read_bytes()==raw for name,raw in before.items())


@pytest.mark.parametrize('fault',['view_hash','duplicate','clock','reference_hash','registration','printed_scale','plane','missing_measurement'])
def test_unbound_inconsistent_or_bad_metric_inputs_never_produce_calibration(fault):
    fixture=list(projected_fixture());frames,observations=fixture[:2];measure=json.loads(fixture[2])
    if fault=='view_hash':observations[0]['source_frame']['frame_sha256']='0'*64
    elif fault=='duplicate':frames[1]=frames[0];observations[1]=copy.deepcopy(observations[0])
    elif fault=='clock':fixture[3]=dict(fixture[3],boot_id='other-boot')
    elif fault=='reference_hash':measure['reference_frame_sha256']='0'*64
    elif fault=='registration':measure['board_to_base']['base_points_m'][1][0]+=.08
    elif fault=='printed_scale':measure['printed_scale_checked']=False
    elif fault=='plane':measure['workspace_polygon_base_m'][0][2]=.05
    else:measure.pop('board_to_base')
    fixture[2]=canonical(measure)
    with pytest.raises(CalibrationBlocked):solve(fixture)


def test_high_reprojection_residual_and_poor_view_coverage_are_rejected():
    fixture=list(projected_fixture());rng=np.random.default_rng(42)
    for observation in fixture[1]:observation['corners_px']=(np.asarray(observation['corners_px'])+rng.normal(0,4,(35,2))).tolist()
    with pytest.raises(CalibrationBlocked,match='residual_too_high'):solve(fixture)
    fixture=list(projected_fixture())
    first=np.asarray(fixture[1][0]['corners_px'])
    for index,observation in enumerate(fixture[1]):observation['corners_px']=(first+index*.01).tolist()
    with pytest.raises(CalibrationBlocked,match='coverage_insufficient'):solve(fixture)


def test_original_calibration_payload_hashes_are_required_by_existing_geometry_reader():
    bundle=solve(projected_fixture());sources=dict(bundle.source_payloads);sources['views/000.jpg']=b'changed'
    with pytest.raises(ValueError,match='hashes'):TableCalibration(bundle.artifact_bytes,sources)
    assert opencv_capabilities()['CharucoDetector'] and not opencv_capabilities()['device_opened']


def test_actual_JPEG_corner_detection_to_intrinsics_and_table_solver_is_exercised_without_measured_claim():
    fixture=list(projected_fixture());_,observations,raw_measurement,context,K,distortion,_=fixture
    pattern=board().generateImage((1440,1920),marginSize=0,borderBits=1)
    plane=np.asarray([[0,0,0],[.18,0,0],[.18,.24,0],[0,.24,0]],np.float32)
    raster_corners=np.asarray([[0,0],[1439,0],[1439,1919],[0,1919]],np.float32)
    frames=[]
    for index in range(12):
        rotation=np.asarray([[-.26,.02,.24][index%3],[-.24,.22,-.02][index//3%3],(index-5)*.015])
        centroid=np.asarray([[190,320,450][index%3],[140,240,340][index//3%3]],float);z=[.59,.68,.76][index%3]
        middle=cv2.Rodrigues(rotation)[0]@board().getChessboardCorners().mean(axis=0)
        translation=np.asarray([(centroid[0]-320)*z/700-middle[0],(centroid[1]-240)*z/710-middle[1],z-middle[2]])
        pixels=cv2.projectPoints(plane,rotation,translation,K,np.zeros(5))[0].reshape(-1,2)
        image=cv2.warpPerspective(pattern,cv2.getPerspectiveTransform(raster_corners,pixels),(640,480),borderValue=255)
        frames.append(CachedFrame(context['camera_id'],f'rendered-{index}',jpeg(image),640,480,context['clock_domain'],context['boot_id'],1000+index,'synthetic_projected_render'))
    measurement=json.loads(raw_measurement);measurement.update(reference_frame_id=frames[0].frame_id,reference_frame_sha256=frames[0].sha256)
    bundle=calibrate_from_cached_frames(frames,canonical(measurement),context,expected_frame_sha256={frame.frame_id:frame.sha256 for frame in frames},source_provenance='synthetic')
    assert bundle.document['provenance']=='synthetic' and bundle.document['accepted_view_count']>=10
    np.testing.assert_allclose(bundle.document['intrinsics'],K,rtol=0,atol=15)
    assert bundle.document['intrinsics_residual_rmse_px']<.8
    with pytest.raises(CalibrationBlocked,match='cannot_be_measured'):
        calibrate_from_cached_frames(frames,canonical(measurement),context,expected_frame_sha256={frame.frame_id:frame.sha256 for frame in frames})
