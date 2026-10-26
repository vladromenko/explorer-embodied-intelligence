"""OpenCV ChArUco calibration from original cached JPEG views; no device owners.

Metric camera/table geometry requires actual board views and source-bound human
board-to-base measurements. Synthetic corner tests always emit synthetic artifacts.
No robot, capture, NPU, firmware or calibration-register operation is imported.
"""
from __future__ import annotations
from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
import re

import numpy as np
from .tabletop_grounding import CachedFrame,TableCalibration,CALIBRATION_SCHEMA

BOARD_SCHEMA='explorer_metric_charuco_board_v1'
DEFAULT_BOARD={'schema':BOARD_SCHEMA,'board_id':'explorer-charuco-6x8-30mm-v1','dictionary':'DICT_4X4_50',
    'squares_x':6,'squares_y':8,'square_length_m':.03,'marker_length_m':.0225,
    'legacy_pattern':False,'border_bits':1,'page_mm':[210.,297.]}


def canonical(value):return json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()
def sha(raw):return hashlib.sha256(raw).hexdigest()


class CalibrationBlocked(ValueError):
    def __init__(self,reasons,diagnostics=None):
        self.reasons=list(dict.fromkeys(reasons));self.diagnostics=diagnostics or {}
        super().__init__(';'.join(self.reasons))


@dataclass(frozen=True)
class CalibrationLimits:
    min_views:int=10
    min_corners:int=12
    min_corner_hull_fraction:float=.015
    min_image_span_fraction:float=.45
    min_centroid_span_fraction:float=.15
    min_normal_span:float=.12
    max_rms_px:float=.8
    max_per_view_rms_px:float=1.5
    max_registration_rmse_m:float=.003
    max_plane_rmse_m:float=.003


@dataclass(frozen=True)
class CalibrationBundle:
    artifact_bytes:bytes
    source_payloads:dict
    diagnostics:dict
    @property
    def document(self):return json.loads(self.artifact_bytes)
    def validate(self):return TableCalibration(self.artifact_bytes,self.source_payloads)


def opencv_capabilities():
    import cv2
    aruco=getattr(cv2,'aruco',None)
    return {'opencv_version':cv2.__version__,'aruco_available':aruco is not None,
        'CharucoBoard':hasattr(aruco,'CharucoBoard'),'CharucoDetector':hasattr(aruco,'CharucoDetector'),
        'calibrateCameraExtended':hasattr(cv2,'calibrateCameraExtended'),'solvePnP':hasattr(cv2,'solvePnP'),
        'device_opened':False}


def _board(spec):
    import cv2
    spec=dict(spec)
    if not all(opencv_capabilities()[key] for key in ('aruco_available','CharucoBoard','CharucoDetector','calibrateCameraExtended','solvePnP')):
        raise CalibrationBlocked(['opencv_aruco_solver_unavailable_no_package_upgrade_performed'])
    if spec.get('schema')!=BOARD_SCHEMA or not re.fullmatch('[A-Za-z0-9_.-]{1,80}',str(spec.get('board_id',''))) or spec.get('dictionary')!='DICT_4X4_50':raise ValueError('Explicit supported metric board identity/dictionary required')
    x,y=spec.get('squares_x'),spec.get('squares_y');square,marker=spec.get('square_length_m'),spec.get('marker_length_m')
    if any(type(value) is not int or not 3<=value<=10 for value in (x,y)) or x*y>100:raise ValueError('Bounded ChArUco grid required')
    if any(type(value) not in (float,int) or not math.isfinite(value) or value<=0 for value in (square,marker)) or not .5*square<=marker<=.85*square:raise ValueError('Metric square/marker lengths required')
    if spec.get('legacy_pattern') is not False or spec.get('border_bits')!=1 or spec.get('page_mm')!=[210.,297.]:raise ValueError('Pinned current-pattern A4 board layout required')
    if square*x*1000>190 or square*y*1000>267:raise ValueError('Board does not fit the explicit A4 metric page')
    dictionary=cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
    board=cv2.aruco.CharucoBoard((x,y),float(square),float(marker),dictionary);board.setLegacyPattern(False)
    return board


