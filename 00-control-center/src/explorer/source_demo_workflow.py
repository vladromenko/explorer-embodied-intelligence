"""ENGINEERING_GLUE: thin human-demo intents over the existing04 skill workflow."""
from copy import deepcopy
import re
import uuid

from . import lab_skill_workflow as lab

ID=re.compile('[0-9a-f]{32}')


def prepare(store,body,actor):
    if (not isinstance(body,dict) or set(body)!={'skill_id','skill_version','context','request_id'} or
            not isinstance(body['request_id'],str) or not ID.fullmatch(body['request_id']) or
            type(body['skill_version']) is not int or body['skill_version']<1 or not isinstance(body['context'],dict) or
            set(body['context'])-{'variant_id','session_id','object_instance_id','name','episode_scope'} or
            not isinstance(actor,str) or not actor):
        raise ValueError('Select confirmed source skill/version/session/variant and a preparation request identity')
    context=lab.capture_context(store,body['skill_id'],body['skill_version'],body['context'])['record_start_body']
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        previous=next((row for row in lab._records(db) if row.get('type')==lab.PREFIX+'demo_attempt' and row['request_id']==body['request_id']),None)
        if previous:
            if previous['request']!=body or previous['actor_id']!=actor:raise ValueError('Immutable demonstration preparation request changed')
            return deepcopy(previous)
        identity=uuid.uuid4().hex
        selected=dict(purpose='source04_demo',input_mode='leader',source_domain='PHYSICAL',
            skill_id=context['skill_id'],skill_version=context['skill_version'],spec_sha256=context['skill_spec_sha256'],
            action_contract_id=lab.CONTRACT,source_session_id=uuid.UUID(context['session_id']).hex,demonstration_id=identity)
        return lab._append(db,'demo_attempt',dict(demonstration_id=identity,actor_id=actor,request_id=body['request_id'],
            request=deepcopy(body),record_start_body=context,binding=selected,control_started=False),body['request_id'])


def attempt(store,identity,actor):
    if not isinstance(identity,str) or not ID.fullmatch(identity):raise ValueError('Choose an existing server demonstration attempt')
    with store.connect() as db:
        row=next((value for value in lab._records(db) if value.get('type')==lab.PREFIX+'demo_attempt' and value['demonstration_id']==identity),None)
    if row is None or row['actor_id']!=actor:raise ValueError('Demonstration belongs to another authenticated observer')
    chosen=lab._chosen(store,row['binding']['skill_id'],row['binding']['skill_version'])
    if not chosen['confirmed'] or chosen['spec_sha256']!=row['binding']['spec_sha256']:
        raise ValueError('Original confirmed demonstration spec changed')
    return row


def record_return(store,row,kind,result):
    if kind not in ('control_return','capture_return'):raise ValueError('Known source-return metadata kind required')
    with store.connect() as db:
        return lab._append(db,kind,dict(demonstration_id=row['demonstration_id'],actor_id=row['actor_id'],
            binding=row['binding'],source_result=deepcopy(result)))


def capture_identity(store,row):
    with store.connect() as db:
        values=[value for value in lab._records(db) if value.get('type')==lab.PREFIX+'capture_return' and value['demonstration_id']==row['demonstration_id']]
    if not values:raise ValueError('No acknowledged source recording for this demonstration')
    return values[-1]['source_result']['recording_id']


def listing(store,actor):
    with store.connect() as db:values=lab._records(db)
    return [deepcopy(row) for row in values if row.get('type')==lab.PREFIX+'demo_attempt' and row['actor_id']==actor]
