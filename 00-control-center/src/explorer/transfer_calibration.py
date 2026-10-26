"""ENGINEERING_GLUE: durable transfer calibration wizard over existing cached providers.

Only immutable events/media and numerical fitting. No device owner, native POST,
robot connection or experiment package is imported. API accepts clicks/buttons;
server providers supply source-bound metric measurements, never browser JSON.
"""
from __future__ import annotations
from copy import deepcopy
import io
import json
import os
from pathlib import Path
import time
import uuid

import numpy as np
from PIL import Image

from explorer_shared.contracts import canonical,digest,event
from explorer_shared.transfer_mapping04 import TransferBlocked,sha
from explorer_shared.transfer_calibration import fit_joint_reference,fit_aperture_reference,register_task_frames

TRANSFER_SLUG='cross_embodiment_transfer'
SOURCE_SLUG='so101_source_learning'
PREFIX='skill_transfer.calibration.'


def _project_id():
    from explorer_shared.experiments import experiment_id
    return experiment_id(TRANSFER_SLUG)
STEPS=(
 dict(id='joint_reference_views',title='Согласовать позы исходной руки с CAD',roles=['source'],minimum_views=6,
      instruction='После отдельного наблюдаемого теста покажите SO-101 follower в шести разных устойчивых позах с видимым калиброванным маркером. Камера и measured joints считываются автоматически; последние два кадра остаются проверочными.'),
 dict(id='gripper_scale_views',title='Измерить апертуру двух захватов',roles=['source','target'],minimum_views=6,
      instruction='Покажите пустой захват открытым, промежуточным и закрытым: по три вида для SO-101 и ROSMASTER. Метрическая шкала/калиброванный depth должны видеть обе губки. Сила не определяется по картинке.'),
 dict(id='object_goal_views',title='Указать предмет и контейнер в обеих сценах',roles=['source','target'],minimum_views=4,
      instruction='В исходном и целевом кадрах нажмите на предмет, затем на контейнер. Система использует подтверждённую depth/плоскость и калибровку; неизвестная высота не превращается в координату.'),
)


def _uuid(value):
    try:return str(uuid.UUID(str(value)))
    except (TypeError,ValueError,AttributeError) as error:raise ValueError('Explicit wizard/skill UUID required') from error


def _records(db,wizard_id):
    result=[]
    for row in db.execute("SELECT body FROM events WHERE json_extract(body,'$.payload.type') LIKE 'skill_transfer.calibration.%' AND json_extract(body,'$.payload.wizard_id')=? ORDER BY received,event_id",(wizard_id,)):
        document=json.loads(row['body'])
        if digest(document['payload'])!=document['sha256']:raise ValueError('Immutable calibration event SHA mismatch')
        result.append(dict(payload=document['payload'],sha256=document['sha256']))
    return result


def _append(db,kind,body,identity):
    payload=dict(body,type=PREFIX+kind);document=event('nx',_project_id(),payload);document['event_id']=identity
    previous=db.execute('SELECT body FROM events WHERE event_id=?',(identity,)).fetchone()
    if previous:
        if json.loads(previous['body'])['payload']!=payload:raise ValueError('Conflicting immutable wizard request')
    else:db.execute('INSERT INTO events VALUES (?,?,?,?,?)',(identity,'nx',time.time(),document['sha256'],canonical(document).decode()))


def _state(db,wizard_id):
    wizard_id=_uuid(wizard_id);records=_records(db,wizard_id)
    created=next((row['payload'] for row in records if row['payload']['type']==PREFIX+'created'),None)
    if not created:raise ValueError('Unknown transfer calibration wizard')
    captures=[row['payload'] for row in records if row['payload']['type']==PREFIX+'capture'];solved={row['payload']['step_id']:row['payload'] for row in records if row['payload']['type']==PREFIX+'solved'}
    steps=[]
    for step in STEPS:
        views=[row for row in captures if row['step_id']==step['id']];accepted=[row for row in views if row['quality']['usable']]
        status='SIM_AUTO_GEOMETRY' if created['domain']=='simulation' else 'PASS' if step['id'] in solved else 'READY_TO_FIT' if len(accepted)>=step['minimum_views'] else 'CAPTURE_REQUIRED'
        steps.append(dict(step,status=status,accepted_views=len(accepted),rejected_views=len(views)-len(accepted),artifact=solved.get(step['id'],{}).get('artifact')))
    active=next((row['id'] for row in steps if row['status'] not in ('PASS','SIM_AUTO_GEOMETRY')),None)
    return dict(schema='explorer_skill_transfer_calibration_wizard_v1',**{key:value for key,value in created.items() if key!='type'},revision=records[-1]['sha256'],steps=steps,active_step=active,captures=captures,
        calibration_complete=active is None,physical_calibration_ready=active is None and created['domain']=='physical',physical_execution_authorized=False,
        target_dataset_adaptation_completed=False,learned_source_skill_inferred=False,requires_manual_JSON_or_terminal=False)


