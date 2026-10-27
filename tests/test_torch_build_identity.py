"""Exact runtime guard fixtures only: no Torch import/model/forward."""
import ast
import importlib.util
from pathlib import Path
import subprocess
import sys

import pytest

from explorer_shared.learning.torch_build_identity import pin_torch_build, check_torch_build
from explorer_shared.learning.sim_train_forward_audit import AuditRejected


GIT = '70d99e998b4955e0049d13a98d77ae1b14db1f45'


def fixture(tmp_path, text=None):
    version=tmp_path/'version.py';metadata=tmp_path/'METADATA'
    version.write_text(text if text is not None else "__version__ = '2.11.0+cu130'\ncuda: str = '13.0'\ngit_version = '"+GIT+"'\n")
    metadata.write_text('Name: torch\nVersion: 2.11.0\n')
    return version,metadata


def actual(version, **changes):
    result=dict(module_version='2.11.0+cu130',distribution_version='2.11.0',cuda_build='13.0',git_version=GIT,module_file=str(version))
    result.update(changes)
    return result


def test_exact_full_module_and_distinct_bare_distribution_are_independently_pinned(tmp_path):
    version,metadata=fixture(tmp_path);record=pin_torch_build(version,metadata,'2.11.0')
    assert record['module_version']=='2.11.0+cu130' and record['distribution_version']=='2.11.0'
    assert check_torch_build(record,**actual(version))
    assert record['static_AST_only'] and not record['version_file_executed_in_preparation']


@pytest.mark.parametrize('changes',[{'module_version':'2.11.0'}, {'module_version':'2.11.0+cu129'},
                                  {'module_version':'2.11.0+cpu'}, {'distribution_version':'2.11.0+cu130'},
                                  {'cuda_build':'12.9'}, {'git_version':'0'*40}])
def test_wrong_suffix_bare_module_or_other_build_field_is_rejected(tmp_path,changes):
    version,metadata=fixture(tmp_path);record=pin_torch_build(version,metadata,'2.11.0')
    with pytest.raises(AuditRejected):check_torch_build(record,**actual(version,**changes))


@pytest.mark.parametrize('text',["cuda='13.0'\ngit_version='"+GIT+"'\n",
                               "__version__='2.11.0'+'+cu130'\ncuda='13.0'\ngit_version='"+GIT+"'\n",
                               "__version__=str('2.11.0+cu130')\ncuda='13.0'\ngit_version='"+GIT+"'\n",
                               "__version__='2.11.0+cu130'\n__version__='other'\ncuda='13.0'\ngit_version='"+GIT+"'\n"])
def test_missing_nonliteral_or_duplicate_module_value_denied_in_preparation(tmp_path,text):
    version,metadata=fixture(tmp_path,text)
    with pytest.raises(AuditRejected):pin_torch_build(version,metadata,'2.11.0')


def test_metadata_byte_change_rejected_even_when_version_header_is_unchanged(tmp_path):
    version,metadata=fixture(tmp_path);record=pin_torch_build(version,metadata,'2.11.0')
    metadata.write_text(metadata.read_text()+'Summary: changed\n')
    with pytest.raises(AuditRejected,match='metadata bytes'):check_torch_build(record,**actual(version))


def test_version_source_byte_change_and_foreign_module_path_rejected(tmp_path):
    version,metadata=fixture(tmp_path);record=pin_torch_build(version,metadata,'2.11.0')
    foreign=tmp_path/'other.py';foreign.write_bytes(version.read_bytes())
    with pytest.raises(AuditRejected,match='path/bytes'):check_torch_build(record,**actual(version,module_file=str(foreign)))
    version.write_text(version.read_text()+'# different\n')
    with pytest.raises(AuditRejected,match='path/bytes'):check_torch_build(record,**actual(version))


def test_static_preparation_never_executes_version_file(tmp_path):
    version,metadata=fixture(tmp_path);marker=tmp_path/'executed'
    version.write_text(version.read_text()+"\nopen("+repr(str(marker))+",'w').write('executed')\n")
    pin_torch_build(version,metadata,'2.11.0')
    assert not marker.exists()


def test_B_import_remains_Torch_and_physics_lazy():
    path=Path(__file__).resolve().parents[1]/'scripts/run_train_forward_audit_B.py'
    subprocess.run([sys.executable,'-c','import runpy,sys;runpy.run_path('+repr(str(path))+');assert not any(k.split(".")[0] in {"torch","mujoco","cv2","rospy","rclpy"} for k in sys.modules)'],check=True,timeout=15)


def test_B_inference_body_changes_ONLY_exact_runtime_guard():
    root=Path(__file__).resolve().parents[1]/'scripts'
    functions=[]
    for filename in ['run_train_forward_audit.py','run_train_forward_audit_B.py']:
        tree=ast.parse((root/filename).read_bytes());fn=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='run_cell')
        remove=[]
        for i,node in enumerate(fn.body):
            if isinstance(node,ast.Expr) and isinstance(node.value,ast.Call):
                call=node.value
                if isinstance(call.func,ast.Name) and call.func.id=='check_torch_build':remove.append(i)
                elif isinstance(call.func,ast.Name) and call.func.id=='require' and any(isinstance(arg,ast.Constant) and arg.value in ['Frozen framework/Python runtime differs','Frozen NumPy/Python runtime differs'] for arg in call.args):remove.append(i)
        assert len(remove)==(1 if filename=='run_train_forward_audit.py' else 2)
        fn.body=[node for i,node in enumerate(fn.body) if i not in remove]
        functions.append(ast.dump(fn,include_attributes=False))
    assert functions[0]==functions[1]
