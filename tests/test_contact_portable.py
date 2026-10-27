"""Portable qualifier guards on synthetic bytes; no CAD or native execution."""
import importlib.util
import os
import json
from pathlib import Path
import subprocess
import sys

import pytest


@pytest.fixture
def proof():
    path=Path(__file__).parents[1]/'scripts/qualify_contact_portable.py'
    spec=importlib.util.spec_from_file_location('portable_contact_fixture',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module


def test_portable_copy_checks_exact_bytes_aliases_and_source_recheck(proof,tmp_path):
    source=tmp_path/'source';source.write_bytes(b'EXPLICIT_SOFTWARE_DATA_NEVER_EXECUTED')
    expected=proof.hash_file(source);target=tmp_path/'copy'
    assert proof.copy_exact(source,target,expected['sha256'])==expected
    assert target.read_bytes()==source.read_bytes()
    source.write_bytes(b'CHANGED_SOURCE_BYTES')
    with pytest.raises(proof.PortableProofRejected,match='SHA'):proof.copy_exact(source,tmp_path/'other',expected['sha256'])
    alias=tmp_path/'alias';alias.symlink_to(source)
    with pytest.raises(proof.PortableProofRejected,match='nonaliased'):proof.hash_file(alias)


def test_portable_output_cannot_write_inside_or_over_original_producer(proof,tmp_path):
    original=tmp_path/'old';original.mkdir()
    for output in (original,original/'new-copy',tmp_path):
        with pytest.raises(proof.PortableProofRejected,match='overlap'):
            proof.prepare(original,'f'*64,output,'a'*40,'case')
    assert list(original.iterdir())==[]


def test_relocated_source_bindings_are_data_only_exact_hashes(proof,tmp_path):
    raw=b"raise RuntimeError('ARCHIVED_PYTHON_MUST_NEVER_EXECUTE')\n";checksum=proof.sha(raw)
    folder=tmp_path/'initial-state/source';folder.mkdir(parents=True);path=folder/(checksum+'.bin');path.write_bytes(raw)
    doc=dict(source_bindings=dict(helper=dict(file='source/'+checksum+'.bin',sha256=checksum,bytes=len(raw))))
    bindings=proof.relocated_bindings(dict(folder=str(tmp_path)),doc)
    assert bindings==dict(helper=dict(path=str(path),sha256=checksum))
    path.write_bytes(b'CHANGED_ARCHIVED_PAYLOAD')
    with pytest.raises(proof.PortableProofRejected,match='SHA'):proof.relocated_bindings(dict(folder=str(tmp_path)),doc)


def test_native_comparison_does_not_accept_one_ULP_or_dtype_differences(proof,tmp_path):
    import numpy as np
    array=np.arange(12,dtype=np.float64).reshape(3,4);a=tmp_path/'a.npy';b=tmp_path/'b.npy'
    np.save(a,array,allow_pickle=False);np.save(b,array,allow_pickle=False)
    assert proof.compare_native(a,b)['bitwise_equal']
    changed=array.copy();changed[1,1]=np.nextafter(changed[1,1],float('inf'));np.save(b,changed,allow_pickle=False)
    report=proof.compare_native(a,b)
    assert not report['bitwise_equal'] and report['different_float64_words']==1 and report['maximum_absolute_numeric_difference']>0
    np.save(b,array.astype(np.float32),allow_pickle=False)
    assert proof.compare_native(a,b)['reason']=='NATIVE_ABI_DIFFERS'


def test_original_tree_python_open_ban_and_positive_control_are_real_in_fresh_PID(proof,tmp_path):
    original=tmp_path/'original';original.mkdir();(original/'sample').write_bytes(b'EXPLICIT_ORIGINAL_DATA')
    copied=tmp_path/'copy';copied.write_bytes(b'EXPLICIT_PORTABLE_DATA')
    script="""import importlib.util,pathlib
spec=importlib.util.spec_from_file_location('portable',PATH);p=importlib.util.module_from_spec(spec);spec.loader.exec_module(p)
blocked=p.audit_originals([ROOT])
try:pathlib.Path(ROOT,'sample').read_bytes()
except PermissionError:pass
else:raise AssertionError('Old original opened')
assert len(blocked)==1
assert pathlib.Path(COPY).read_bytes()==b'EXPLICIT_PORTABLE_DATA'
""".replace('PATH',repr(str(Path(proof.__file__)))).replace('ROOT',repr(str(original))).replace('COPY',repr(str(copied)))
    subprocess.run([sys.executable,'-c',script],check=True,env={**os.environ,"PYTHONPATH":str(Path(__file__).resolve().parents[1]/"shared")})


def test_current_trusted_loop_cannot_be_replaced_by_untrusted_archived_payload(proof,monkeypatch):
    called=[]
    monkeypatch.setattr(proof.importlib.util,'spec_from_file_location',lambda *a,**k:called.append(a))
    with pytest.raises(proof.PortableProofRejected,match='source differs'):
        proof.current_driver(dict(trusted_current_sources={'scripts/run_contact_probes.py':'f'*64}))
    assert not called


@pytest.mark.parametrize('status,code',[('FAILED',0),('PASS',1)])
def test_first_concrete_failure_stops_full_campaign_and_nonzero_exit_cannot_be_PASS(proof,tmp_path,monkeypatch,status,code):
    plan=dict(output_root=str(tmp_path),cases=[dict(attempt_id='case0'),dict(attempt_id='case1')],trusted_commit='a'*40,forbidden_original_roots=['/synthetic/old-root'])
    called=[]
    class Child:
        pid=1234
        def __init__(self,*args,**kwargs):
            called.append(args);path=tmp_path/'replay/case0';path.mkdir(parents=True)
            (path/'qualification.json').write_bytes(proof.canonical(dict(status=status,scope='MOCK_NOT_NUMERICAL_PROOF')))
        def wait(self,timeout):return code
    monkeypatch.setattr(proof.subprocess,'Popen',Child);monkeypatch.setattr(proof,'guard',lambda plan:None)
    monkeypatch.setattr(proof,'resources',lambda:dict(scope='MOCK_NO_ALLOCATION'))
    monkeypatch.setattr(proof.shutil,'which',lambda name:None)
    proof.coordinate(plan,'b'*64)
    result=json.loads((tmp_path/'qualification.json').read_bytes())
    assert len(called)==1 and len(result['children'])==1 and not result['all_declared_cases_PASS']
    assert result['C_open_scope']=='UNMEASURED_STRACE_NOT_INSTALLED' and not result['physical_io']
