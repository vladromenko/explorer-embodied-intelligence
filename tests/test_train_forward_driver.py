"""No actual models: lazy import, prior-only code inspection and resource fence."""
import importlib.util
from pathlib import Path
import subprocess
import sys
import json

import pytest
from explorer_shared.learning.sim_train_forward_audit import AuditRejected
from explorer_shared.contracts import canonical


def driver():
    path=Path(__file__).resolve().parents[1]/'scripts/run_train_forward_audit.py'
    spec=importlib.util.spec_from_file_location('train_audit_driver',path);value=importlib.util.module_from_spec(spec);spec.loader.exec_module(value)
    return value


def test_preparation_import_does_not_load_torch_mujoco_or_native_owners():
    path=Path(__file__).resolve().parents[1]/'scripts/run_train_forward_audit.py'
    code='import runpy,sys;runpy.run_path('+repr(str(path))+');assert not any(k.split(".")[0] in {"torch","mujoco","cv2","rospy","rclpy"} for k in sys.modules)'
    subprocess.run([sys.executable,'-c',code],check=True,timeout=15)


def test_actual_installed_training_vs_prior_loss_definition_is_static_inspection_only():
    value=driver().source_inspection()
    assert value['posterior_forward_executed'] is False and not value['future_labels_available_to_actor']
    assert 'posterior' in value['train'] and 'zeros' in value['deployment'] and '0.1*' in value['training_loss']
    assert len(value['source_files_sha256'])==11 and set(value['installed_versions'])=={'lerobot','numpy','torch','torchvision'}


@pytest.mark.parametrize('change',['foreign_unit','unboundedCPU','swap','wrongRAM'])
def test_resource_denial_is_before_torch_import(change):
    d=driver();r=dict(cgroup='/sys/fs/cgroup/expected.service',limits={'cpu.max':'200000 100000','memory.max':str(3*1024**3),'memory.swap.max':'0'})
    if change=='foreign_unit':r['cgroup']='/sys/fs/cgroup/other.service'
    elif change=='unboundedCPU':r['limits']['cpu.max']='max 100000'
    elif change=='swap':r['limits']['memory.swap.max']='1048576'
    else:r['limits']['memory.max']=str(4*1024**3)
    with pytest.raises(AuditRejected,match='before Torch'):d.require_resources(r,'expected')


def test_changed_source_pin_is_rejected_before_resource_or_model_loading(tmp_path,monkeypatch):
    d=driver();source=tmp_path/'source';source.write_bytes(b'original')
    plan=dict(schema=d.SCHEMA,total_forward_calls=1756,cells=d.CELLS,episode_id=d.EPISODE,frames=439,
              source_commit='frozen',source_files_sha256={str(source):d.file_sha(source)})
    path=tmp_path/'plan.json';path.write_bytes(canonical(plan));expected=d.file_sha(path);source.write_bytes(b'changed')
    monkeypatch.setattr(d.subprocess,'check_output',lambda args,**kwargs:'frozen' if 'rev-parse' in args else '')
    monkeypatch.setattr(d,'resources',lambda:pytest.fail('resource/model stage reached'))
    with pytest.raises(AuditRejected,match='Immutable input'):d.checked(path,expected,'expected')


def test_summary_requires_bound_raw_predictions_and_rejects_missing_completed_audit(tmp_path,monkeypatch):
    d=driver();monkeypatch.setattr(d,'CELLS',{'ZERO_ABS':d.CELLS['ZERO_ABS']})
    cell=tmp_path/'cells/ZERO_ABS';cell.mkdir(parents=True);pred=cell/'predictions';pred.mkdir()
    for i in range(439):(pred/(str(i).zfill(6)+'.json')).write_bytes(b'{"software_fixture":true}')
    result=dict(cell='ZERO_ABS',checkpoint_sha256='weights',queries=439,actual_latent_checks=dict(latent_zero_checks=439,posterior_calls=0),
                optimizer_updates=0,future_labels_or_phase_in_actor=False,metrics={},
                predictions_files_sha256={p.name:d.file_sha(p) for p in pred.iterdir()})
    (cell/'result.json').write_bytes(canonical(result))
    receipt=dict(source_pins_revalidated=True,queries=439,result_sha256=d.file_sha(cell/'result.json'))
    (cell/'completed.json').write_bytes(canonical(receipt));(pred/'000123.json').write_bytes(b'tampered')
    with pytest.raises(AuditRejected,match='prediction bytes'):d.summarize({'models':{'ZERO_ABS':{'checkpoint_sha256':'weights'}}},tmp_path/'summary',tmp_path)
    (cell/'completed.json').unlink()
    with pytest.raises(FileNotFoundError):d.summarize({'models':{'ZERO_ABS':{'checkpoint_sha256':'weights'}}},tmp_path/'summary',tmp_path)
