"""Review revisions create explicit retries without rewriting imported originals."""
import pytest
from explorer.store import Store


def test_renewed_review_after_failed_import_gets_new_job_and_keeps_original_config(tmp_path):
    store=Store(tmp_path)
    first=store.create_import_job('mobile_import','02','e'*32,'a'*64,source_format='native_capture_v1',source_review_sha256='b'*64)
    with store.connect() as db:db.execute("UPDATE jobs SET status='failed' WHERE id=?",(first['id'],))
    same=store.create_import_job('mobile_import','02','e'*32,'a'*64,source_format='native_capture_v1',source_review_sha256='b'*64)
    assert same['id']==first['id']
    second=store.create_import_job('mobile_import','02','e'*32,'a'*64,source_format='native_capture_v1',source_review_sha256='c'*64)
    assert second['id']!=first['id'] and second['config']['retry_of_job_id']==first['id']
    assert store.job(first['id'])['config']==first['config']
    assert store.job(first['id'])['status']=='failed'
    with store.connect() as db:db.execute("UPDATE jobs SET status='completed' WHERE id=?",(second['id'],))
    assert store.create_import_job('mobile_import','02','e'*32,'a'*64,source_format='native_capture_v1',source_review_sha256='d'*64)['id']==second['id']
    with pytest.raises(ValueError,match='Conflicting'):store.create_import_job('mobile_import','02','e'*32,'f'*64,source_format='native_capture_v1',source_review_sha256='d'*64)


def test_review_hash_cannot_be_attached_to_a_different_producer(tmp_path):
    store=Store(tmp_path)
    with pytest.raises(ValueError,match='review SHA256'):store.create_import_job('mobile_import','02','e'*32,source_review_sha256='b'*64)
    with pytest.raises(ValueError,match='review SHA256'):store.create_import_job('mobile_import','02','e'*32,'a'*64,source_format='native_capture_v1',source_review_sha256='bad')
    assert not store.jobs()
