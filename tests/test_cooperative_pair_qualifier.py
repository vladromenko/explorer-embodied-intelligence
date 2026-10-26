"""Qualifier source access is isolated/read-only; these tests load no model."""
import importlib.util
from pathlib import Path
import sqlite3
import sys

import pytest

from explorer.store import Store


def module():
    path=Path(__file__).resolve().parents[1]/'scripts/qualify_cooperative_cpu_pair.py'
    spec=importlib.util.spec_from_file_location('cooperative_pair_qualifier',path)
    value=importlib.util.module_from_spec(spec);spec.loader.exec_module(value);return value


def test_registry_copy_never_opens_original_write_capable_store_and_queries_are_readonly(tmp_path):
    helper=module();original=Store(tmp_path/'original');job=original.create_job('so101_skill_train','04',{'fixture':True})
    before=helper.sha(original.path);before_modules=set(sys.modules)
    view,inventory=helper.original_sql_view(original.root,tmp_path/'private-copy')
    assert view.root==original.root.resolve() and view.path.parent==tmp_path/'private-copy'
    assert view.job(job['id'])['config']=={'fixture':True}
    with view.connect() as db:
        with pytest.raises(sqlite3.OperationalError):db.execute('DELETE FROM jobs')
    assert helper.sha(original.path)==before and inventory['research.sqlite3']==before
    assert 'torch' not in set(sys.modules)-before_modules


def test_original_registry_snapshot_is_bounded_before_large_read(tmp_path):
    helper=module();root=tmp_path/'source';root.mkdir()
    path=root/'research.sqlite3'
    with path.open('wb') as stream:stream.truncate(65*1024**2)
    with pytest.raises(ValueError,match='Bounded qualification fixture'):helper.original_sql_view(root,tmp_path/'copy')
    assert path.stat().st_size==65*1024**2 and not (tmp_path/'copy/research.sqlite3').exists()
