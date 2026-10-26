"""ENGINEERING_GLUE: server-bound immutable SO101 cohort admission, shared by ACT/DP.

Importing this module loads no Torch/policy/robot/camera. Existing source grading
and split rules remain authoritative; a dataset's own boolean flags are no proof.
"""
import hashlib
import json
from pathlib import Path

from explorer_shared.contracts import digest
from explorer_shared.so101_station import NAMES,UNITS,LOW,HIGH

CONTRACT='so101_follower_calibrated_position6_v1'


def sha(path):
    with Path(path).open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()


def managed_file(store,path,expected):
    path=Path(path).resolve()
    if (not isinstance(expected,str) or len(expected)!=64 or any(char not in '0123456789abcdef' for char in expected)
            or not path.is_relative_to(Path(store.root).resolve()) or not path.is_file() or sha(path)!=expected):
        raise ValueError('Immutable SO101 source/checkpoint escaped managed state or changed SHA')
    return path


def member(folder,name):
    if not isinstance(name,str) or not name or Path(name).is_absolute() or '..' in Path(name).parts:
        raise ValueError('Retained dataset member must be relative within its immutable cohort')
    path=(Path(folder)/name).resolve()
    if not path.is_relative_to(Path(folder).resolve()):raise ValueError('Retained dataset member escaped its cohort')
    return path


def admission_sources():
    """Pin reused rules as well as this adapter in durable optimizer context."""
    root=Path(__file__).resolve().parents[3]
    return [Path(__file__),Path(__file__).with_name('lab_skill_workflow.py'),
        Path(__file__).with_name('skill_catalogue.py'),
        root/'01-autonomous-learning/src/explorer_sim/so101_dataset_qualification.py',
        root/'01-autonomous-learning/src/explorer_sim/station_adapter.py',
        root/'01-autonomous-learning/src/explorer_sim/dataset_export.py',
        root/'shared/explorer_shared/so101_station.py',root/'shared/explorer_shared/contracts.py',
        root/'shared/explorer_shared/experiments.py']


