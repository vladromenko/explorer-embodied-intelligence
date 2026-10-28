"""Whole paired native admission from original writers/importers, generated only."""
import hashlib
import json
from pathlib import Path
import pytest
from explorer import source_demo_workflow as source_reviews
from test_so101_approved_capture_cohort import _change_source_review
from cooperative05_cohort_helpers import generated_pair_cohort


def test_paired_original_producers_import_review_and_aggregate_keep_separate_native_heads(tmp_path,monkeypatch):
    root,store,spec,rows,reviews,aggregate,evidence=generated_pair_cohort(tmp_path,monkeypatch)
    try:
        assert len(reviews)==5 and len(rows)==5
        assert [review['split'] for review in reviews].count('train')==3
        assert [review['split'] for review in reviews].count('development')==1
        assert [review['split'] for review in reviews].count('sealed_test')==1
        assert not aggregate['manifest']['action_spaces_merged'] and not aggregate['jobs_enqueued']
        assert evidence['actual_physical_inputs']==0
        for review in reviews:
            assert review['native_admission_reports']['giver']['action_contract']['dimension']==9
            assert review['native_admission_reports']['receiver']['action_contract']['dimension']==6
            assert not review['clock_domains_equated'] and not review['model_physical_success_inferred']
        assert len(aggregate['manifest']['train_recordings'])==3 and len(aggregate['manifest']['development_recordings'])==1
        assert len(aggregate['manifest']['sealed_recordings'])==1 and aggregate['manifest']['sealed_not_for_fitting']
        # A later real source-review transition must invalidate the whole pair;
        # an old cooperative review never substitutes for current native consent.
        receiver=store.job(reviews[0]['native_episode_refs']['receiver']['import_job_id'])
        path=Path(receiver['result']['source_manifest_path']);original=path.read_bytes();raw=json.loads(original)
        decision=_change_source_review(tmp_path/'receiver',0,raw,'reject',90_000_000_000)
        source_reviews.observe_source_review(store,raw['episode_id'],receiver['config']['archive_sha256'],decision)
        with pytest.raises(ValueError,match='native admission/source evidence changed'):
            root.command('aggregate',{'skill_id':spec['skill_id'],'version':1},authenticated_actor='SOFTWARE_REVIEW_ACTOR')
        assert path.read_bytes()==original
        assert all(hashlib.sha256(Path(name).read_bytes()).hexdigest()==checksum for name,checksum in evidence['native9_originals']['originals_sha256'].items())
    finally:root.close()
