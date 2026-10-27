"""ENGINEERING_GLUE: thin human-demo intents over the existing04 skill workflow."""
from copy import deepcopy
from explorer_shared.native_capture_approval import validate_source_approval
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


def observe_source_review(store,recording_id,archive_sha256,wrapper):
    """Remember latest authenticated Source metadata, including late rejection.

    This is data approval only. It cannot grant control or infer task success.
    Missing intermediate reviews are allowed only with a later same-boot clock;
    a direct predecessor link also orders reviews within one clock tick.
    """
    checked=validate_source_approval(wrapper,recording_id,archive_sha256,require_approved=False)
    exact={key:deepcopy(wrapper[key]) for key in ('source_review','review_sha256','transfer_ready')}
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        old=lab._latest(lab._records(db),'source_review_observed','recording_id').get(recording_id)
        if old:
            prior=old['wrapper'];a,b=prior['source_review'],exact['source_review']
            if old['archive_sha256']!=archive_sha256:raise ValueError('Immutable source04 archive changed')
            if prior==exact:return checked
            if (a['source_boot_id'],a['source_clock_domain'])!=(b['source_boot_id'],b['source_clock_domain']):
                raise ValueError('Source review clock/boot changed; explicit recovery required')
            if b['reviewed_native_ns']<a['reviewed_native_ns'] or (b['previous_review_sha256']!=prior['review_sha256'] and b['reviewed_native_ns']<=a['reviewed_native_ns']):
                raise ValueError('Stale source04 review cannot replace the current decision')
            # A previously observed revision cannot become latest again.
            if any(row.get('type')==lab.PREFIX+'source_review_observed' and row.get('recording_id')==recording_id and
                   row['wrapper']['review_sha256']==exact['review_sha256'] for row in lab._records(db)):
                raise ValueError('Stale source04 review revision cannot be replayed')
        lab._append(db,'source_review_observed',dict(recording_id=recording_id,archive_sha256=archive_sha256,wrapper=exact),recording_id+':'+exact['review_sha256'])
    return checked


def current_source_review(store,recording_id,archive_sha256,review_sha256):
    with store.connect() as db:
        row=lab._latest(lab._records(db),'source_review_observed','recording_id').get(recording_id)
    if row is None or row['archive_sha256']!=archive_sha256:raise ValueError('Current source04 review unavailable')
    return validate_source_approval(row['wrapper'],recording_id,archive_sha256,expected_review_sha256=review_sha256)


def queue_source_review(store,recording_id,archive_sha256,wrapper):
    checked=observe_source_review(store,recording_id,archive_sha256,wrapper)
    if not checked['transfer_ready']:return None
    return store.create_import_job('station_import','04',recording_id,archive_sha256,
        source_format='so101_source_capture_v1',source_review_sha256=checked['review_sha256'])


def source_capture_evidence(store,job):
    """Reverify immutable import pins AND latest known Source decision for admission."""
    import json
    config=job['config'];result=job.get('result') or {}
    if (job['kind'],job['project_id'],config.get('source_format'))!=('station_import','04','so101_source_capture_v1'):
        raise ValueError('Exact source04 import required')
    current=current_source_review(store,config['episode_id'],config['archive_sha256'],config['source_review_sha256'])
    expected={'source_manifest_path':'source_manifest_sha256','capture_manifest_path':'capture_manifest_sha256',
        'source_archive_path':'source_archive_sha256','source_approval_path':'source_approval_file_sha256',
        'provenance_path':'provenance_sha256'}
    paths={}
    for name,key in expected.items():
        path=lab._local(store,result.get(name))
        if lab._sha(path)!=result.get(key):raise ValueError('Imported source04 artifact SHA changed: '+name)
        paths[name]=path
    if (result.get('source_format')!='so101_source_capture_v1' or result.get('source_archive_sha256')!=config['archive_sha256'] or
            result.get('source_review_sha256')!=config['source_review_sha256']):raise ValueError('Imported source04 producer/review pins changed')
    wrapper=json.loads(paths['source_approval_path'].read_bytes())
    approved=validate_source_approval(wrapper,config['episode_id'],config['archive_sha256'],expected_review_sha256=config['source_review_sha256'])
    if approved!=current or wrapper!=result.get('source_approval'):raise ValueError('Imported source04 approval differs from current Source')
    provenance=json.loads(paths['provenance_path'].read_bytes());base=paths['provenance_path'].parent
    if (provenance.get('schema')!='explorer_so101_source_capture_import_v1' or provenance.get('recording_id')!=config['episode_id'] or
            provenance.get('source_archive_sha256')!=config['archive_sha256'] or provenance.get('source_review_sha256')!=config['source_review_sha256']):
        raise ValueError('Source04 immutable import provenance differs')
    for name,checksum in provenance['source_files_sha256'].items():
        path=lab._local(store,base/name)
        if not path.is_relative_to(base) or lab._sha(path)!=checksum:raise ValueError('Source04 retained original SHA changed')
    return dict(capture_manifest_path=str(paths['capture_manifest_path']),capture_manifest_sha256=result['capture_manifest_sha256'],
        source_review_sha256=config['source_review_sha256'],source_archive_sha256=config['archive_sha256'],
        import_provenance_sha256=result['provenance_sha256'],source_approval=approved)
