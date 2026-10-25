"""Protect review credit against source drift and invalid partial imports."""
import importlib.util
import json
from pathlib import Path

import pytest

MODULE = Path(__file__).resolve().parents[2] / 'literature/tools/registry.py'
spec = importlib.util.spec_from_file_location('literature_registry', MODULE)
registry = importlib.util.module_from_spec(spec)
spec.loader.exec_module(registry)


def fixture(tmp_path):
    paper = dict(paper_id='P001', canonical_id='fixture:one', fulltext_status='RETRIEVED',
                 fulltext_sha256='a' * 64, primary_status='NOT_VERIFIED', review_status='UNREVIEWED')
    path = tmp_path / 'literature_registry.json'
    path.write_text(json.dumps(dict(papers=[paper])))
    directory = tmp_path / 'reviews'
    directory.mkdir()
    review = {field: 'Synthetic unit-test fixture, not a real literature review' for field in registry.REVIEW_FIELDS}
    review.update(paper_id='P001', source_sha256='a' * 64, full_text_examined=True,
                  deep_review=False, decision='COMPARE', quantitative_result='NOT_EXTRACTED',
                  evidence_locations=[dict(page=1, section='synthetic', supports='fixture')])
    return path, directory / 'P001.json', review


def test_retrieval_never_earns_review_credit_and_valid_review_does(tmp_path):
    path, review_path, review = fixture(tmp_path)
    assert registry.report(path)['counts']['full_text_read'] == 0
    review_path.write_text(json.dumps(review))
    registry.ingest_reviews(path)
    report = registry.report(path)
    assert report['counts']['full_text_read'] == 1
    assert report['counts']['deep_reviews'] == 0
    assert report['status'] == 'LITERATURE_REVIEW_INCOMPLETE'


def test_source_drift_and_string_boolean_cannot_partially_promote_registry(tmp_path):
    path, review_path, review = fixture(tmp_path)
    original = path.read_bytes()
    review.update(source_sha256='b' * 64, full_text_examined='true', deep_review='false')
    review_path.write_text(json.dumps(review))
    with pytest.raises(ValueError, match='SHA256'):
        registry.ingest_reviews(path)
    assert path.read_bytes() == original
