"""Strict reviewed source extensions; old optimizer receipts stay byte-identical.

This is a source compatibility check, never a replacement optimizer experiment.
Every changed file has an exact new SHA and an invertible, bounded text change
whose inverse must reproduce the independently recorded old whole-file SHA.
"""
import hashlib
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
RECEIPT=ROOT/'_registry/evidence/experiment05-training-source-extensions-20261028.json'

RECEIPT06=ROOT/'_registry/evidence/experiment06-training-source-extension-20261028.json'


def assert_qualified_source(root,name,expected):
    raw=(Path(root)/name).read_bytes()
    if hashlib.sha256(raw).hexdigest()==expected:return
    latest=json.loads(RECEIPT06.read_bytes())
    assert latest['previous_receipt_path']==str(RECEIPT.relative_to(ROOT))
    assert hashlib.sha256(RECEIPT.read_bytes()).hexdigest()==latest['previous_receipt_sha256']
    for proof in (latest,json.loads(RECEIPT.read_bytes())):
        extension=proof['extensions'].get(name)
        if extension is not None and hashlib.sha256(raw).hexdigest()==extension['current_sha256']:
            for replacement in extension['inverse_replacements']:
                after,before=replacement['current'].encode(),replacement['previous'].encode()
                assert raw.count(after)==1,(name,'Ambiguous inverse')
                raw=raw.replace(after,before)
            assert hashlib.sha256(raw).hexdigest()==extension['previous_sha256']
    assert hashlib.sha256(raw).hexdigest()==expected,(name,'Unreviewed change or original source not exactly reconstructed')