def _vector_rectangles(binary):
    active={};result=[]
    for row,line in enumerate(binary):
        padded=np.concatenate(([False],line==0,[False]));changes=np.flatnonzero(padded[1:]!=padded[:-1])
        runs={(int(start),int(end)) for start,end in zip(changes[::2],changes[1::2])}
        for key in list(active):
            if key not in runs:result.append(active.pop(key))
        for start,end in runs:
            if (start,end) in active:active[(start,end)][3]+=1
            else:active[(start,end)]=[start,row,end-start,1]
    result.extend(active.values())
    return sorted(result,key=lambda item:(item[1],item[0],item[2],item[3]))


def printable_board(spec=None):
    """Deterministic exact-scale vector SVG/PDF + source geometry metadata."""
    spec=dict(DEFAULT_BOARD if spec is None else spec);board=_board(spec)
    pixels_per_mm=8;size_mm=[spec['squares_x']*spec['square_length_m']*1000,spec['squares_y']*spec['square_length_m']*1000]
    pixels=[int(round(value*pixels_per_mm)) for value in size_mm]
    raster=board.generateImage(tuple(pixels),marginSize=0,borderBits=1)
    rectangles=_vector_rectangles(raster);origin=[(210-size_mm[0])/2,(297-size_mm[1])/2]
    rects=[[origin[0]+x/pixels_per_mm,origin[1]+y/pixels_per_mm,w/pixels_per_mm,h/pixels_per_mm] for x,y,w,h in rectangles]
    title='Explorer ChArUco | square%gmm | print100%% | verify ruler'%(spec['square_length_m']*1000)
    svg=['<svg xmlns="http://www.w3.org/2000/svg" width="210mm" height="297mm" viewBox="0 0 210 297">',
        '<rect width="210" height="297" fill="white"/>',
        '<text x="15" y="14" font-family="sans-serif" font-size="3">'+title+'</text>']
    svg.extend('<rect class="board-bit" x="%.6f" y="%.6f" width="%.6f" height="%.6f" fill="black"/>'%tuple(rect) for rect in rects)
    svg.extend(['<path d="M15 283H115M15 280V286M65 281V285M115 280V286" stroke="black" stroke-width="0.2"/>',
        '<text x="15" y="290" font-family="sans-serif" font-size="3">Ruler100mm. Disable fit-to-page; measure before calibration.</text>','</svg>'])
    svg_bytes=('\n'.join(svg)+'\n').encode()
    scale=72/25.4;content=['0 g']
    content.extend('%.6f %.6f %.6f %.6f re f'%(x*scale,(297-y-h)*scale,w*scale,h*scale) for x,y,w,h in rects)
    content.extend(['BT /F1 8 Tf 42.519685 802.204724 Td ('+title+') Tj ET',
        '0.566929 w 42.519685 39.685039 m 325.984252 39.685039 l S',
        'BT /F1 7 Tf 42.519685 19.842520 Td (Ruler100mm. Print100%; no fit-to-page. Verify measured scale.) Tj ET'])
    stream=('\n'.join(content)+'\n').encode()
    objects=[b'<< /Type /Catalog /Pages 2 0 R >>',b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
        ('<< /Type /Page /Parent 2 0 R /MediaBox [0 0 %.6f %.6f] /Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>'%(210*scale,297*scale)).encode(),
        b'<< /Length '+str(len(stream)).encode()+b' >>\nstream\n'+stream+b'endstream',b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>']
    pdf=bytearray(b'%PDF-1.4\n');offsets=[]
    for number,obj in enumerate(objects,1):offsets.append(len(pdf));pdf.extend(str(number).encode()+b' 0 obj\n'+obj+b'\nendobj\n')
    position=len(pdf);pdf.extend(b'xref\n0 6\n0000000000 65535 f \n')
    pdf.extend(b''.join(('%010d 00000 n \n'%offset).encode() for offset in offsets));pdf.extend(('trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n'%position).encode())
    metadata={'schema':'explorer_printable_metric_board_v1','board_spec':spec,'board_spec_sha256':sha(canonical(spec)),
        'board_size_mm':size_mm,'board_origin_on_page_mm':origin,'page_mm':[210.,297.],
        'square_length_mm':spec['square_length_m']*1000,'marker_length_mm':spec['marker_length_m']*1000,
        'raster_sha256':sha(raster.tobytes()),'raster_size':pixels,'svg_sha256':sha(svg_bytes),'pdf_sha256':sha(bytes(pdf)),
        'metric_scale_physical_verified':False,'operator_must_measure_printed_square_and_100mm_ruler':True,
        'opencv_aruco_version_generated':opencv_capabilities()['opencv_version'],'physical_calibration_produced':False}
    return {'svg':svg_bytes,'pdf':bytes(pdf),'metadata':canonical(metadata),'raster':raster}


def write_printable_board(directory,spec=None):
    spec=dict(DEFAULT_BOARD if spec is None else spec);generated=printable_board(spec);directory=Path(directory);directory.mkdir(parents=True,exist_ok=True)
    paths={}
    for extension,key in (('svg','svg'),('pdf','pdf'),('json','metadata')):
        path=directory/(spec['board_id']+'.'+extension);raw=generated[key]
        if path.exists() and path.read_bytes()!=raw:raise ValueError('Existing printed board artifact differs; never overwrite an issued pattern')
        if not path.exists():path.write_bytes(raw)
        paths[extension]=str(path)
    return paths


def _frame(frame,expected):
    if not isinstance(frame,CachedFrame) or not isinstance(expected,str) or len(expected)!=64 or frame.sha256!=expected:
        raise CalibrationBlocked(['original_cached_frame_hash_mismatch'])
    if len(frame.jpeg)>10*1024**2 or frame.width*frame.height>1920*1080:raise CalibrationBlocked(['calibration_frame_exceeds_bounds'])


def observe_charuco(frame,board_spec=None,*,expected_frame_sha256):
    """Actual supported OpenCV corner detection on one exact existing JPEG."""
    import cv2
    spec=dict(DEFAULT_BOARD if board_spec is None else board_spec);board=_board(spec);_frame(frame,expected_frame_sha256)
    image=cv2.imdecode(np.frombuffer(frame.jpeg,dtype=np.uint8),cv2.IMREAD_GRAYSCALE)
    if image is None or image.shape!=(frame.height,frame.width):raise CalibrationBlocked(['original_JPEG_size_or_decode_mismatch'])
    corners,ids,marker_corners,marker_ids=cv2.aruco.CharucoDetector(board).detectBoard(image)
    return {'schema':'explorer_charuco_view_v1','source_frame':frame.metadata(),
        'board_spec_sha256':sha(canonical(spec)),'charuco_ids':[] if ids is None else ids.reshape(-1).astype(int).tolist(),
        'corners_px':[] if corners is None else corners.reshape(-1,2).astype(float).tolist(),
        'detected_marker_count':0 if marker_ids is None else int(len(marker_ids)),
        'detector':'cv2.aruco.CharucoDetector.detectBoard','opencv_version':cv2.__version__,
        'physical_board_placement_verified':False}


def _array(value,shape=None):
    out=np.asarray(value,dtype=float)
    if (shape is not None and out.shape!=shape) or not np.isfinite(out).all():raise CalibrationBlocked(['invalid_finite_geometry'])
    return out


def _registration(board_points,base_points):
    board_points=_array(board_points);base_points=_array(base_points)
    if board_points.shape!=base_points.shape or board_points.ndim!=2 or board_points.shape[1]!=3 or len(board_points)<4 or len(board_points)>100:raise CalibrationBlocked(['four_or_more_bound_board_to_base_measurements_required'])
    centered=board_points-board_points.mean(axis=0)
    if np.linalg.matrix_rank(centered,tol=1e-8)<2 or np.ptp(board_points[:,:2],axis=0).min()<.05:raise CalibrationBlocked(['metric_registration_point_coverage_insufficient'])
    u,values,vt=np.linalg.svd(centered.T@(base_points-base_points.mean(axis=0)));rotation=vt.T@u.T
    if np.linalg.det(rotation)<0:vt[-1]*=-1;rotation=vt.T@u.T
    translation=base_points.mean(axis=0)-rotation@board_points.mean(axis=0)
    matrix=np.eye(4);matrix[:3,:3]=rotation;matrix[:3,3]=translation
    error=(rotation@board_points.T).T+translation-base_points
    return matrix,float(np.sqrt(np.mean(np.sum(error**2,axis=1))))


def _identity(context):
    for key in ('calibration_id','camera_id','robot_id','base_frame','clock_domain','boot_id','mount_id'):
        if not isinstance(context.get(key),str) or not context[key] or context[key].lower() in ('unknown','unset','unconfigured','none') or len(context[key])>160:raise CalibrationBlocked(['explicit_'+key+'_required'])
    if any(type(context.get(key)) is not int or context[key]<0 for key in ('valid_from_ns','valid_until_ns')) or context['valid_until_ns']<context['valid_from_ns']:
        raise CalibrationBlocked(['explicit_clock_bound_calibration_validity_required'])


def _solve(frames,observations,spec,measurement_bytes,context,limits,provenance):
    import cv2
    board=_board(spec);_identity(context)
    if provenance not in ('measured','synthetic') or not isinstance(measurement_bytes,bytes):raise CalibrationBlocked(['explicit_measurement_original_bytes_required'])
    if len(measurement_bytes)>65536:raise CalibrationBlocked(['measurement_document_exceeds_64KiB'])
    try:measurement=json.loads(measurement_bytes)
    except (ValueError,UnicodeError):raise CalibrationBlocked(['invalid_measurement_document'])
    if not isinstance(measurement,dict) or measurement.get('schema')!='explorer_board_to_robot_measurement_v1' or measurement.get('units')!='m' or measurement.get('provenance')!=provenance:
        raise CalibrationBlocked(['source_bound_metric_board_measurement_required'])
    for key in ('camera_id','robot_id','base_frame','clock_domain','boot_id','mount_id'):
        if measurement.get(key)!=context[key]:raise CalibrationBlocked(['measurement_'+key+'_mismatch'])
    board_artifacts=printable_board(spec)
    if measurement.get('board_spec_sha256')!=sha(canonical(spec)) or measurement.get('printed_board_pdf_sha256')!=sha(board_artifacts['pdf']):
        raise CalibrationBlocked(['measurement_must_bind_issued_metric_board_artifact'])
    ruler=measurement.get('printed_ruler_length_m');ruler_sigma=measurement.get('printed_ruler_sigma_m')
    if type(ruler) not in (int,float) or type(ruler_sigma) not in (int,float) or not math.isfinite(ruler) or not math.isfinite(ruler_sigma) or abs(ruler-.1)>.001 or not 0<=ruler_sigma<=.001 or provenance=='measured' and ruler_sigma<=0:
        raise CalibrationBlocked(['printed_100mm_ruler_scale_measurement_required'])
    if not measurement.get('measured_by') or not measurement.get('measurement_method') or measurement.get('printed_scale_checked') is not True:
        raise CalibrationBlocked(['operator_measurement_and_printed_scale_check_required'])
    measurement_sigma=measurement.get('measurement_sigma_m');rotation_sigma=measurement.get('rotation_sigma_rad');thickness=measurement.get('board_thickness_m')
    if any(type(value) not in (int,float) or not math.isfinite(value) for value in (measurement_sigma,rotation_sigma,thickness)) or not 0<=measurement_sigma<=.05 or not 0<=rotation_sigma<=.2 or not 0<=thickness<=.02 or provenance=='measured' and measurement_sigma<=0:
        raise CalibrationBlocked(['explicit_measurement_uncertainty_and_board_thickness_required'])
    if not isinstance(frames,(list,tuple)) or len(frames)!=len(observations) or len(frames)>100 or sum(len(frame.jpeg) for frame in frames)>128*1024**2:raise CalibrationBlocked(['bounded_original_view_pairing_required'])
    points=board.getChessboardCorners();objects=[];images=[];accepted=[];rejected=[];hashes=set();image_size=None
    for frame,observation in zip(frames,observations):
        _frame(frame,(observation.get('source_frame') or {}).get('frame_sha256'))
        if observation.get('source_frame')!=frame.metadata() or observation.get('board_spec_sha256')!=sha(canonical(spec)):
            raise CalibrationBlocked(['corner_source_frame_or_board_binding_mismatch'])
        if frame.camera_id!=context['camera_id'] or frame.clock_domain!=context['clock_domain'] or frame.boot_id!=context['boot_id']:
            raise CalibrationBlocked(['view_camera_clock_boot_mismatch'])
        if not context['valid_from_ns']<=frame.monotonic_ns<=context['valid_until_ns']:raise CalibrationBlocked(['view_outside_declared_validity'])
        if frame.sha256 in hashes:raise CalibrationBlocked(['duplicate_original_view_cannot_increase_coverage'])
        hashes.add(frame.sha256)
        if image_size is None:image_size=(frame.width,frame.height)
        if image_size!=(frame.width,frame.height):raise CalibrationBlocked(['original_view_resolution_changed'])
        ids=observation.get('charuco_ids');pixels=_array(observation.get('corners_px'))
        if not isinstance(ids,list) or any(type(value) is not int or not 0<=value<len(points) for value in ids) or len(set(ids))!=len(ids):raise CalibrationBlocked(['invalid_unique_charuco_corner_ids'])
        if len(ids)<limits.min_corners:
            rejected.append({'frame_id':frame.frame_id,'reason':'too_few_detected_board_corners','corners':len(ids)})
        else:
            if pixels.shape!=(len(ids),2) or np.any(pixels<0) or np.any(pixels[:,0]>=frame.width) or np.any(pixels[:,1]>=frame.height):raise CalibrationBlocked(['detected_corners_outside_exact_source_image'])
            hull=cv2.convexHull(pixels.astype(np.float32));area=float(cv2.contourArea(hull))/(frame.width*frame.height)
            if area<limits.min_corner_hull_fraction:rejected.append({'frame_id':frame.frame_id,'reason':'corner_image_coverage_too_small'})
            else:
                objects.append(points[np.asarray(ids)].astype(np.float32));images.append(pixels.astype(np.float32));accepted.append((frame,observation))
    diagnostics={'accepted_views':len(accepted),'rejected_views':rejected,'opencv_version':cv2.__version__,'physical_execution_allowed':False}
    if len(accepted)<limits.min_views:raise CalibrationBlocked(['need_'+str(limits.min_views)+'_distinct_good_board_views'],diagnostics)
    all_pixels=np.concatenate(images);centroids=np.asarray([image.mean(axis=0) for image in images]);width,height=image_size
    span=np.ptp(all_pixels,axis=0)/[width,height];center_span=np.ptp(centroids,axis=0)/[width,height]
    corner_coverage=len({value for _,observation in accepted for value in observation['charuco_ids']})/len(points)
    if span.min()<limits.min_image_span_fraction or center_span.min()<limits.min_centroid_span_fraction or corner_coverage<.8:
        raise CalibrationBlocked(['board_view_image_or_corner_coverage_insufficient'],dict(diagnostics,image_span=span.tolist(),centroid_span=center_span.tolist(),corner_id_coverage=corner_coverage))
    try:rms,K,dist,rvecs,tvecs,std_intr,std_extr,per_view=cv2.calibrateCameraExtended(objects,images,image_size,None,None,flags=0)
    except cv2.error as error:raise CalibrationBlocked(['opencv_intrinsics_solver_failed'],dict(diagnostics,error=str(error)))
    dist=dist.reshape(-1)
    if not np.isfinite(K).all() or not np.isfinite(dist).all() or len(dist)!=5 or not .3*width<K[0,0]<4*width or not .3*height<K[1,1]<4*width or not 0<K[0,2]<width or not 0<K[1,2]<height:
        raise CalibrationBlocked(['intrinsics_solution_nonfinite_or_implausible'],diagnostics)
    if rms>limits.max_rms_px or float(np.max(per_view))>limits.max_per_view_rms_px:
        raise CalibrationBlocked(['intrinsics_reprojection_residual_too_high'],dict(diagnostics,rms_px=float(rms),per_view_rms_px=per_view.reshape(-1).tolist()))
    normals=np.asarray([cv2.Rodrigues(rotation)[0][:,2] for rotation in rvecs])
    if np.ptp(normals[:,:2],axis=0).min()<limits.min_normal_span:raise CalibrationBlocked(['board_tilt_diversity_insufficient'],diagnostics)
    reference=next(((frame,observation) for frame,observation in accepted if frame.frame_id==measurement.get('reference_frame_id')),None)
    if reference is None or reference[0].sha256!=measurement.get('reference_frame_sha256'):
        raise CalibrationBlocked(['robot_measurement_must_bind_exact_reference_board_view'],diagnostics)
    reference_frame,reference_observation=reference;indices=np.asarray(reference_observation['charuco_ids']);reference_pixels=np.asarray(reference_observation['corners_px'],np.float32)
    okay,rotation,translation=cv2.solvePnP(points[indices],reference_pixels,K,dist,flags=cv2.SOLVEPNP_ITERATIVE)
    if not okay or not np.isfinite(rotation).all() or not np.isfinite(translation).all():raise CalibrationBlocked(['reference_board_pose_solver_failed'],diagnostics)
    camera_from_board=np.eye(4);camera_from_board[:3,:3]=cv2.Rodrigues(rotation)[0];camera_from_board[:3,3]=translation.reshape(3)
    depths=(camera_from_board[:3,:3]@points[indices].T+translation).T[:,2]
    projected=cv2.projectPoints(points[indices],rotation,translation,K,dist)[0].reshape(-1,2)
    pose_rms=float(np.sqrt(np.mean(np.sum((projected-reference_pixels)**2,axis=1))))
    if np.any(depths<=0) or pose_rms>limits.max_per_view_rms_px:raise CalibrationBlocked(['reference_pose_depth_or_residual_invalid'],diagnostics)
    correspondences=measurement.get('board_to_base') or {};base_from_board,registration_rms=_registration(correspondences.get('board_points_m'),correspondences.get('base_points_m'))
    board_points=_array(correspondences['board_points_m'])
    if np.any(np.abs(board_points[:,2])>1e-8) or np.any(board_points[:,:2]<0) or np.any(board_points[:,0]>spec['squares_x']*spec['square_length_m']+1e-8) or np.any(board_points[:,1]>spec['squares_y']*spec['square_length_m']+1e-8):raise CalibrationBlocked(['metric_registration_points_outside_known_printed_board'])
    if registration_rms>limits.max_registration_rmse_m:raise CalibrationBlocked(['board_to_robot_registration_residual_too_high'],dict(diagnostics,registration_rmse_m=registration_rms))
    up=_array(measurement.get('table_up_direction_base'),(3,));length=np.linalg.norm(up)
    if not .99<length<1.01:raise CalibrationBlocked(['explicit_table_up_unit_direction_required'])
    normal=base_from_board[:3,2];normal=normal if np.dot(normal,up)>0 else -normal
    if np.dot(normal,up)<.95:raise CalibrationBlocked(['table_up_and_measured_board_plane_disagree'])
    x_axis=base_from_board[:3,0];y_axis=np.cross(normal,x_axis)
    base_from_table=np.eye(4);base_from_table[:3,:3]=np.column_stack((x_axis,y_axis,normal));base_from_table[:3,3]=base_from_board[:3,3]-normal*thickness
    workspace=_array(measurement.get('workspace_polygon_base_m'))
    if workspace.ndim!=2 or workspace.shape[1]!=3 or not 3<=len(workspace)<=32:raise CalibrationBlocked(['measured_workspace_polygon_required'])
    table_points=(np.linalg.inv(base_from_table)@np.column_stack((workspace,np.ones(len(workspace)))).T).T[:,:3]
    plane_rms=float(np.sqrt(np.mean(table_points[:,2]**2)))
    if plane_rms>limits.max_plane_rmse_m or not cv2.isContourConvex(table_points[:,:2].astype(np.float32)) or abs(cv2.contourArea(table_points[:,:2].astype(np.float32)))<.002:
        raise CalibrationBlocked(['workspace_plane_residual_or_convex_region_invalid'],dict(diagnostics,plane_rmse_m=plane_rms))
    base_from_camera=base_from_board@np.linalg.inv(camera_from_board)
    payloads={'board-spec.json':canonical(spec),'printed-board.svg':board_artifacts['svg'],'printed-board.pdf':board_artifacts['pdf'],
        'robot-measurements.json':measurement_bytes,'camera-mount-context.json':canonical(context),
        'corner-observations.json':canonical(observations),'intrinsics-fit.json':canonical({'rms_px':float(rms),'per_view_rms_px':per_view.reshape(-1).tolist(),
            'K':K.tolist(),'distortion':dist.tolist(),'intrinsics_standard_deviation':std_intr.reshape(-1).tolist()}),
        'metric-registration.json':canonical({'base_from_board':base_from_board.tolist(),'registration_rmse_m':registration_rms,'plane_rmse_m':plane_rms})}
    for index,frame in enumerate(frames):payloads['views/'+str(index).zfill(3)+'.jpg']=frame.jpeg
    pixel_sigma=max(.1,float(rms));distance=float(np.linalg.norm(translation));focal=min(K[0,0],K[1,1])
    body={'schema':CALIBRATION_SCHEMA,'units':'m','provenance':provenance,**{key:context[key] for key in ('calibration_id','camera_id','robot_id','base_frame','clock_domain','boot_id','mount_id','valid_from_ns','valid_until_ns')},
        'intrinsics':K.tolist(),'distortion_k1_k2_p1_p2_k3':dist.tolist(),'image_size':list(image_size),
        'base_from_camera':base_from_camera.tolist(),'base_from_table':base_from_table.tolist(),'workspace_polygon_table_xy_m':table_points[:,:2].tolist(),
        'uncertainty':{'pixel_sigma':pixel_sigma,'translation_sigma_m':max(measurement_sigma,registration_rms)+pixel_sigma*distance/focal+distance*(ruler_sigma+abs(ruler-.1))/.1,
            'rotation_sigma_rad':max(rotation_sigma,pixel_sigma/focal),'plane_sigma_m':max(measurement_sigma,registration_rms,plane_rms)},
        'uncertainty_model':'declared_measurement_terms_plus_fit_pixel_propagation_not_statistical_confidence_or_robot_accuracy',
        'residual_rmse_m':registration_rms,'intrinsics_residual_rmse_px':float(rms),'reference_pose_residual_rmse_px':pose_rms,'plane_residual_rmse_m':plane_rms,
        'source_files_sha256':{name:sha(raw) for name,raw in payloads.items()},'board_spec_sha256':sha(canonical(spec)),
        'reference_frame_id':reference_frame.frame_id,'reference_frame_sha256':reference_frame.sha256,
        'opencv_implementation':opencv_capabilities(),'quality_limits':dict(limits.__dict__),'accepted_view_count':len(accepted),'rejected_views':rejected,
        'geometry_scope':'camera_intrinsics_and_measured_table_plane_not_RGB_object_height_or_grasp_contact',
        'physical_execution_allowed':False,'real_robot_execution_tested':False,'printed_scale_relative_uncertainty':(ruler_sigma+abs(ruler-.1))/.1}
    diagnostics.update(rms_px=float(rms),reference_pose_rms_px=pose_rms,registration_rmse_m=registration_rms,plane_rmse_m=plane_rms,
        image_span=span.tolist(),centroid_span=center_span.tolist(),corner_id_coverage=corner_coverage)
    bundle=CalibrationBundle(canonical(body),payloads,diagnostics);bundle.validate();return bundle


def calibrate_from_cached_frames(frames,measurement_bytes,context,*,expected_frame_sha256,board_spec=None,limits=None,source_provenance='measured'):
    """Real detector+solver on existing JPEGs; never starts a capture owner."""
    spec=dict(DEFAULT_BOARD if board_spec is None else board_spec)
    if source_provenance not in ('measured','synthetic'):raise CalibrationBlocked(['explicit_calibration_provenance_required'])
    if source_provenance=='measured' and any(frame.timestamp_basis.startswith(('synthetic','software')) for frame in frames):raise CalibrationBlocked(['synthetic_frames_cannot_be_measured_calibration'])
    if not isinstance(expected_frame_sha256,dict):raise CalibrationBlocked(['expected_original_frame_hashes_required'])
    if not isinstance(frames,(list,tuple)) or len(frames)>100 or sum(len(frame.jpeg) for frame in frames)>128*1024**2:raise CalibrationBlocked(['bounded_original_view_pairing_required'])
    observations=[observe_charuco(frame,spec,expected_frame_sha256=expected_frame_sha256.get(frame.frame_id)) for frame in frames]
    return _solve(frames,observations,spec,measurement_bytes,context,limits or CalibrationLimits(),source_provenance)


def calibrate_from_synthetic_observations(frames,observations,measurement_bytes,context,*,board_spec=None,limits=None):
    """Projected-corner software qualification always remains SYNTHETIC."""
    return _solve(frames,observations,dict(DEFAULT_BOARD if board_spec is None else board_spec),measurement_bytes,context,limits or CalibrationLimits(),'synthetic')
