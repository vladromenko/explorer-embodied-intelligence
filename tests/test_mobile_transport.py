import concurrent.futures
import hashlib
import json
import tarfile

import pytest
from explorer.store import Store
from explorer_shared.mobile_records import MobileRecords


def original(tmp_path,state='complete'):
    folder=tmp_path/'original'/'episode-123'
    (folder/'frames').mkdir(parents=True)
    (folder/'episode.json').write_text(json.dumps({'id':'episode-123','format':'explorer_mobile_episode_v3','state':state}))
    (folder/'samples.jsonl').write_text(json.dumps({'image':'frames/0000.jpg'})+'\n')
    (folder/'frames/0000.jpg').write_bytes(b'transport-only test bytes, not a physical camera frame')
    return folder,MobileRecords(folder.parent,tmp_path/'cache')


def test_original_mobile_bytes_are_immutable_and_corrupt_cache_is_repaired(tmp_path):
    folder,records=original(tmp_path)
    before={str(p.relative_to(folder)):p.read_bytes() for p in folder.rglob('*') if p.is_file()}
    archive=records.archive('episode-123')
    expected=hashlib.sha256(archive.read_bytes()).hexdigest()
    with tarfile.open(archive) as saved:
        for name,data in before.items():
            assert saved.extractfile(name).read()==data
    archive.write_bytes(b'interrupted cache storage')
    repaired=records.archive('episode-123')
    assert hashlib.sha256(repaired.read_bytes()).hexdigest()==expected
    assert {str(p.relative_to(folder)):p.read_bytes() for p in folder.rglob('*') if p.is_file()}==before


def test_active_or_escaping_mobile_sources_are_not_archived(tmp_path):
    folder,records=original(tmp_path,'recording')
    with pytest.raises(ValueError,match='Finish'):
        records.archive('episode-123')
    (folder/'episode.json').write_text(json.dumps({'id':'episode-123','format':'explorer_mobile_episode_v3','state':'complete'}))
    (folder/'samples.jsonl').write_text(json.dumps({'image':'../../private-file'})+'\n')
    with pytest.raises(ValueError,match='path'):
        records.archive('episode-123')


def test_concurrent_finish_replies_create_only_one_durable_import_job(tmp_path):
    store=Store(tmp_path/'state')
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        jobs=list(pool.map(lambda _:store.create_import_job('mobile_import','02','one-native-episode'),range(16)))
    assert len({job['id'] for job in jobs})==1
    assert len(store.jobs())==1
