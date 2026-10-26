"""No optimizer: strict equality and preallocation cgroup admission."""
import importlib.util
from pathlib import Path
import sys

import pytest

SCRIPTS=Path(__file__).parents[1]/'scripts';sys.path.insert(0,str(SCRIPTS))
spec=importlib.util.spec_from_file_location('cpu_controller',SCRIPTS/'qualify_current_cpu_controller.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)


def test_tensor_exactness_includes_dtype_layout_shape_and_nested_scalar_type():
    import torch
    value=torch.tensor([1.,2.]);module.typed_exact({'optimizer':[value,3]},{'optimizer':[value.clone(),3]})
    for other in (value.double(),value.reshape(2,1),torch.tensor([1.,3.]),value.to_sparse()):
        with pytest.raises(module.q.QualificationRejected):module.typed_exact(value,other)
    with pytest.raises(module.q.QualificationRejected):module.typed_exact({'counter':1},{'counter':True})


def test_invalid_cgroup_cannot_reach_source_or_model_allocation(tmp_path,monkeypatch):
    def refused():raise module.q.QualificationRejected('Own bounded cgroup required')
    monkeypatch.setattr(module.q,'limits',refused)
    def unexpected(*args):raise AssertionError('Source/model admission must not execute')
    monkeypatch.setattr(module.q,'assert_source',unexpected)
    with pytest.raises(module.q.QualificationRejected,match='cgroup'):
        module.run(tmp_path,tmp_path/'no-output','0'*40,'0'*64,'0'*64,'declared')
    assert not (tmp_path/'no-output').exists()
