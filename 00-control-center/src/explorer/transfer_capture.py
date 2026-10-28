"""Thin06 DATA lifecycle over the existing native recorder/review/import queue.

No controller calls or device owners. Durable intents precede data writes; only
an exact known context can reconcile a missing start/finish HTTP reply.
"""
from contextlib import contextmanager
from copy import deepcopy
import fcntl
import json
import time
import uuid

from explorer_shared.contracts import canonical,digest,event
from explorer_shared.native_capture_identity import validate_native_capture_start,validate_native_capture_finish
from . import transfer_target as target
from .native_capture_approval import validate_source_approval
from .transfer_target_import import IMPORT_KIND,build_aggregate

PREFIX='skill_transfer.target_capture.'


@contextmanager
def _lock(store):
    with (store.root/'.target-capture-data.lock').open('a+b') as held:
        fcntl.flock(held,fcntl.LOCK_EX)
        yield


def _rows(store):
    with store.connect() as db:
        result={}
        for row in db.execute("SELECT body FROM events WHERE json_extract(body,'$.payload.type')=? ORDER BY received,event_id",(PREFIX+'state',)):
            document=json.loads(row['body'])
            if digest(document['payload'])!=document['sha256']:raise ValueError('Target capture journal changed')
            value=document['payload'];result[value['capture_id']]=value
        return result


def _save(store,row,**changes):
    payload=dict(deepcopy(row),**changes,type=PREFIX+'state')
    payload['revision']=row.get('revision',0)+1
    document=event('nx','06',payload)
    document['event_id']=PREFIX+digest(payload)
    with store.connect() as db:
        db.execute('INSERT OR IGNORE INTO events VALUES (?,?,?,?,?)',(document['event_id'],'nx',time.time(),document['sha256'],canonical(document).decode()))
    return payload


def _known(store,wizard_id,capture_id,actor):
    row=_rows(store).get(capture_id)
    if not row or row['wizard_id']!=wizard_id or row['actor_id']!=actor:raise ValueError('Known target capture and original actor required')
    return row


def _reconcile(store,row,nano):
    if row['state']=='START_PENDING':
        packet=nano.status()
        identity=validate_native_capture_start(dict(skill_context=row['context']),row['source_identity'],packet)
        row=_save(store,row,state='RECORDING',recording_id=identity)
    if row['state']=='FINISH_PENDING':
        manifest=nano.manifest(row['recording_id'])
        validate_native_capture_finish(dict(skill_context=row['context']),row['source_identity'],row['recording_id'],manifest,row['outcome'])
        row=_save(store,row,state='SEALED',admission=manifest.get('admission',{}))
    return row


def capture_list(store,wizard_id,actor):
    wizard,model=target.source_for_wizard(store,wizard_id)
    rows=[row for row in _rows(store).values() if row['wizard_id']==wizard_id and row['actor_id']==actor]
    imports=[job for job in store.jobs() if job['kind']==IMPORT_KIND and job['project_id']=='06' and job['config'].get('wizard_id')==wizard_id]
    return dict(wizard_id=wizard_id,source_model_binding=wizard['source_model_binding'],captures=rows,imports=imports,
        phases=deepcopy(model['manifest']['skill_spec']['stages']),
        physical_execution_authorized=False,controller_settings_written=False)


def start(store,wizard_id,metadata,actor,nano):
    context=target.prepare_capture_context(store,wizard_id,metadata,actor=actor);identity=context['capture_id']
    with _lock(store):
        previous=_rows(store).get(identity)
        if previous:
            if previous['context']!=context or previous['actor_id']!=actor:raise ValueError('Conflicting target capture request')
            return _reconcile(store,previous,nano)
        if any(row['state'] not in ('SEALED',) for row in _rows(store).values()):raise ValueError('Finish or reconcile the known target recording first')
        packet=nano.status();stamp=packet.get('capture') or {}
        if packet.get('active_recording_id') is not None or stamp.get('state') not in ('IDLE','SEALED'):
            raise ValueError('Existing native recorder must be idle; no foreign recording adoption')
        source=dict(clock_domain=stamp.get('source_clock_domain'),boot_id=stamp.get('source_boot_id'))
        if not all(isinstance(value,str) and value for value in source.values()):raise ValueError('Known native DATA clock and boot required')
        # Same read-only original reference preflight; no HOME, grant or motion.
        nano.reference()
        row=_save(store,dict(capture_id=identity,wizard_id=wizard_id,actor_id=actor,context=context,
            source_identity=source,state='START_PENDING',recording_id=None))
        reply=nano.start_recording(context)
        recording=validate_native_capture_start(dict(skill_context=context),source,dict(capture=reply,active_recording_id=reply.get('recording_id')))
        return _save(store,row,state='RECORDING',recording_id=recording)


