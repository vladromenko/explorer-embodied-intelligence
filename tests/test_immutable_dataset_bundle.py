"""Real immutable filesystem materialization; no dataset expert/physical claim."""
import hashlib
from pathlib import Path

import pytest

from explorer_shared.immutable_dataset_bundle import write_immutable_bundle


def test_same_immutable_bytes_and_source_drift_never_replace_existing_bundle(tmp_path):
    source=tmp_path/'original';source.write_bytes(b' \nexact original\xff\n')
    expected=hashlib.sha256(source.read_bytes()).hexdigest();metadata=b'{"original":true}'
    files={'episodes/a/source.bin':(source,expected),'source-review.json':(metadata,hashlib.sha256(metadata).hexdigest())}
    destination=tmp_path/'aggregate';manifest=b'{"declared":"canonical"}'
    path=write_immutable_bundle(destination,manifest,files)
    assert path.read_bytes()==manifest and (destination/'episodes/a/source.bin').read_bytes()==source.read_bytes()
    assert write_immutable_bundle(destination,manifest,files)==path
    with pytest.raises(ValueError,match='manifest changed'):write_immutable_bundle(destination,b'other',files)
    (destination/'episodes/a/source.bin').write_bytes(b'changed own test copy')
    with pytest.raises(ValueError,match='payload changed'):write_immutable_bundle(destination,manifest,files)
    assert source.read_bytes()==b' \nexact original\xff\n'


def test_unsafe_payload_or_changed_source_is_rejected_without_touching_originals(tmp_path):
    source=tmp_path/'original';source.write_bytes(b'changed before copy')
    for name in ('../outside','/absolute','a/../outside','manifest.json'):
        with pytest.raises(ValueError,match='payload path'):write_immutable_bundle(tmp_path/'bad',b'{}',{name:(b'',hashlib.sha256(b'').hexdigest())})
    with pytest.raises(ValueError,match='Source changed'):write_immutable_bundle(tmp_path/'new',b'{}',{'source.bin':(source,'0'*64)})
    assert not (tmp_path/'new').exists() and source.read_bytes()==b'changed before copy'
