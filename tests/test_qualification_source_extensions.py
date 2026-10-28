"""Reviewed fixture/API extension must reconstruct the exact old whole file."""
import json
from pathlib import Path
import pytest
from qualification_source_extensions import ROOT,RECEIPT,assert_qualified_source

@pytest.mark.parametrize('name',[
    '00-control-center/src/explorer/app.py','tests/test_so101_approved_capture_cohort.py'])
def test_current_extension_is_exact_and_unrelated_edits_are_rejected(tmp_path,name):
    extension=json.loads(RECEIPT.read_bytes())['extensions'][name]
    assert_qualified_source(ROOT,name,extension['previous_sha256'])
    candidate=tmp_path/name;candidate.parent.mkdir(parents=True)
    candidate.write_bytes((ROOT/name).read_bytes()+b'\nUNREVIEWED_EXTRA_CHANGE=True\n')
    with pytest.raises(AssertionError):assert_qualified_source(tmp_path,name,extension['previous_sha256'])
    with pytest.raises(AssertionError):assert_qualified_source(ROOT,name,'0'*64)


def test_target_context_extension_is_exact_and_does_not_exempt_default_trainer_edits(tmp_path):
    import hashlib
    from qualification_source_extensions import RECEIPT06_TARGET
    name="shared/explorer_shared/learning/rosmaster_skill_pipeline.py"
    extension=json.loads(RECEIPT06_TARGET.read_bytes())["extensions"][name]
    assert_qualified_source(ROOT,name,extension["previous_sha256"])
    candidate=tmp_path/name;candidate.parent.mkdir(parents=True)
    candidate.write_bytes((ROOT/name).read_bytes()+b"\nUNREVIEWED_TRAINER_CHANGE=True\n")
    with pytest.raises(AssertionError):assert_qualified_source(tmp_path,name,extension["previous_sha256"])


@pytest.mark.parametrize('name',['00-control-center/src/explorer/rosmaster_learning.py',
    '00-control-center/src/explorer/rosmaster_eval_job.py','00-control-center/src/explorer/job_runner.py',
    '00-control-center/src/explorer/queue_policy.py'])
def test_saved_target_shared_extension_requires_exact_inverse_and_rejects_extra_changes(tmp_path,name):
    from qualification_source_extensions import RECEIPT06_SAVED
    extension=json.loads(RECEIPT06_SAVED.read_bytes())['extensions'][name]
    assert_qualified_source(ROOT,name,extension['previous_sha256'])
    candidate=tmp_path/name;candidate.parent.mkdir(parents=True)
    candidate.write_bytes((ROOT/name).read_bytes()+b'\nUNREVIEWED_SAVED_MODEL_CHANGE=True\n')
    with pytest.raises(AssertionError):assert_qualified_source(tmp_path,name,extension['previous_sha256'])
