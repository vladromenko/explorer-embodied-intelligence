"""ENGINEERING_GLUE: durable web calibration over the existing cached JPEG source.

Only CPU detection/solve and immutable artifacts. No camera, NPU, serial, robot
owner or actuator API is imported. Human measurements stay explicit web fields;
robot extrinsics are never inferred from RGB or silently replaced by identity.
"""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
import hashlib
import inspect
import fcntl
import json
import math
import os
from pathlib import Path
import sqlite3
import threading
import time
import uuid

import numpy as np

from .contracts import canonical,digest
from .tabletop_calibrate import (DEFAULT_BOARD,CalibrationBlocked,CalibrationLimits,
    calibrate_from_cached_frames,observe_charuco,printable_board,opencv_capabilities,board_geometry)
from .tabletop_grounding import CachedFrame,TableCalibration

MAX_CAPTURES=80
MAX_SESSION_JPEG_BYTES=24*1024**2
REQUIRED_CONTEXT=('robot_id','base_frame','mount_id')


def _uuid(value):
    try:return str(uuid.UUID(str(value)))
    except (ValueError,TypeError,AttributeError) as error:raise ValueError('Explicit calibration UUID required') from error


def _text(value,name):
    if not isinstance(value,str) or not value.strip() or len(value)>160 or value.lower() in ('unknown','none','unset','unconfigured'):raise CalibrationBlocked(['trusted_'+name+'_required'])
    return value.strip()


def _sha(raw):return hashlib.sha256(raw).hexdigest()


def _immutable(path,raw):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    if path.exists():
        if path.read_bytes()!=raw:raise ValueError('Immutable calibration source changed')
        return
    temporary=path.with_name('.'+path.name+'.'+uuid.uuid4().hex+'.tmp')
    with temporary.open('xb') as stream:stream.write(raw);stream.flush();os.fsync(stream.fileno())
    os.chmod(temporary,0o600);temporary.replace(path)
    descriptor=os.open(path.parent,os.O_RDONLY|os.O_DIRECTORY)
    try:os.fsync(descriptor)
    finally:os.close(descriptor)


