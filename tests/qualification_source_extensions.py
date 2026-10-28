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
RECEIPT06_PI=ROOT/'_registry/evidence/experiment06-training-source-extension-missing-pi-20261028.json'

RECEIPT06_TARGET=ROOT/'_registry/evidence/experiment06-training-source-extension-target-context-20261028.json'

RECEIPT06_SAVED=ROOT/'_registry/evidence/experiment06-training-source-extension-saved-target-20261028.json'

RECEIPT06_NATIVE=ROOT/'_registry/evidence/experiment06-training-source-extension-native-target-20261028.json'

RECEIPT06_COHORT=ROOT/'_registry/evidence/experiment06-training-source-extension-target-cohort-20261028.json'

def assert_qualified_source(root,name,expected):
    raw=(Path(root)/name).read_bytes()
    if hashlib.sha256(raw).hexdigest()==expected:return
    paths=(RECEIPT06_COHORT,RECEIPT06_NATIVE,RECEIPT06_SAVED,RECEIPT06_TARGET,RECEIPT06_PI,RECEIPT06,RECEIPT)
    proofs=[json.loads(path.read_bytes()) for path in paths]
    for current,previous in zip(proofs,paths[1:]):
        assert current['previous_receipt_path']==str(previous.relative_to(ROOT))
        assert hashlib.sha256(previous.read_bytes()).hexdigest()==current['previous_receipt_sha256']
    for proof in proofs:
        extension=proof['extensions'].get(name)
        if extension is not None and hashlib.sha256(raw).hexdigest()==extension['current_sha256']:
            for replacement in extension['inverse_replacements']:
                after,before=replacement['current'].encode(),replacement['previous'].encode()
                assert raw.count(after)==1,(name,'Ambiguous inverse')
                raw=raw.replace(after,before)
            assert hashlib.sha256(raw).hexdigest()==extension['previous_sha256']
            if hashlib.sha256(raw).hexdigest()==expected:return
    assert hashlib.sha256(raw).hexdigest()==expected,(name,'Unreviewed change or original source not exactly reconstructed')
