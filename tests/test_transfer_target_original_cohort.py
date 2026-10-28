"""Actual original shared native9 writer/import/approval/aggregate, GENERATED only."""
from copy import deepcopy
from pathlib import Path
import uuid

import pytest

from explorer import transfer_target as target
from explorer import transfer_target_import as cohort
from explorer_shared.learning.artifacts import sha
from explorer_shared.learning.rosmaster_skill_pipeline import CanonicalCorpus
from explorer_shared.native_capture_identity import native_context_sha256,normalize_capture_context
from test_transfer_target_native_capture import context_fixture
from test_rosmaster_native02_e2e import native_aggregate


def original_target_cohort(tmp_path,monkeypatch,*,store=None,wizard=None):
    if store is None:store,wizard,_,_=context_fixture(tmp_path,monkeypatch)
    source=target.source_for_wizard(store,wizard['wizard_id'])[1]['manifest']['skill_spec']
    variants=[dict(variant_id='GENERATED-'+str(i),split='train' if i<3 else 'development' if i==3 else 'sealed_test') for i in range(5)]
    selected=dict(spec=dict(skill_id=wizard['skill_id'],version=wizard['target_version'],stages=deepcopy(source['stages']),coverage_plan=dict(variants=variants)))
    from explorer_shared.contracts import digest
    selected['spec_sha256']=digest(selected['spec'])
    contexts=[];imports=[]
    def session(index,variant):return dict(session_id=str(uuid.uuid4()))
    def prepare(index,variant,session,unused):
        metadata=dict(request_id=str(uuid.uuid4()),session_id=session['session_id'],partition=variant['split'],
            scene_id='GENERATED-scene-'+variant['split'],object_instance_id='GENERATED-object-'+str(index),
            group_id='GENERATED-group-'+str(index),variant_id=variant['variant_id'],phase_id=source['stages'][0]['id'])
        context=target.prepare_capture_context(store,wizard['wizard_id'],metadata,actor='SOFTWARE-FIXTURE-OWNER')
        contexts.append(context);return normalize_capture_context(context)
    def job_factory(identity,archive,review):
        return store.create_job(cohort.IMPORT_KIND,'06',dict(episode_id=identity,source_format='native_capture_v1',
            archive_sha256=archive,source_review_sha256=review,wizard_id=wizard['wizard_id'],source_capture_context=deepcopy(contexts[-1])))
    def imported(job,variant,session):
        cohort.note_source_review(store,wizard['wizard_id'],job['id'],job['result']['source_approval'],actor='SOFTWARE-FIXTURE-OWNER')
        imports.append(job['id'])
    path,_,evidence=native_aggregate(tmp_path/'native-originals',monkeypatch,store=store,selected=selected,
        session_for_variant=session,prepare_context=prepare,job_factory=job_factory,importer=cohort.run_import,
        on_import=imported,finalize=lambda:cohort.build_aggregate(store,wizard['wizard_id'],imports))
    return store,wizard,path,imports,evidence


def test_shared_original06_capture_approval_import_cohort_keeps_domains_splits_and_full9(tmp_path,monkeypatch):
    store,wizard,path,imports,evidence=original_target_cohort(tmp_path,monkeypatch);corpus=CanonicalCorpus(path)
    assert len(corpus.train)==3 and len(corpus.development)==1 and len(corpus.sealed_ids)==1
    assert set(evidence['request_methods'])=={'GET'} and evidence['missing_real_demonstrations']
    assert all(sha(name)==expected for name,expected in evidence['originals_sha256'].items())
    for row in corpus.episodes:
        assert row['descriptor']['source_project_id']=='06' and row['descriptor']['source_approval_required']
        assert len(row['frames'])==6 and row['envelope']['source_kind']=='physical'  # Declared fake native packet domain only.
        assert row['report']['physical_qualification'] is False
        assert all(frame['action'].shape==(9,) and not frame['masks'][9:15].any() for frame in row['frames'])
    assert len(imports)==5 and len(evidence['originals_sha256'])==10
    assert corpus.spec['source_spec_snapshot']['version']==1 and corpus.version==2
    assert corpus.manifest['source_model_binding']['source_domain']=='SIMULATION' and corpus.manifest['target_domain']=='physical'
    repeated=cohort.build_aggregate(store,wizard['wizard_id'],imports)
    assert repeated['dataset_manifest']==str(path) and repeated['dataset_manifest_sha256']==sha(path)
    with pytest.raises(ValueError,match='unique'):cohort.build_aggregate(store,wizard['wizard_id'],imports+[imports[0]])


def test_late_target_rejection_blocks_existing_aggregate_without_rewriting_any_original(tmp_path,monkeypatch):
    store,wizard,path,imports,evidence=original_target_cohort(tmp_path,monkeypatch)
    before=sha(path);corpus=CanonicalCorpus(path)
    identity=corpus.train[0]['descriptor']['source_episode_id']
    job=next(store.job(ident) for ident in imports if store.job(ident)['config']['episode_id']==identity)
    from explorer_shared.native_capture_approval import source_bytes
    import hashlib
    wrapper=deepcopy(job['result']['source_approval']);previous=wrapper['review_sha256']
    wrapper['source_review'].update(review_id=uuid.uuid4().hex,decision='reject',previous_review_sha256=previous)
    wrapper['review_sha256']=hashlib.sha256(source_bytes(wrapper['source_review'])).hexdigest();wrapper['transfer_ready']=False
    receipt=cohort.note_source_review(store,wizard['wizard_id'],job['id'],wrapper,actor='SOFTWARE-FIXTURE-OWNER')
    assert receipt==cohort.note_source_review(store,wizard['wizard_id'],job['id'],wrapper,actor='SOFTWARE-FIXTURE-OWNER')
    assert receipt['decision']=='reject' and not receipt['raw_originals_modified'] and not receipt['optimizer_started']
    with pytest.raises(ValueError,match='review|approved'):cohort.build_aggregate(store,wizard['wizard_id'],imports)
    dataset=store.create_job(target.DATASET_KIND,'06',dict(inputs='GENERATED_ONLY'))
    store.complete_job(dataset['id'],dict(dataset_manifest=str(path),dataset_manifest_sha256=before))
    with pytest.raises(ValueError,match='review|approved'):target.target_dataset(store,wizard['wizard_id'],dataset['id'])
    with pytest.raises(ValueError,match='review chain'):
        cohort.note_source_review(store,wizard['wizard_id'],job['id'],job['result']['source_approval'],actor='SOFTWARE-FIXTURE-OWNER')
    assert sha(path)==before and all(sha(name)==expected for name,expected in evidence['originals_sha256'].items())
    from explorer.queue_policy import default_metadata,GIB
    budget=default_metadata(job)['resources'];assert budget['memory_bytes']==budget['disk_bytes']==GIB and budget['cpu_cores']==1