def create_session(store,body,*,audit):
    if audit.get('all_computable_mapping_A_resolved') is not True:raise TransferBlocked('AUTOMATIC_MAPPING_A_MUST_BE_RESOLVED_FIRST','A')
    skill_id=_uuid(body.get('skill_id'));source_version=body.get('source_version');target_version=body.get('target_version');domain=body.get('domain','physical')
    if any(type(value) is not int or value<1 for value in (source_version,target_version)) or target_version<=source_version:raise ValueError('Target version advances the SAME source skill UUID')
    if domain not in ('simulation','physical'):raise ValueError('Explicit simulation/physical calibration scope required')
    wizard_id=_uuid(body.get('request_id') or str(uuid.uuid4()))
    payload=dict(wizard_id=wizard_id,skill_id=skill_id,source_version=source_version,target_version=target_version,domain=domain,
        experiment_slug=TRANSFER_SLUG,source_experiment_slug=SOURCE_SLUG,source_audit_sha256=digest(audit),source_profile=deepcopy(audit['source_profile']),source_requirements=deepcopy(audit.get('C',[])),
        A_resolved=deepcopy(audit['A']),automatic_motor_actions=False,controller_settings_modified=False)
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE');_append(db,'created',payload,PREFIX+'created:'+wizard_id);return _state(db,wizard_id)


def session(store,wizard_id):
    with store.connect() as db:return _state(db,wizard_id)


def _write_once(path,data):
    path.parent.mkdir(parents=True,exist_ok=True)
    if path.exists():
        if path.read_bytes()!=data:raise ValueError('Immutable calibration artifact changed')
    else:
        with path.open('xb') as stream:stream.write(data);stream.flush();os.fsync(stream.fileno())


def _image_quality(raw,metadata,measurement):
    reasons=[]
    try:
        image=Image.open(io.BytesIO(raw));image.load()
        if image.mode!='RGB' or image.size!=(metadata.get('width'),metadata.get('height')) or image.width*image.height>1920*1080:raise ValueError('camera image dimensions/identity')
        pixels=np.asarray(image,float);contrast=float(pixels.std());mean=float(pixels.mean())
        if contrast<8 or not 8<mean<247:reasons.append('FRAME_CONTRAST_LIGHTING_RETRY')
    except (OSError,ValueError,TypeError):reasons.append('ORIGINAL_RGB_FRAME_INVALID');contrast=mean=None
    if measurement.get('frame_sha256')!=sha(raw) or not measurement.get('calibration_id'):reasons.append('SOURCE_BOUND_METRIC_CALIBRATION_REQUIRED')
    if measurement.get('clock_domain')!=metadata.get('clock_domain') or measurement.get('boot_id')!=metadata.get('boot_id'):reasons.append('A_FRAME_STATE_CLOCK_MAPPING_REQUIRED')
    frame_tick,state_tick=metadata.get('monotonic_ns'),measurement.get('state_monotonic_ns')
    if type(frame_tick) is not int or type(state_tick) is not int or abs(frame_tick-state_tick)>100_000_000:reasons.append('FRAME_STATE_CAUSAL_ASSOCIATION_RETRY')
    if measurement.get('metric_geometry_verified') is not True:reasons.append('METRIC_GEOMETRY_UNCONFIRMED_NO_RGB_Z_GUESS')
    return dict(usable=not reasons,reasons=reasons,contrast=contrast,mean_intensity=mean,contact_or_force_inferred=False)


