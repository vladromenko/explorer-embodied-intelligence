"""Temporary byte snapshots only; no environment/hardware or real state writes."""
import json
from pathlib import Path

import pytest

from explorer_shared.source_archive import archive_sources,verify_source_archive,ArchiveRejected,sha,canonical,loaded_python_sources


def inventory(tmp_path):
    source=tmp_path/'source.py';source.write_bytes(b'def pure(x): return x + 1\n')
    cad=tmp_path/'mesh.STL';cad.write_bytes(b'EXPLICIT_SOFTWARE_CAD_FIXTURE')
    return {str(path):sha(path.read_bytes()) for path in (source,cad)}


def test_exact_source_CAD_bytes_remain_usable_even_after_original_changes_but_recheck_detects_it(tmp_path):
    sources=inventory(tmp_path);result=archive_sources(tmp_path/'archive',sources,context={'method':'SCRIPTED_SIM','physical_io':False})
    assert verify_source_archive(result['manifest_path'],result['manifest_sha256'],recheck_originals=True)['files']==2
    Path(next(iter(sources))).write_bytes(b'changed afterwards')
    assert verify_source_archive(result['manifest_path'],result['manifest_sha256'])['files']==2
    with pytest.raises(ArchiveRejected,match='across trial'):verify_source_archive(result['manifest_path'],result['manifest_sha256'],recheck_originals=True)
    body=json.loads(Path(result['manifest_path']).read_bytes());assert body['original_sources_modified'] is False
    assert {row['sha256'] for row in body['files']}==set(sources.values())


def test_existing_archive_or_bad_source_hash_cannot_silently_overwrite_evidence(tmp_path):
    sources=inventory(tmp_path);folder=tmp_path/'archive';archive_sources(folder,sources)
    with pytest.raises(ArchiveRejected,match='New immutable'):archive_sources(folder,sources)
    sources[next(iter(sources))]='0'*64
    with pytest.raises(ArchiveRejected,match='before snapshot'):archive_sources(tmp_path/'rejected',sources)
    failure=json.loads((tmp_path/'rejected/archive-rejected.json').read_bytes());assert failure['original_sources_modified'] is False


def test_archive_tamper_and_manifest_rebinding_are_rejected(tmp_path):
    result=archive_sources(tmp_path/'archive',inventory(tmp_path));path=Path(result['manifest_path']);body=json.loads(path.read_bytes())
    (path.parent/body['files'][0]['file']).write_bytes(b'tamper')
    with pytest.raises(ArchiveRejected,match='Retained source'):verify_source_archive(path,result['manifest_sha256'])
    path.write_bytes(canonical(dict(body,total_source_bytes=0)))
    with pytest.raises(ArchiveRejected,match='manifest SHA'):verify_source_archive(path,result['manifest_sha256'])


def test_symlink_locator_preserves_alias_and_resolved_source_and_detects_retarget(tmp_path):
    sources=inventory(tmp_path);real=Path(next(iter(sources)));alias=tmp_path/'legacy-source.py';alias.symlink_to(real)
    result=archive_sources(tmp_path/'archive',{str(alias):sources[str(real)]});body=json.loads(Path(result['manifest_path']).read_bytes())
    assert body['files'][0]['original_was_symlink'] and body['files'][0]['resolved_path']==str(real)
    other=tmp_path/'identical-other.py';other.write_bytes(real.read_bytes());alias.unlink();alias.symlink_to(other)
    with pytest.raises(ArchiveRejected,match='across trial'):verify_source_archive(result['manifest_path'],result['manifest_sha256'],recheck_originals=True)


def test_bounds_and_parent_escape_cannot_be_marked_complete(tmp_path):
    sources=inventory(tmp_path)
    with pytest.raises(ArchiveRejected,match='byte bound'):archive_sources(tmp_path/'too-large',sources,max_file_bytes=2)
    result=archive_sources(tmp_path/'valid',sources);path=Path(result['manifest_path']);body=json.loads(path.read_bytes());body['files'][0]['file']='../source.py';raw=canonical(body);path.write_bytes(raw)
    with pytest.raises(ArchiveRejected,match='path/size'):verify_source_archive(path,sha(raw))


def test_loaded_graph_is_explicit_limited_python_source_inventory():
    files=loaded_python_sources(('explorer_shared.source_archive',))
    assert len(files)==1 and all(name.endswith('source_archive.py') for name in files)
    assert next(iter(files.values()))==sha(Path(next(iter(files))).read_bytes())
