"""ENGINEERING_GLUE: correlate Source04 recording acknowledgements, never control.

One durable start intent precedes Source IO. Explicit recovery may acknowledge
only that intent's original known writer. Reads never adopt a recording/grant.
"""
from copy import deepcopy

from explorer_shared.so101_station import _record_context
from . import source_demo_workflow as flow


def _rows(db,row,kind):
    return [value for value in flow.lab._records(db) if value.get('type')==flow.lab.PREFIX+kind and
        value.get('demonstration_id')==row['demonstration_id'] and value.get('actor_id')==row['actor_id']]


def projection(store,row):
    """Persisted DATA acknowledgement only; no polled source ownership."""
    with store.connect() as db:
        captures=_rows(db,row,'capture_return');intents=_rows(db,row,'demo_capture_start_intent')
    ident=captures[-1]['source_result']['recording_id'] if captures else None
    if ident is not None and not flow.ID.fullmatch(str(ident)):raise ValueError('Saved source recording identity changed')
    return {'acknowledged_recording_id':ident,'capture_start_pending':bool(intents and not captures),
        'control_authority_adopted':False}


def begin(store,row,body):
    if not isinstance(body,dict) or set(body)!={'grant_id','expected_control_epoch'}:
        raise ValueError('Exact acknowledged demo grant/returned epoch required')
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        if _rows(db,row,'capture_return'):raise ValueError('This demonstration already has an acknowledged recording; prepare another demonstration')
        if _rows(db,row,'demo_capture_start_intent'):
            raise ValueError('Capture start is already pending; explicitly reconcile its data identity before retrying')
        controls=_rows(db,row,'control_return')
        grant=(controls[-1]['source_result'].get('grant') or {}) if controls else {}
        if (grant.get('binding')!=row['binding'] or grant.get('actor_id')!=row['actor_id'] or
                grant.get('native_mode')!='teleoperation' or grant.get('execution_permission') is not True or
                grant.get('grant_id')!=body['grant_id'] or grant.get('control_epoch')!=body['expected_control_epoch'] or
                not all(grant.get(key) for key in ('boot_id','clock_domain','leader_calibration_sha256','follower_calibration_sha256'))):
            raise ValueError('Capture needs the server-acknowledged original teleoperation grant/epoch')
        return flow.lab._append(db,'demo_capture_start_intent',dict(demonstration_id=row['demonstration_id'],
            actor_id=row['actor_id'],binding=deepcopy(row['binding']),record_start_body=deepcopy(row['record_start_body']),
            original_grant=deepcopy(grant)),row['demonstration_id'])


def validate_source(store,row,packet,*,native=None,source_stamp=None):
    """Check actual source identity; grant is historical provenance, never reused."""
    with store.connect() as db:intents=_rows(db,row,'demo_capture_start_intent')
    if len(intents)!=1:raise ValueError('Exact durable source capture start intent required')
    intent=intents[0];grant=intent['original_grant'];capture=(packet or {}).get('capture') or {}
    ident=capture.get('recording_id')
    if (not isinstance(ident,str) or not flow.ID.fullmatch(ident) or packet.get('recovery_required') is not False or
            packet.get('closed') is not False or packet.get('schema')!='explorer_so101_source_capture_v1' or
            packet.get('recording_id')!=ident or capture.get('state') not in ('RECORDING','PAUSED') or
            capture.get('actor_id')!=row['actor_id'] or intent['binding']!=row['binding'] or
            intent['record_start_body']!=row['record_start_body'] or capture.get('native_control_epoch')!=grant['control_epoch'] or
            capture.get('source_boot_id')!=grant['boot_id'] or capture.get('source_clock_domain')!=grant['clock_domain'] or
            capture.get('calibration_sha256')!={'leader':grant['leader_calibration_sha256'],'follower':grant['follower_calibration_sha256']} or
            (capture.get('original_start_receipt') or {}).get('episode_id')!=ident):
        raise ValueError('Source capture start identity is foreign, uncertain or changed; no adoption')
    body=deepcopy(row['record_start_body']);context=dict(body.get('context') or {})
    context['source04_demo_permission']={'binding':row['binding'],'grant_id':grant['grant_id'],'control_epoch':grant['control_epoch']}
    body['context']=context;expected=_record_context(body)
    if flow.lab.canonical(capture.get('source_context'))!=flow.lab.canonical(expected):
        raise ValueError('Source capture original demonstration context differs from the saved start intent')
    if native is not None:
        original=native.get('recording') or {};original_context=deepcopy(original.get('source_context') or {})
        original_context.pop('native_phase_annotation',None)
        roles=native.get('roles') or {}
        if (original.get('episode_id')!=ident or original.get('boot_id')!=grant['boot_id'] or
                original.get('clock_domain')!=grant['clock_domain'] or original.get('demonstration_actor')!=row['actor_id'][:80] or
                flow.lab.canonical(original_context)!=flow.lab.canonical(expected) or
                any((roles.get(role) or {}).get('calibration_sha256')!=grant[role+'_calibration_sha256'] for role in ('leader','follower')) or
                not isinstance(source_stamp,dict) or source_stamp.get('boot_id')!=grant['boot_id'] or source_stamp.get('clock_domain')!=grant['clock_domain']):
            raise ValueError('Original source writer/boot/calibration no longer matches the capture intent')
    return ident


def acknowledge(store,row,packet,*,native=None,source_stamp=None):
    ident=validate_source(store,row,packet,native=native,source_stamp=source_stamp)
    saved=projection(store,row)['acknowledged_recording_id']
    if saved is not None and saved!=ident:raise ValueError('Another recording was already acknowledged for this demonstration')
    if saved is None:flow.record_return(store,row,'capture_return',{'recording_id':ident})
    return {'recording_id':ident,'data_recording_acknowledged':True,'control_authority_adopted':False,
        'native_commands_dispatched':False}
