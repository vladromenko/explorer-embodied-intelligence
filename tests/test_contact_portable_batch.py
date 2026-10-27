"""Small exact ONE→batch lineage fixtures; no models, devices or full copies."""
from copy import deepcopy
import importlib.util
from pathlib import Path

import pytest


@pytest.fixture
def batch():
    path=Path(__file__).parents[1]/'scripts/qualify_contact_portable_batch.py'
    spec=importlib.util.spec_from_file_location('contact_batch_fixture',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module


def one_fixture(batch,tmp_path,mutation=None):
    plan=dict(qualifier_sha256=batch.QUALIFIER_SHA,cases=[dict(attempt_id='MOCK_ONE')],trusted_current_sources={'fixture':'SOURCE_ONLY'},producer_report_sha256='d'*64)
    batch.write(tmp_path/'portable-plan.json',plan)
    proof=dict(status='PASS',native=dict(bitwise_equal=True,different_float64_words=0),
        contacts_solver_JSONL_bitwise_equal=True,contact_score_bitwise_equal=True,
        Python_open_positive_control_blocked=True,old_producer_Python_open_attempts=[])
    child=dict(exit_code=0,reaped=True,qualification=proof,qualification_sha256='f'*64)
    result=dict(all_declared_cases_PASS=True,children=[child],plan_sha256=batch.file_sha(tmp_path/'portable-plan.json'),C_open_scope='UNMEASURED_STRACE_NOT_INSTALLED')
    if mutation:mutation(result)
    batch.write(tmp_path/'qualification.json',result)
    return batch.file_sha(tmp_path/'qualification.json')


def test_actual_one_lineage_has_exact_native_contact_scope_before_full_copies(batch,tmp_path):
    expected=one_fixture(batch,tmp_path);receipt=batch.one_proof(tmp_path,expected)
    assert receipt['qualification_sha256']==expected and receipt['qualifier_sha256']==batch.QUALIFIER_SHA
    assert receipt['original_OS_closure']=='PARTIAL' and receipt['C_open_scope']=='UNMEASURED_STRACE_NOT_INSTALLED'


@pytest.mark.parametrize('mutation',[
    lambda r:r.update(all_declared_cases_PASS=False),
    lambda r:r['children'][0].update(exit_code=1),
    lambda r:r['children'][0]['qualification']['native'].update(different_float64_words=1),
    lambda r:r['children'][0]['qualification'].update(contacts_solver_JSONL_bitwise_equal=False),
    lambda r:r['children'][0]['qualification'].update(old_producer_Python_open_attempts=['/forbidden/old']),
])
def test_failed_or_weakened_one_proof_cannot_unlock_batch(batch,tmp_path,mutation):
    expected=one_fixture(batch,tmp_path,mutation)
    with pytest.raises(batch.BatchRejected):batch.one_proof(tmp_path,expected)


def test_qualified_old_verifier_is_not_edited_or_loaded_if_sha_changes(batch,monkeypatch):
    calls=[];monkeypatch.setattr(batch,'file_sha',lambda path:'a'*64)
    monkeypatch.setattr(batch.importlib.util,'spec_from_file_location',lambda *a,**k:calls.append(a))
    with pytest.raises(batch.BatchRejected,match='changed'):batch.original_qualifier()
    assert not calls