class TabletopCalibrationService:
    def __init__(self,root,frame_provider,*,profile_provider,context_provider,clock_domain,boot_id,
                 clock=time.monotonic_ns,emit=None,max_frame_age_ns=2_000_000_000,current_calibration_sha_provider=None):
        self.root=Path(root);self.root.mkdir(parents=True,exist_ok=True,mode=0o700)
        self.path=self.root/'calibration.sqlite3';self.frame_provider=frame_provider;self.profile_provider=profile_provider
        self.context_provider=context_provider;self.clock_domain,self.boot_id,self.clock=clock_domain,boot_id,clock
        self.emit=emit or (lambda event:None);self.max_frame_age_ns=max_frame_age_ns;self.current_calibration_sha_provider=current_calibration_sha_provider or (lambda:None);self.emit_error=None
        self.lock=threading.RLock();self.worker=None;self.closed=False;self._board=None
        with self.connect() as db:db.executescript('''
            CREATE TABLE IF NOT EXISTS sessions(id TEXT PRIMARY KEY,created REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS events(id TEXT PRIMARY KEY,session_id TEXT NOT NULL,sequence INTEGER NOT NULL,sha256 TEXT NOT NULL,body TEXT NOT NULL,UNIQUE(session_id,sequence));
            CREATE TABLE IF NOT EXISTS requests(id TEXT PRIMARY KEY,session_id TEXT NOT NULL,operation TEXT NOT NULL,intent_sha256 TEXT NOT NULL,state TEXT NOT NULL,response TEXT);
            CREATE TABLE IF NOT EXISTS active(singleton INTEGER PRIMARY KEY CHECK(singleton=1),session_id TEXT NOT NULL,solution_id TEXT NOT NULL,artifact_sha256 TEXT NOT NULL,accept_event_id TEXT NOT NULL);
        ''')
        os.chmod(self.path,0o600)

    @contextmanager
    def connect(self):
        db=sqlite3.connect(self.path,timeout=15);db.row_factory=sqlite3.Row
        db.execute('PRAGMA journal_mode=WAL');db.execute('PRAGMA synchronous=FULL');db.execute('PRAGMA foreign_keys=ON')
        try:
            with db:yield db
        finally:db.close()

    def board(self,format):
        if format not in ('pdf','svg','json'):raise ValueError('Only issued calibration PDF/SVG/metadata are available')
        with self.lock:
            if self._board is None:self._board=printable_board()
            generated=self._board
        raw=generated['metadata'] if format=='json' else generated[format]
        return raw,dict(board_id=DEFAULT_BOARD['board_id'],format=format,sha256=_sha(raw),bytes=len(raw),content_type={'pdf':'application/pdf','svg':'image/svg+xml','json':'application/json'}[format],physical_scale_verified=False)

    def _issued(self):
        raw,unused=self.board('json');return json.loads(raw)

    def _events(self,db,session_id):
        rows=[];previous=None
        for row in db.execute('SELECT id,body,sha256,sequence FROM events WHERE session_id=? ORDER BY sequence',(session_id,)):
            body=json.loads(row['body'])
            if row['sequence']!=len(rows) or digest(body)!=row['sha256'] or body.get('previous_sha256')!=previous:raise ValueError('Calibration event chain/SHA mismatch')
            rows.append(dict(body,sha256=row['sha256'],event_id=row['id']));previous=row['sha256']
        return rows

    def _append(self,db,session_id,kind,payload):
        previous=self._events(db,session_id);body=dict(kind=kind,session_id=session_id,sequence=len(previous),previous_sha256=previous[-1]['sha256'] if previous else None,
            node_monotonic_ns=self.clock(),clock_domain=self.clock_domain,boot_id=self.boot_id,payload=payload)
        checksum=digest(body);identity='calibration-'+uuid.uuid4().hex
        db.execute('INSERT INTO events VALUES (?,?,?,?,?)',(identity,session_id,len(previous),checksum,canonical(body).decode()));return identity,checksum

    def _state(self,db,session_id=None):
        if session_id is None:
            row=db.execute('SELECT id FROM sessions ORDER BY rowid DESC LIMIT 1').fetchone();session_id=row['id'] if row else None
        if session_id is None:return None
        session_id=_uuid(session_id);events=self._events(db,session_id)
        if not events or events[0]['kind']!='created':raise ValueError('Unknown calibration session')
        created=events[0]['payload'];captures=[event['payload'] for event in events if event['kind']=='capture']
        solutions=[event['payload'] for event in events if event['kind']=='solved'];failed=[event['payload'] for event in events if event['kind']=='solve_failed']
        requested=[event['payload'] for event in events if event['kind']=='solve_requested']
        measurement_form=None
        if requested:
            raw=(self.root/requested[-1]['snapshot']['measurement_file']).read_bytes()
            if _sha(raw)!=requested[-1]['snapshot']['measurement_sha256']:raise ValueError('Saved operator form SHA mismatch')
            measurement_form=json.loads(raw).get('operator_form')
        pending=db.execute("SELECT id,state FROM requests WHERE session_id=? AND operation='solve' AND state IN ('QUEUED','RUNNING')",(session_id,)).fetchone()
        finished_state={'solved':'SOLVED','solve_failed':'FAILED','accepted':'ACCEPTED'}.get(events[-1]['kind'],'COLLECTING')
        last_result=next((event for event in reversed(events) if event['kind'] in ('solved','solve_failed')),None)
        return dict(created,session_id=session_id,revision=events[-1]['sha256'],captures=captures,solutions=solutions,
            last_failure=failed[-1] if failed else None,last_solve_result=dict(kind=last_result['kind'],payload=last_result['payload']) if last_result else None,measurement_form=measurement_form,solve_request_id=pending['id'] if pending else None,state=pending['state'] if pending else finished_state)

    def _request(self,db,request_id,operation,intent):
        previous=db.execute('SELECT * FROM requests WHERE id=?',(request_id,)).fetchone()
        if previous and (previous['operation']!=operation or previous['intent_sha256']!=digest(intent)):raise ValueError('Calibration request UUID was reused for another intent')
        return previous

    def _frame(self,identity=None):
        frame=self.frame_provider(identity)
        if not isinstance(frame,CachedFrame) or frame.clock_domain!=self.clock_domain or frame.boot_id!=self.boot_id:raise CalibrationBlocked(['same_camera_clock_boot_cached_frame_required'])
        if not 0<=self.clock()-frame.monotonic_ns<=self.max_frame_age_ns:raise CalibrationBlocked(['fresh_existing_camera_frame_required'])
        return frame

    def _context(self):
        supplied=self.context_provider() or {}
        if not isinstance(supplied,dict):raise CalibrationBlocked(['trusted_calibration_context_must_be_an_object'])
        return {key:supplied.get(key) for key in REQUIRED_CONTEXT}|{key:value for key,value in supplied.items() if key in ('robot_calibration_sha256','camera_hardware_id','robot_hardware_id')}

    def _session_matches(self,state,frame):
        if (state['camera_id']!=frame.camera_id or state['clock_domain']!=frame.clock_domain or state['boot_id']!=frame.boot_id or
                state['image_size']!=[frame.width,frame.height] or state['camera_profile_sha256']!=digest(self.profile_provider())):
            raise CalibrationBlocked(['camera_profile_clock_boot_changed_start_new_series'])

    def status(self,session_id=None):
        self.start()
        with self.connect() as db:state=self._state(db,session_id);active=db.execute('SELECT * FROM active WHERE singleton=1').fetchone()
        try:frame=self._frame();preview=frame.metadata();frame_error=None
        except (ValueError,OSError,TypeError,KeyError) as error:preview=None;frame_error=str(error)
        try:issued=self._issued();capabilities=opencv_capabilities();solver_error=None
        except (ImportError,ValueError,OSError) as error:issued=None;capabilities={'aruco_available':False};solver_error=str(error)
        context=self._context();missing=[]
        for key in REQUIRED_CONTEXT:
            try:_text(context.get(key),key)
            except CalibrationBlocked as error:missing.extend(error.reasons)
        good=[row for row in (state or {}).get('captures',[]) if row['quality']['usable']];reference=next((row for row in reversed(good) if row['purpose']=='reference'),None)
        limits=CalibrationLimits()
        solve_blockers=list(missing)
        if self.closed:solve_blockers.append('calibration_service_closed')
        if not preview:solve_blockers.append('fresh_existing_camera_frame_required')
        if not all(capabilities.get(key) for key in ('CharucoBoard','CharucoDetector','calibrateCameraExtended','solvePnP')):solve_blockers.append('opencv_aruco_solver_unavailable_no_package_upgrade_performed')
        if len(good)<limits.min_views:solve_blockers.append('minimum_distinct_good_board_views_required')
        if reference is None:solve_blockers.append('good_flat_reference_board_view_required')
        elif reference.get('trusted_context')!=context:solve_blockers.append('reference_robot_base_mount_context_changed_capture_new_reference')
        if state and preview:
            try:self._session_matches(state,frame)
            except CalibrationBlocked as error:solve_blockers.extend(error.reasons)
        if state and state['state'] in ('QUEUED','RUNNING'):solve_blockers.append('calibration_solve_in_progress')
        steps=[dict(id='print_scale',title='Распечатать доску A4 и проверить линейку100мм',instruction='Печатайте100%, отключите подгонку страницы. Измерьте линию100мм и толщину листа.'),
            dict(id='views',title='Показать доску в разных местах и наклонах',instruction='Двигайте только доску: центр и углы кадра, наклон вокруг двух осей. Камера и руки остаются неподвижными.',good_views=len(good),minimum_good_views=limits.min_views,rejected_views=len((state or {}).get('captures',[]))-len(good)),
            dict(id='reference',title='Положить доску на измеренное место стола',instruction='Снимите опорный вид. Измерьте XYZ четырёх отмеченных углов относительно указанного основания робота. Не выводите эти координаты из картинки.',reference_capture_id=reference['capture_id'] if reference else None),
            dict(id='solve',title='Рассчитать и проверить оси',instruction='Введите измерения в миллиметрах в форме. Система сама сохраняет документ и рассчитывает intrinsics, distortion и привязку; матрицы и JSON не нужны.'),
            dict(id='accept',title='Применить к сетке',instruction='Оставьте доску на опорном месте, проверьте наложение осей и подтвердите неподвижное крепление. Это не разрешение движения робота.')]
        return dict(schema='explorer_tabletop_calibration_wizard_v1',session=state,steps=steps,current_frame=preview,current_frame_error=frame_error,
            board=issued,solver_capabilities=capabilities,solver_error=solver_error,trusted_context=context,missing_facts=missing,
            ready_to_solve=bool(state and not solve_blockers),solve_blockers=list(dict.fromkeys(solve_blockers)),measurement_fields=dict(units='mm',corner_order=['A','B','C','D'],board_outer_corners_mm=(np.asarray(board_geometry()['outer_corners_m'])*1000).tolist() if issued else None,required_confirmations=['printed_scale_checked','board_flat_on_table_confirmed','camera_fixed_confirmed','robot_base_fixed_confirmed']),active=dict(active) if active else None,expected_active_calibration_sha256=active['artifact_sha256'] if active else self.current_calibration_sha_provider(),service_closed=self.closed,event_delivery_error=self.emit_error,requires_manual_JSON_or_terminal=False,physical_actions_emitted=False,robot_execution_enabled=False)

    def _quality(self,frame,observation):
        import cv2
        ids=observation.get('charuco_ids') or [];corners=np.asarray(observation.get('corners_px') or [],float);limits=CalibrationLimits();reasons=[];fraction=0.
        if len(ids)<limits.min_corners:reasons.append('too_few_detected_board_corners')
        elif corners.shape!=(len(ids),2) or not np.isfinite(corners).all():reasons.append('invalid_detected_corners')
        else:
            fraction=float(cv2.contourArea(cv2.convexHull(corners.astype(np.float32))))/(frame.width*frame.height)
            if fraction<limits.min_corner_hull_fraction:reasons.append('board_too_small_in_frame')
        return dict(usable=not reasons,reasons=reasons,detected_corners=len(ids),corner_hull_fraction=fraction,extrinsics_inferred=False,board_corner_guide=self._corner_guide(observation) if not reasons else None)

    def _corner_guide(self,observation):
        import cv2
        geometry=board_geometry();points=np.asarray(geometry['inner_corners_m'],np.float32);ids=np.asarray(observation['charuco_ids'],int)
        matrix,unused=cv2.findHomography(points[ids,:2],np.asarray(observation['corners_px'],np.float32),method=0)
        if matrix is None or not np.isfinite(matrix).all():return None
        outer=np.asarray(geometry['outer_corners_m'],np.float32)[:,:2]
        projected=cv2.perspectiveTransform(outer.reshape(1,-1,2),matrix).reshape(-1,2)
        return dict(labels=geometry['outer_corner_labels'],corners_px=projected.tolist(),board_corners_mm=(np.asarray(geometry['outer_corners_m'])*1000).tolist(),
            guide_only=True,robot_extrinsics_inferred=False)

    def capture(self,body):
        if self.closed:raise ValueError('Calibration service is closed')
        if set(body)-{'request_id','session_id','expected_revision','frame_id','frame_sha256','purpose','new_session'}:raise ValueError('Choose calibration capture fields only')
        if 'new_session' in body and type(body['new_session']) is not bool:raise ValueError('Explicit new-series boolean required')
        request_id=_uuid(body.get('request_id'));purpose=body.get('purpose','intrinsics')
        if purpose not in ('intrinsics','reference'):raise ValueError('Choose intrinsic or reference board view')
        intent={key:body.get(key) for key in ('session_id','frame_id','frame_sha256','purpose','new_session')}
        with self.connect() as db:
            previous=self._request(db,request_id,'capture',intent)
            if previous:return self.status(previous['session_id'])
            state=None if body.get('new_session') else self._state(db,body.get('session_id'))
        frame=self._frame(body.get('frame_id'))
        if body.get('frame_id')!=frame.frame_id or body.get('frame_sha256')!=frame.sha256:raise CalibrationBlocked(['capture_must_bind_displayed_original_frame_and_sha'])
        if len(frame.jpeg)>2*1024**2 or frame.width*frame.height>1920*1080:raise CalibrationBlocked(['calibration_frame_exceeds_bounds'])
        if state:
            self._session_matches(state,frame)
            if body.get('expected_revision')!=state['revision']:raise ValueError('Calibration series changed; refresh before capturing')
            if state['state'] in ('QUEUED','RUNNING'):raise CalibrationBlocked(['calibration_solve_in_progress'])
        try:observation=observe_charuco(frame,expected_frame_sha256=frame.sha256);quality=self._quality(frame,observation)
        except (CalibrationBlocked,ValueError) as error:observation=dict(source_frame=frame.metadata(),detection_error=str(error));quality=dict(usable=False,reasons=getattr(error,'reasons',[str(error)]),detected_corners=0)
        session_id=state['session_id'] if state else request_id
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE');previous=self._request(db,request_id,'capture',intent)
            if previous:return self.status(previous['session_id'])
            current=self._state(db,session_id) if state else None
            if state and (current['revision']!=body.get('expected_revision') or current['state'] in ('QUEUED','RUNNING')):raise ValueError('Calibration series changed while detecting board')
            if not state:
                issued=self._issued();provenance='synthetic' if frame.timestamp_basis.startswith(('synthetic','software')) else 'measured'
                created=dict(camera_id=frame.camera_id,clock_domain=frame.clock_domain,boot_id=frame.boot_id,image_size=[frame.width,frame.height],camera_profile_sha256=digest(self.profile_provider()),
                    board_spec=deepcopy(DEFAULT_BOARD),board_spec_sha256=issued['board_spec_sha256'],printed_board_pdf_sha256=issued['pdf_sha256'],source_provenance=provenance)
                db.execute('INSERT INTO sessions VALUES (?,?)',(session_id,time.time()));self._append(db,session_id,'created',created);current=self._state(db,session_id)
            if len(current['captures'])>=MAX_CAPTURES or sum(row['jpeg_bytes'] for row in current['captures'])+len(frame.jpeg)>MAX_SESSION_JPEG_BYTES:raise CalibrationBlocked(['calibration_series_capacity_reached_sources_preserved_start_new_series'])
            if any(row['frame']['frame_sha256']==frame.sha256 for row in current['captures']):quality=dict(quality,usable=False,reasons=quality['reasons']+['duplicate_original_view_cannot_increase_coverage'])
            relative='sessions/'+session_id+'/frames/'+frame.sha256+'.jpg';_immutable(self.root/relative,frame.jpeg)
            observed='sessions/'+session_id+'/observations/'+request_id+'.json';observed_raw=canonical(observation);_immutable(self.root/observed,observed_raw)
            payload=dict(capture_id=request_id,purpose=purpose,trusted_context=self._context(),frame=frame.metadata(),jpeg_file=relative,jpeg_bytes=len(frame.jpeg),observation_file=observed,observation_sha256=_sha(observed_raw),quality=quality)
            self._append(db,session_id,'capture',payload);db.execute('INSERT INTO requests VALUES (?,?,?,?,?,?)',(request_id,session_id,'capture',digest(intent),'COMPLETE',None))
        return self.status(session_id)

    def _load_frames(self,state):
        frames=[]
        for capture in state['captures']:
            if capture['quality']['usable']:
                raw=(self.root/capture['jpeg_file']).read_bytes();meta=capture['frame']
                if _sha(raw)!=meta['frame_sha256']:raise CalibrationBlocked(['stored_original_calibration_JPEG_changed'])
                observation=(self.root/capture['observation_file']).read_bytes()
                if _sha(observation)!=capture['observation_sha256']:raise CalibrationBlocked(['stored_original_corner_observation_changed'])
                frames.append(CachedFrame(meta['camera_id'],meta['frame_id'],raw,meta['width'],meta['height'],meta['clock_domain'],meta['boot_id'],meta['monotonic_ns'],meta['timestamp_basis']))
        return frames

    def _measurement(self,state,form,context,reference):
        if not isinstance(form,dict):raise CalibrationBlocked(['structured_web_measurement_fields_required'])
        for key in ('printed_scale_checked','board_flat_on_table_confirmed','camera_fixed_confirmed','robot_base_fixed_confirmed'):
            if form.get(key) is not True:raise CalibrationBlocked([key+'_required'])
        allowed={'operator_id','reference_capture_id','printed_ruler_length_mm','printed_ruler_sigma_mm','measurement_sigma_mm','board_thickness_mm','base_corners_mm','printed_scale_checked','board_flat_on_table_confirmed','camera_fixed_confirmed','robot_base_fixed_confirmed'}
        if set(form)-allowed:raise ValueError('Use named web measurements; client intrinsics/extrinsics are not accepted')
        if form.get('reference_capture_id')!=reference['capture_id']:raise CalibrationBlocked(['measurements_must_bind_exact_displayed_reference_capture'])
        operator=_text(form.get('operator_id'),'operator_id');corners=np.asarray(form.get('base_corners_mm'),float)
        if corners.shape!=(4,3) or not np.isfinite(corners).all() or np.max(abs(corners))>5000:raise CalibrationBlocked(['four_measured_robot_base_XYZ_corners_mm_required'])
        values={}
        for key in ('printed_ruler_length_mm','printed_ruler_sigma_mm','measurement_sigma_mm','board_thickness_mm'):
            value=form.get(key)
            if type(value) not in (int,float) or not math.isfinite(value):raise CalibrationBlocked([key+'_required'])
            values[key]=value/1000
        width=DEFAULT_BOARD['squares_x']*DEFAULT_BOARD['square_length_m'];height=DEFAULT_BOARD['squares_y']*DEFAULT_BOARD['square_length_m']
        board=[[0,0,0],[width,0,0],[width,height,0],[0,height,0]];points=corners/1000
        normal=np.cross(points[1]-points[0],points[3]-points[0]);length=float(np.linalg.norm(normal))
        if length<1e-8:raise CalibrationBlocked(['measured_board_corners_are_degenerate'])
        normal/=length
        if normal[2]<0:normal=-normal
        workspace=points-normal*values['board_thickness_mm']
        return dict(schema='explorer_board_to_robot_measurement_v1',units='m',provenance=state['source_provenance'],
            **{key:context[key] for key in ('camera_id','robot_id','base_frame','clock_domain','boot_id','mount_id')},board_spec_sha256=state['board_spec_sha256'],printed_board_pdf_sha256=state['printed_board_pdf_sha256'],
            reference_frame_id=reference['frame']['frame_id'],reference_frame_sha256=reference['frame']['frame_sha256'],measured_by=operator,
            measurement_method='four_operator_measured_board_corners_from_structured_web_form',printed_scale_checked=True,
            printed_ruler_length_m=values['printed_ruler_length_mm'],printed_ruler_sigma_m=values['printed_ruler_sigma_mm'],measurement_sigma_m=values['measurement_sigma_mm'],
            rotation_sigma_rad=min(.2,2*values['measurement_sigma_mm']/min(width,height)),rotation_uncertainty_basis='conservative_declared_point_error_over_board_extent_not_accuracy_test',
            board_thickness_m=values['board_thickness_mm'],board_to_base=dict(board_points_m=board,base_points_m=points.tolist()),table_up_direction_base=[0.,0.,1.],
            workspace_polygon_base_m=workspace.tolist(),workspace_basis='measured_board_footprint_projected_down_by_measured_paper_thickness',operator_form=deepcopy(form))

    def solve(self,body):
        if self.closed:raise ValueError('Calibration service is closed')
        if set(body)-{'request_id','session_id','expected_revision','measurements'}:raise ValueError('Choose calibration solve fields only')
        request_id=_uuid(body.get('request_id'));session_id=_uuid(body.get('session_id'));intent=dict(session_id=session_id,measurements=body.get('measurements'))
        with self.connect() as db:
            previous=self._request(db,request_id,'solve',intent)
            if previous:return self.status(session_id)
            state=self._state(db,session_id)
        if state['revision']!=body.get('expected_revision'):raise ValueError('Calibration series changed; refresh before solving')
        if state['state'] in ('QUEUED','RUNNING'):raise CalibrationBlocked(['calibration_solve_in_progress'])
        frame=self._frame();self._session_matches(state,frame);trusted=self._context()
        for key in REQUIRED_CONTEXT:_text(trusted.get(key),key)
        if not isinstance(body.get('measurements'),dict):raise CalibrationBlocked(['structured_web_measurement_fields_required'])
        reference_id=body['measurements'].get('reference_capture_id')
        reference=next((row for row in state['captures'] if row['capture_id']==reference_id and row['purpose']=='reference' and row['quality']['usable']),None)
        if reference is None:raise CalibrationBlocked(['good_flat_reference_board_view_required'])
        if reference.get('trusted_context')!=trusted:raise CalibrationBlocked(['reference_robot_base_mount_context_changed_capture_new_reference'])
        context=dict(trusted,calibration_id='tabletop-'+request_id,camera_id=state['camera_id'],clock_domain=state['clock_domain'],boot_id=state['boot_id'],valid_from_ns=min(row['frame']['monotonic_ns'] for row in state['captures']),valid_until_ns=2**63-1,
            camera_profile_sha256=state['camera_profile_sha256'],validity_scope='same_node_boot_camera_profile_and_confirmed_fixed_mount')
        measurement=self._measurement(state,body.get('measurements'),context,reference);measurement_raw=canonical(measurement)
        relative='sessions/'+session_id+'/measurements/'+request_id+'.json';_immutable(self.root/relative,measurement_raw)
        snapshot=dict(state=state,context=context,measurement_file=relative,measurement_sha256=_sha(measurement_raw),reference_capture_id=reference['capture_id'])
        with self.lock:
            if self.worker and self.worker.is_alive():raise CalibrationBlocked(['one_CPU_calibration_solver_already_running'])
            with self.connect() as db:
                db.execute('BEGIN IMMEDIATE');previous=self._request(db,request_id,'solve',intent)
                if previous:return self.status(session_id)
                current=self._state(db,session_id)
                if current['revision']!=state['revision']:raise ValueError('Calibration series changed while saving measurements')
                self._append(db,session_id,'solve_requested',dict(request_id=request_id,snapshot=snapshot));db.execute('INSERT INTO requests VALUES (?,?,?,?,?,?)',(request_id,session_id,'solve',digest(intent),'QUEUED',None))
            self.start()
        return self.status(session_id)

    def start(self):
        """Resume only a previously requested numerical solve, never a device."""
        with self.lock:
            if self.closed or (self.worker and self.worker.is_alive()):return
            with self.connect() as db:
                row=db.execute("SELECT id,session_id FROM requests WHERE operation='solve' AND state IN ('QUEUED','RUNNING') ORDER BY rowid LIMIT 1").fetchone()
                if row is None:return
                event=next((event for event in self._events(db,row['session_id']) if event['kind']=='solve_requested' and event['payload']['request_id']==row['id']),None)
                if event is None:raise ValueError('Durable calibration solve snapshot is missing')
            owner=(self.root/'solver.lock').open('a+b')
            try:fcntl.flock(owner.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:owner.close();return
            def work():
                try:self._solve_run(row['session_id'],row['id'],event['payload']['snapshot'])
                finally:fcntl.flock(owner.fileno(),fcntl.LOCK_UN);owner.close()
            self.worker=threading.Thread(target=work,name='cached-tabletop-calibration-CPU',daemon=True);self.worker.start()

    def _solve_run(self,session_id,request_id,snapshot):
        try:
            with self.connect() as db:db.execute("UPDATE requests SET state='RUNNING' WHERE id=?",(request_id,))
            state=snapshot['state'];frames=self._load_frames(state);measurement_raw=(self.root/snapshot['measurement_file']).read_bytes()
            if _sha(measurement_raw)!=snapshot['measurement_sha256']:raise CalibrationBlocked(['stored_operator_measurements_changed'])
            implementation={}
            for function in (calibrate_from_cached_frames,TableCalibration,type(self)):
                path=Path(inspect.getfile(function)).resolve();raw=path.read_bytes();relative='sessions/'+session_id+'/implementation/'+request_id+'-'+path.name
                _immutable(self.root/relative,raw);implementation[str(path)]=dict(sha256=_sha(raw),snapshot_file=relative)
            bundle=calibrate_from_cached_frames(frames,measurement_raw,snapshot['context'],expected_frame_sha256={frame.frame_id:frame.sha256 for frame in frames},source_provenance=state['source_provenance'])
            if any(_sha(Path(path).read_bytes())!=info['sha256'] for path,info in implementation.items()):raise CalibrationBlocked(['calibration_implementation_changed_during_solve'])
            solution_id=_sha(bundle.artifact_bytes);directory=self.root/'sessions'/session_id/'solutions'/solution_id
            for name,raw in bundle.source_payloads.items():_immutable(directory/name,raw)
            _immutable(directory/'calibration.json',bundle.artifact_bytes)
            calibrated=bundle.validate();overlay=dict(reference_capture_id=snapshot['reference_capture_id'],axes_px=[calibrated.project(point) for point in ([0,0,0],[.03,0,0],[0,.03,0])],workspace_polygon_px=[calibrated.project([*point,0]) for point in calibrated.body['workspace_polygon_table_xy_m']],physical_accuracy_verified=False)
            payload=dict(implementation=implementation,overlay=overlay,request_id=request_id,solution_id=solution_id,artifact_sha256=solution_id,artifact_file=(directory/'calibration.json').relative_to(self.root).as_posix(),diagnostics=bundle.diagnostics,
                provenance=bundle.document['provenance'],context=snapshot['context'],reference_capture_id=snapshot['reference_capture_id'])
            kind='solved';result_state='COMPLETE'
        except Exception as error:
            payload=dict(request_id=request_id,reasons=getattr(error,'reasons',['calibration_solver_failed']),diagnostics=getattr(error,'diagnostics',{}),error_type=type(error).__name__,measurement_file=snapshot['measurement_file']);kind='solve_failed';result_state='FAILED'
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE');self._append(db,session_id,kind,payload);db.execute('UPDATE requests SET state=?,response=? WHERE id=?',(result_state,canonical(payload).decode(),request_id))
        try:self.emit(dict(kind='so101_source.calibration_'+kind,session_id=session_id,result=deepcopy(payload),physical_actions_emitted=False))
        except Exception as error:self.emit_error=type(error).__name__

    def accepted(self):
        with self.connect() as db:
            row=db.execute('SELECT * FROM active WHERE singleton=1').fetchone()
            if not row:return None
            state=self._state(db,row['session_id']);solution=next((item for item in state['solutions'] if item['solution_id']==row['solution_id']),None)
            accepted=next((event for event in self._events(db,row['session_id']) if event['event_id']==row['accept_event_id'] and event['kind']=='accepted'),None)
            if not accepted or accepted['payload'].get('solution_id')!=row['solution_id'] or accepted['payload'].get('artifact_sha256')!=row['artifact_sha256']:raise ValueError('Accepted calibration event binding mismatch')
            if not solution or solution['artifact_sha256']!=row['artifact_sha256']:raise ValueError('Accepted calibration/index binding mismatch')
            return dict(solution,session_id=row['session_id'],accept_event_id=row['accept_event_id'],path=str(self.root/solution['artifact_file']))

    def _bundle(self,solution):
        path=(self.root/solution['artifact_file']).resolve()
        if not path.is_relative_to(self.root.resolve()):raise ValueError('Calibration artifact escaped managed wizard directory')
        raw=path.read_bytes()
        if _sha(raw)!=solution['artifact_sha256']:raise ValueError('Solved calibration bytes changed')
        body=json.loads(raw);payloads={}
        for name,expected in body['source_files_sha256'].items():
            member=(path.parent/name).resolve()
            if not member.is_relative_to(path.parent) or member.stat().st_size>MAX_SESSION_JPEG_BYTES:raise ValueError('Calibration payload path/size mismatch')
            payloads[name]=member.read_bytes()
            if _sha(payloads[name])!=expected:raise ValueError('Calibration original payload SHA changed')
        if sum(map(len,payloads.values()))>32*1024**2:raise ValueError('Calibration original payload bytes exceed bound')
        return TableCalibration(raw,payloads)

    def accept(self,body):
        if self.closed:raise ValueError('Calibration service is closed')
        if set(body)-{'request_id','session_id','expected_revision','solution_id','solution_sha256','confirm_axes_overlay','confirm_mount_unchanged','expected_active_calibration_sha256'}:raise ValueError('Choose calibration acceptance fields only')
        request_id=_uuid(body.get('request_id'));session_id=_uuid(body.get('session_id'))
        intent={key:body.get(key) for key in ('session_id','solution_id','solution_sha256','confirm_axes_overlay','confirm_mount_unchanged','expected_active_calibration_sha256')}
        with self.connect() as db:
            previous=self._request(db,request_id,'accept',intent)
            if previous:return self.status(session_id)
            state=self._state(db,session_id)
        if state['revision']!=body.get('expected_revision'):raise ValueError('Calibration series changed; refresh before accepting')
        if any(body.get(key) is not True for key in ('confirm_axes_overlay','confirm_mount_unchanged')):raise CalibrationBlocked(['explicit_axes_and_fixed_mount_review_required'])
        solution=next((item for item in state['solutions'] if item['solution_id']==body.get('solution_id')),None)
        if not solution or solution['artifact_sha256']!=body.get('solution_sha256'):raise CalibrationBlocked(['exact_solved_calibration_SHA_required'])
        calibrated=self._bundle(solution)
        if calibrated.body['provenance']!='measured' or state['source_provenance']!='measured':raise CalibrationBlocked(['synthetic_calibration_cannot_activate_physical_camera'])
        frame=self._frame();self._session_matches(state,frame);current_context=self._context()
        if any(current_context.get(key)!=solution['context'].get(key) for key in (*REQUIRED_CONTEXT,'robot_calibration_sha256','camera_hardware_id','robot_hardware_id')):raise CalibrationBlocked(['trusted_robot_base_or_mount_identity_changed'])
        reference=next(row for row in state['captures'] if row['capture_id']==solution['reference_capture_id'])
        old_raw=(self.root/reference['observation_file']).read_bytes()
        if _sha(old_raw)!=reference['observation_sha256']:raise CalibrationBlocked(['stored_original_corner_observation_changed'])
        old=json.loads(old_raw);now=observe_charuco(frame,expected_frame_sha256=frame.sha256)
        a=dict(zip(old.get('charuco_ids',[]),old.get('corners_px',[])));b=dict(zip(now.get('charuco_ids',[]),now.get('corners_px',[])));common=set(a)&set(b)
        if len(common)<CalibrationLimits().min_corners:raise CalibrationBlocked(['keep_board_at_measured_reference_position_for_acceptance'])
        errors=np.asarray([np.linalg.norm(np.asarray(a[key])-b[key]) for key in common]);rms=float(np.sqrt(np.mean(errors**2)))
        if rms>2. or float(errors.max())>4.:raise CalibrationBlocked(['camera_or_reference_board_moved_retry_reference'],dict(reference_corner_rmse_px=rms))
        calibrated.check_frame(frame,now_ns=self.clock(),clock_domain=self.clock_domain,boot_id=self.boot_id,max_age_ns=self.max_frame_age_ns)
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE');previous=self._request(db,request_id,'accept',intent)
            if previous:return self.status(session_id)
            current=self._state(db,session_id)
            if current['revision']!=state['revision']:raise ValueError('Calibration series changed while checking current board')
            # Detection and DB acquisition may block. Revalidate the exact source
            # and trusted identities at commit, not only before the CPU callback.
            if self.closed:raise ValueError('Calibration service is closed')
            bound=self._frame(frame.frame_id);self._session_matches(state,bound)
            if bound.frame_id!=frame.frame_id or bound.sha256!=frame.sha256:raise CalibrationBlocked(['acceptance_original_frame_changed'])
            context_at_commit=self._context()
            if any(context_at_commit.get(key)!=solution['context'].get(key) for key in (*REQUIRED_CONTEXT,'robot_calibration_sha256','camera_hardware_id','robot_hardware_id')):raise CalibrationBlocked(['trusted_robot_base_or_mount_identity_changed'])
            calibrated.check_frame(bound,now_ns=self.clock(),clock_domain=self.clock_domain,boot_id=self.boot_id,max_age_ns=self.max_frame_age_ns)
            active=db.execute('SELECT artifact_sha256 FROM active WHERE singleton=1').fetchone();active_sha=active['artifact_sha256'] if active else self.current_calibration_sha_provider()
            if active_sha!=body.get('expected_active_calibration_sha256'):raise ValueError('Active calibration changed; refresh before accepting another version')
            identity,unused=self._append(db,session_id,'accepted',dict(request_id=request_id,solution_id=solution['solution_id'],artifact_sha256=solution['artifact_sha256'],current_frame=frame.metadata(),reference_corner_rmse_px=rms,physical_execution_allowed=False))
            db.execute('INSERT INTO active VALUES (1,?,?,?,?) ON CONFLICT(singleton) DO UPDATE SET session_id=excluded.session_id,solution_id=excluded.solution_id,artifact_sha256=excluded.artifact_sha256,accept_event_id=excluded.accept_event_id',(session_id,solution['solution_id'],solution['artifact_sha256'],identity))
            db.execute('INSERT INTO requests VALUES (?,?,?,?,?,?)',(request_id,session_id,'accept',digest(intent),'COMPLETE',None))
        try:self.emit(dict(kind='so101_source.calibration_accepted',session_id=session_id,calibration_id=calibrated.body['calibration_id'],calibration_sha256=calibrated.sha256,physical_actions_emitted=False))
        except Exception as error:self.emit_error=type(error).__name__
        return self.status(session_id)

    def media(self,session_id,capture_id):
        session_id=_uuid(session_id);capture_id=_uuid(capture_id)
        with self.connect() as db:state=self._state(db,session_id)
        capture=next((row for row in state['captures'] if row['capture_id']==capture_id),None)
        if capture is None:raise ValueError('Unknown original calibration view')
        raw=(self.root/capture['jpeg_file']).read_bytes()
        if _sha(raw)!=capture['frame']['frame_sha256']:raise ValueError('Original calibration JPEG changed')
        return raw,deepcopy(capture['frame'])

    def close(self):
        self.closed=True
        if self.worker:self.worker.join(timeout=.25)
