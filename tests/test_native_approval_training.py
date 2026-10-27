"""Rejected/revised Source review blocks worker fit without rewriting originals."""
from copy import deepcopy
import hashlib
import json

import pytest

from explorer import skill_workflow as flow
from explorer.native_capture_approval import source_bytes
from explorer.native_capture_cohort import validate_current_approvals
from explorer_shared.contracts import canonical
from explorer_shared.learning.rosmaster_skill_pipeline import CanonicalCorpus,train_rosmaster_skill
from explorer_shared.learning.rosmaster_skill_features import PipelineBlocked
from test_rosmaster_skill_pipeline import fixture_aggregate


def test_aggregate_source_approval_semantics_rechecked_not_just_payload_sha(tmp_path):
    path,store=fixture_aggregate(tmp_path,native02=True);corpus=CanonicalCorpus(path)
    assert len(corpus.train)==3 and all(row['descriptor']['source_approval_required'] for row in corpus.episodes)
    manifest=json.loads(path.read_bytes());descriptor=manifest['episodes'][0];file=path.parent/descriptor['source_approval_file']
    wrapper=json.loads(file.read_bytes());wrapper['source_review']['decision']='reject';wrapper['transfer_ready']=False
    wrapper['review_sha256']=hashlib.sha256(source_bytes(wrapper['source_review'])).hexdigest()
    file.write_bytes(source_bytes(wrapper));manifest['source_files_sha256'][descriptor['source_approval_file']]=hashlib.sha256(file.read_bytes()).hexdigest()
    descriptor['source_review_sha256']=wrapper['review_sha256'];path.write_bytes(canonical(manifest))
    with pytest.raises(PipelineBlocked,match='NATIVE_SOURCE_APPROVAL_NOT_VALID'):CanonicalCorpus(path)


def test_bound_pause_segments_flow_to_corpus_without_crossing_or_inflating_originals(tmp_path):
    path,store=fixture_aggregate(tmp_path,native02=True,paused=True);corpus=CanonicalCorpus(path)
    assert len(corpus.train)==3 and len(corpus.development)==1 and len(corpus.sealed_ids)==1
    assert all([frame['recording_segment_id'] for frame in row['frames']]==[0]*5+[1]*5 for row in corpus.episodes)
    from explorer_shared.learning.rosmaster_skill_segments import chunk_slots
    assert chunk_slots(corpus.train[0]['frames'],4,3)==([4,4,4],[False,True,True])


def test_current_central_reject_after_queue_blocks_actual_worker_before_train_entry(tmp_path,monkeypatch):
    path,store=fixture_aggregate(tmp_path,native02=True);manifest=json.loads(path.read_bytes());descriptor=manifest['episodes'][0]
    wrapper=json.loads((path.parent/descriptor['source_approval_file']).read_bytes());original=descriptor['source_episode_id']
    selected={'spec':manifest['skill_spec']};identity=manifest['skill_id'];variant=descriptor['variant_id']
    session=flow.create_session(store,identity,1,{'split':descriptor['split'],'scene':descriptor['scene']})
    capture=flow.begin_capture(store,identity,1,variant,session['session_id'],stationary_chassis_confirmed=True)
    flow.capture_started(store,capture['capture_id'],original)
    flow.capture_finished(store,capture['capture_id'],original,{'outcome':'success','completed_stages':list(flow.MANIPULATION_STAGES),
        'full_task_confirmed':True,'object_and_goal_visible':True})
    flow.capture_reviewed(store,capture['capture_id'],original,wrapper,authenticated_actor=wrapper['source_review']['actor_id'])
    assert validate_current_approvals(store,manifest)
    lineage={'skill_id':identity,'skill_version':1,'spec_sha256':manifest['skill_spec_sha256'],'aggregate_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
        'training_original_episode_ids':[row['source_episode_id'] for row in manifest['episodes'] if row['split']=='train'],
        'development_original_episode_ids':[row['source_episode_id'] for row in manifest['episodes'] if row['split']=='development'],
        'sealed_original_episode_ids':[row['original_episode_id'] for row in manifest['sealed_sources_excluded']]}
    job=store.create_job('rosmaster_skill_train','02',{'dataset_manifest':str(path),'skill_workflow':lineage})
    rejected=deepcopy(wrapper);rejected['source_review'].update(decision='reject',previous_review_sha256=wrapper['review_sha256'],review_id='d'*32)
    rejected['review_sha256']=hashlib.sha256(source_bytes(rejected['source_review'])).hexdigest();rejected['transfer_ready']=False
    flow.capture_reviewed(store,capture['capture_id'],original,rejected,authenticated_actor=wrapper['source_review']['actor_id'])
    before={name:hashlib.sha256(open(name,'rb').read()).hexdigest() for name in CanonicalCorpus(path).source_files}
    def forbidden(*args,**kwargs):raise AssertionError('No optimizer/model/export after withdrawn review')
    monkeypatch.setattr('explorer_shared.learning.rosmaster_skill_pipeline.train_pipeline',forbidden)
    with pytest.raises(ValueError,match='review|approved'):train_rosmaster_skill(store,job)
    assert all(hashlib.sha256(open(name,'rb').read()).hexdigest()==checksum for name,checksum in before.items())