def capture_view(store,wizard_id,step_id,role,*,expected_revision,request_id,capture_provider,measurement_provider,clicks=None):
    request_id=_uuid(request_id)
    with store.connect() as db:
        state=_state(db,wizard_id);previous=next((row['payload'] for row in _records(db,wizard_id) if row['payload']['type']==PREFIX+'capture' and row['payload']['request_id']==request_id),None)
        if previous:return state
    if state['revision']!=expected_revision:raise ValueError('Wizard changed; refresh before capture')
    step=next((row for row in STEPS if row['id']==step_id),None)
    if state['domain']!='physical' or not step or state['active_step']!=step_id or role not in step['roles']:raise ValueError('Capture belongs to another active step/robot role')
    frame=capture_provider(role);raw=frame['bytes'];metadata=deepcopy(frame['metadata'])
    if not isinstance(raw,bytes) or len(raw)>10*1024**2:raise ValueError('Bounded original cached frame required')
    measurement=measurement_provider(step_id,role,raw,metadata,deepcopy(clicks or []),deepcopy(state));quality=_image_quality(raw,metadata,measurement)
    if step_id=='joint_reference_views' and measurement.get('source_calibration_sha256')!=state['source_profile']['source_calibration_sha256']:quality['usable']=False;quality['reasons'].append('SOURCE_CALIBRATION_CHANGED')
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE');current=_state(db,wizard_id)
        if current['revision']!=expected_revision:raise ValueError('Wizard changed while reading frame; no stale labels applied')
        if any(row['frame_sha256']==sha(raw) and row['step_id']==step_id and row['role']==role for row in current['captures']):raise ValueError('This original frame is already captured for this step/role')
        folder=store.root/'calibration'/'skill_transfer'/wizard_id;path=folder/(sha(raw)+'.jpg');_write_once(path,raw)
        payload=dict(wizard_id=wizard_id,request_id=request_id,step_id=step_id,role=role,frame_sha256=sha(raw),frame_file=str(path),frame_metadata=metadata,measurement=measurement,quality=quality,
            clicks=deepcopy(clicks or []),capture_source='existing_cached_provider_no_camera_owner',native_POST_executed=False)
        _append(db,'capture',payload,PREFIX+'capture:'+wizard_id+':'+request_id);return _state(db,wizard_id)


def solve_step(store,wizard_id,step_id,*,expected_revision,source_coordinates=None,source_chain=None):
    with store.connect() as db:state=_state(db,wizard_id)
    if state['revision']!=expected_revision or state['active_step']!=step_id:raise ValueError('Wizard changed or another step is active')
    captures=[row for row in state['captures'] if row['step_id']==step_id and row['quality']['usable']];views=[row['measurement'] for row in captures]
    if step_id=='joint_reference_views':
        if source_coordinates is None or source_chain is None:raise TransferBlocked('AUTOMATIC_SOURCE_GEOMETRY_PROVIDER_REQUIRED','A')
        if source_coordinates.sha256!=state['source_profile']['source_calibration_sha256'] or source_chain.source_sha256!=state['source_profile']['source_urdf_sha256']:raise TransferBlocked('SOURCE_GEOMETRY_VERSION_CHANGED','A')
        artifact=fit_joint_reference(source_coordinates,source_chain,views,provenance='measured')
    elif step_id=='gripper_scale_views':artifact=dict(schema='explorer_skill_transfer_gripper_pair_v1',source=fit_aperture_reference(views,role='source',provenance='measured'),target=fit_aperture_reference(views,role='target',provenance='measured'))
    elif step_id=='object_goal_views':artifact=register_task_frames(views,provenance='measured')
    else:raise ValueError('Unknown calibration step')
    raw=canonical(artifact);identifier='skill-transfer-'+sha(raw)[:24];path=store.root/'calibration'/'skill_transfer'/wizard_id/(identifier+'.json')
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE');current=_state(db,wizard_id)
        if current['revision']!=expected_revision:raise ValueError('Wizard changed while fitting; results not attached')
        _write_once(path,raw);_append(db,'solved',dict(wizard_id=wizard_id,step_id=step_id,artifact=dict(calibration_id=identifier,path=str(path),sha256=sha(raw),body=artifact),used_frame_sha256=[row['frame_sha256'] for row in captures],physical_execution_authorized=False),PREFIX+'solved:'+wizard_id+':'+step_id)
        return _state(db,wizard_id)