def verify_so101_aggregate(store,config):
    path=managed_file(store,config['aggregate_manifest'],config['aggregate_manifest_sha256'])
    outer=json.loads(path.read_text())
    if outer.get('schema')!='explorer_so101_skill_dataset_v1' or outer.get('action_contract_id')!=CONTRACT:
        raise ValueError('Native SO101 whole-skill aggregate required')
    if outer.get('action_names')!=list(NAMES) or outer.get('action_units')!=UNITS or any(config.get(key)!=outer.get(key) for key in ('skill_id','skill_version','spec_sha256')):
        raise ValueError('Source skill/version/native coordinate contract differs')
    if digest(outer['skill_spec'])!=outer['spec_sha256']:raise ValueError('Whole SkillSpec SHA changed')
    # Validate durable event payload integrity before reusing workflow decisions.
    with store.connect() as db:
        events=list(db.execute("SELECT event_id,sha256,body FROM events WHERE event_id LIKE 'lab.skill.%' OR event_id LIKE 'skill.catalogue.%' ORDER BY rowid"))
    receipts=[]
    for row in events:
        envelope=json.loads(row['body']);payload=envelope['payload']
        declared=payload.get('spec') if isinstance(payload.get('spec'),dict) else payload
        if declared.get('skill_id')==outer['skill_id'] and declared.get('skill_version',declared.get('version'))==outer['skill_version']:
            if row['sha256']!=digest(payload) or envelope.get('sha256')!=row['sha256'] or envelope.get('event_id')!=row['event_id']:
                raise ValueError('Durable server workflow event SHA changed')
            if payload.get('type')=='lab.skill.aggregate':receipts.append(payload)
    bound=[row for row in receipts if row.get('aggregate_manifest_sha256')==config['aggregate_manifest_sha256'] and row.get('spec_sha256')==outer['spec_sha256']]
    if not bound or not all(bound[-1].get(key) is True for key in ('full_teaching_provenance_ready','object_expert_eligible','global_split_groups_verified','official_dataset_readback_verified')):
        raise ValueError('Server-bound accepted whole-skill aggregate receipt required; raw/hold archives stay analysis-only')
    if not isinstance(outer.get('receipt'),dict) or any(bound[-1].get(key)!=value for key,value in outer['receipt'].items()):
        raise ValueError('Dataset flags differ from its durable server receipt')
    from . import lab_skill_workflow as flow
    selected=flow._chosen(store,outer['skill_id'],outer['skill_version'])
    if not selected['confirmed'] or selected['spec_sha256']!=outer['spec_sha256'] or selected['spec']!=outer['skill_spec']:
        raise ValueError('Confirmed original whole SkillSpec binding differs')
    files=outer.get('source_files_sha256')
    if not isinstance(files,dict) or not files:raise ValueError('Complete immutable source artifact SHA map required')
    for name,expected in files.items():managed_file(store,member(path.parent,name),expected)
    review_name='source-reviews.json'
    if review_name not in files:raise ValueError('Immutable original binding/review snapshots required')
    snapshots=json.loads(member(path.parent,review_name).read_text())
    decisions,_=flow._decisions(store,selected['spec']);accepted=[row for row in decisions if row['accepted']]
    # JPEG metadata/encoding differences must not hide identical decoded frames.
    from PIL import Image
    decoded_splits={}
    for decision in accepted:
        for frame in decision['evidence']['source_files']:
            with Image.open(frame['path']) as image:
                rgb=image.convert('RGB');content=hashlib.sha256(str(rgb.size).encode()+rgb.tobytes()).hexdigest()
            split=decision['binding']['split']
            if content in decoded_splits and decoded_splits[content]!=split:raise ValueError('Decoded original RGB content crosses dataset splits')
            decoded_splits[content]=split
    current=[dict(binding=row['binding'],review=row['review']) for row in accepted]
    order=lambda row:row['binding']['original_episode_id']
    if not isinstance(snapshots,list) or sorted(snapshots,key=order)!=sorted(current,key=order):
        raise ValueError('Original source/binding/review admission changed; rebuild the accepted cohort')
    fingerprint=flow._cohort_fingerprint(outer['spec_sha256'],accepted)
    if fingerprint!=bound[-1].get('cohort_fingerprint'):raise ValueError('Accepted cohort fingerprint differs')
    expected_ids={split:{row['binding']['original_episode_id'] for row in accepted if row['binding']['split']==split} for split in ('train','development','sealed_test')}
    for split,key in (('train','accepted_train_original_episode_ids'),('development','accepted_development_original_episode_ids'),('sealed_test','sealed_original_episode_ids')):
        values=bound[-1].get(key)
        if not isinstance(values,list) or len(values)!=len(set(values)) or set(values)!=expected_ids[split]:
            raise ValueError('Original whole-episode split receipt differs')
    view_name=outer['internal_view'];view_path=managed_file(store,member(path.parent,view_name),files[view_name]);view=json.loads(view_path.read_text())
    if view.get('state_basis')!='measured_state' or view.get('action_basis')!='dispatched_command' or view.get('action_normalization')!={'low':LOW,'high':HIGH}:
        raise ValueError('Measured follower6 and original dispatched calibrated native6 labels required')
    if view.get('action_names')!=list(NAMES) or view.get('action_units')!=UNITS:raise ValueError('Internal view native names/units changed')
    episodes=view.get('episodes') or [];seen={split:set() for split in ('train','development')}
    import numpy as np
    for descriptor in episodes:
        split=descriptor.get('split');identity=descriptor.get('source_episode_id')
        if split not in seen or identity not in expected_ids[split]:raise ValueError('Sealed/foreign original payload cannot enter the model dataset')
        seen[split].add(identity);episode=member(view_path.parent,descriptor['file'])
        relative=episode.relative_to(path.parent).as_posix()
        if files.get(relative)!=descriptor['sha256']:raise ValueError('Model episode is not in the retained source SHA map')
        with np.load(episode,allow_pickle=False) as data:
            if not {'state_native','action_native','rgb'}<=set(data.files):raise ValueError('No reconstruction of missing measured native state or issued command')
    if any(not expected_ids[split] or seen[split]!=expected_ids[split] for split in seen):raise ValueError('All accepted train/development originals must remain in the model dataset')
    official=member(path.parent,outer['official_dataset']);provenance=official/'explorer_provenance.json'
    if provenance.relative_to(path.parent).as_posix() not in files:raise ValueError('Official dataset provenance must be immutable and retained')
    if sha(path)!=config['aggregate_manifest_sha256']:raise ValueError('Accepted aggregate changed during admission')
    return path,outer,view_path,view