def operation(store,wizard_id,capture_id,body,actor,nano):
    if not isinstance(body,dict) or 'operation' not in body:raise ValueError('Explicit recording operation required')
    action=body['operation']
    with _lock(store):
        row=_known(store,wizard_id,capture_id,actor)
        if action=='refresh':
            if set(body)!={'operation'}:raise ValueError('Fixed refresh request')
            return _reconcile(store,row,nano)
        if action in ('pause','resume','stage'):
            if row['state'] not in ('RECORDING','PAUSED'):raise ValueError('Known active target capture required')
            payload={}
            if action=='stage':
                if set(body)!={'operation','phase_id'} or body['phase_id'] not in row['context']['target_phase_ids']:raise ValueError('Choose an original task phase')
                payload={'phase_id':body['phase_id']}
            elif set(body)!={'operation'}:raise ValueError('Fixed pause/resume request')
            validate_native_capture_start(dict(skill_context=row['context']),row['source_identity'],nano.status())
            nano.record_operation(action,row['recording_id'],payload)
            return _save(store,row,state='PAUSED' if action=='pause' else 'RECORDING' if action=='resume' else row['state'])
        if action=='finish':
            if set(body)!={'operation','operator_outcome','per_phase'} or body['operator_outcome'] not in ('success','failure','unknown'):
                raise ValueError('Explicit outcome and all phase outcomes required')
            phases=body['per_phase']
            if not isinstance(phases,dict) or set(phases)!=set(row['context']['target_phase_ids']) or any(value not in ('success','failure','unknown') for value in phases.values()):
                raise ValueError('Report each original phase; do not infer success')
            def report(value):return dict(status=value,method='operator_observation',observer_id=actor,independent_of_action_commands=True,metrics={})
            outcome=dict(recording_id=row['recording_id'],operator_outcome=body['operator_outcome'],
                outcomes=dict(end_to_end=report(body['operator_outcome']),per_phase={phase:report(value) for phase,value in phases.items()}))
            if row['state'] in ('FINISH_PENDING','SEALED'):
                if row.get('outcome')!=outcome:raise ValueError('Original finish outcome already recorded')
                return _reconcile(store,row,nano)
            if row['state'] not in ('RECORDING','PAUSED'):raise ValueError('Known recording required')
            validate_native_capture_start(dict(skill_context=row['context']),row['source_identity'],nano.status())
            row=_save(store,row,state='FINISH_PENDING',outcome=outcome)
            nano.record_operation('finish',row['recording_id'],dict(outcome_evidence=outcome))
            return row
        if action=='review':
            if set(body)!={'operation','decision'} or body['decision'] not in ('approve','reject') or row['state']!='SEALED':raise ValueError('Review a sealed original separately')
            status=nano.status();ready=next((value for value in status.get('ready_recordings',[]) if value['recording_id']==row['recording_id']),None)
            if not ready:raise ValueError('Wait for original archive seal')
            wrapper=nano.record_operation('review',row['recording_id'],dict(decision=body['decision'],actor_id=actor,expected_archive_sha256=ready['archive_sha256']))
            checked=validate_source_approval(wrapper,row['recording_id'],ready['archive_sha256'],actor=actor,require_approved=False)
            from .transfer_target_import import note_source_review
            for job in store.jobs():
                if (job['kind'],job['project_id'],job['status'])==(IMPORT_KIND,'06','completed') and job['config'].get('episode_id')==row['recording_id']:
                    note_source_review(store,wizard_id,job['id'],wrapper,actor=actor)
            return _save(store,row,review=wrapper,decision=checked['decision'],archive_sha256=ready['archive_sha256'],review_sha256=checked['review_sha256'])
        if action=='import':
            if set(body)!={'operation'} or row['state']!='SEALED' or row.get('decision')!='approve':raise ValueError('Separately approved original required')
            validate_source_approval(nano.review_state(row['recording_id']),row['recording_id'],row['archive_sha256'],expected_review_sha256=row['review_sha256'])
            settings=dict(episode_id=row['recording_id'],source_format='native_capture_v1',archive_sha256=row['archive_sha256'],
                source_review_sha256=row['review_sha256'],source_capture_context=row['context'],wizard_id=wizard_id)
            old=next((job for job in store.jobs() if job['kind']==IMPORT_KIND and job['project_id']=='06' and job['config']==settings),None)
            return old or store.create_job(IMPORT_KIND,'06',settings)
        raise ValueError('Fixed DATA recording operation required')


def aggregate(store,wizard_id,import_ids,actor):
    wizard,_=target.source_for_wizard(store,wizard_id)
    if wizard.get('created_by_actor')!=actor:raise ValueError('Original wizard actor required')
    with _lock(store):
        result=build_aggregate(store,wizard_id,import_ids)
        old=next((job for job in store.jobs() if job['kind']==target.DATASET_KIND and job['project_id']=='06' and job.get('result')==result),None)
        if old:return old
        identity=uuid.uuid4().hex;now=time.time()
        with store.connect() as db:
            db.execute('INSERT INTO jobs(id,kind,project_id,status,created,updated,config,result) VALUES (?,?,?,?,?,?,?,?)',
                (identity,target.DATASET_KIND,'06','completed',now,now,json.dumps(dict(wizard_id=wizard_id,import_job_ids=import_ids)),json.dumps(result)))
            from .experiment_projection import record_runtime_identity
            record_runtime_identity(db,'job',identity,'06')
        return store.job(identity)
