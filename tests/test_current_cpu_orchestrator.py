"""No models: immutable recipe/source/resource guards before expensive work."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest

PATH=Path(__file__).parents[1]/'scripts/qualify_current_cpu_sources.py'
spec=importlib.util.spec_from_file_location('current_cpu_recipe',PATH)
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)


@pytest.fixture
def frozen(tmp_path):
    root=tmp_path/'source';root.mkdir()
    files=set(module.ADMISSION+module.COMMON+module.EXTRA_ACT+module.EXTRA_DP+module.FUSED+module.CONTROLLER+(module.GATE,'conftest.py','tests/test_skill_workflow.py','tests/test_embodied_episode.py'))
    files.update(value.split('::',1)[0] for value in module.TESTS.values())
    for name in files:
        path=root/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text('SOFTWARE_SOURCE_FIXTURE '+name+'\n')
    proofs=root/'shared/training';proofs.mkdir(parents=True)
    for name in module.REFERENCE_NAMES.values():
        (proofs/name).write_text(json.dumps(dict(optimizer_steps=8,checkpoint_sha256={'model.safetensors':'a'*64})))
    prior={name:module.sha(root/name) for name in set(module.ADMISSION+module.COMMON+module.EXTRA_DP)}
    prior[module.STATION]='0'*64
    (proofs/'DIFFUSION_NATIVE6_20261026_CURRENT_PROFILE4_SOURCE_BINDING.json').write_text(json.dumps(dict(implementation_sha256=prior)))
    subprocess.run(['git','init','-q',str(root)],check=True)
    subprocess.run(['git','-C',str(root),'add','.'],check=True)
    subprocess.run(['git','-C',str(root),'-c','user.name=Software Fixture','-c','user.email=fixture@invalid','commit','-qm','Immutable fixture'],check=True)
    commit=module.git(root,'rev-parse','HEAD')
    return root,commit,module.sha(root/module.STATION),module.sha(root/module.GATE),tmp_path/'new-qualification'


def plan(frozen):
    root,commit,station,gate,output=frozen
    return module.prepare_plan(root,output,commit=commit,station_sha256=station,gate_sha256=gate,label='20261027_STATION_SOURCE')


def test_plan_exact_original_shapes_21_sources_station_and_oldweights_without_importing_policies(frozen):
    document=plan(frozen);output=frozen[-1]
    assert len(document['source_sha256']['diffusion'])==21 and module.STATION in document['source_sha256']['diffusion']
    assert {'00-control-center/src/explorer/source_demo_workflow.py','shared/explorer_shared/native_capture_approval.py',
        'shared/explorer_shared/capture_timeline.py'}<=set(document['source_sha256']['diffusion'])
    assert set(document['formal_DP_changes_from_R2'])=={module.STATION}
    assert document['stages']['act']['parameters']==dict(device='cpu',state_dim=6,action_dim=6,act_steps=8,image_size=32,batch_size=2,chunk_size=4)
    assert document['stages']['diffusion']['parameters']['resumed_from_step']==4
    assert document['stages']['fused106']['parameters']['state_dim']==106 and document['stages']['fused106']['parameters']['action_dim']==9
    assert document['actual_model_or_optimizer_executed'] is False and not document['hardware_io']
    assert module.read_plan(output/'plan.json')==document
    assert not (output/'act').exists() and not (output/'diffusion').exists()


def test_recipe_admission_graph_pins_exactly_the_rules_used_by_actual_consumers():
    from explorer.so101_dataset_admission import admission_sources
    root=PATH.parents[1]
    assert set(module.ADMISSION)=={path.relative_to(root).as_posix() for path in admission_sources()}


def test_missing_final_hash_dirty_source_or_reused_output_cannot_prepare(frozen):
    root,commit,station,gate,output=frozen
    with pytest.raises(module.QualificationRejected,match='SHA256'):
        module.prepare_plan(root,output,commit=commit,station_sha256=None,gate_sha256=gate,label='20261027_STATION_SOURCE')
    plan(frozen)
    with pytest.raises(module.QualificationRejected,match='New output root'):plan(frozen)
    (root/module.STATION).write_text('changed')
    with pytest.raises(module.QualificationRejected,match='clean'):module.assert_source(root,commit,station,gate)


@pytest.mark.parametrize('change',[lambda p:p['stages']['act'].update(test='fast_fake_test.py'),
    lambda p:p['stages']['diffusion']['parameters'].update(state_dim=12),
    lambda p:p['stages']['act'].update(executable='/arbitrary/python'),
    lambda p:p['stages']['fused106']['limits'].update(memory_max_bytes=8*module.GIB),
    lambda p:p.update(orchestrator_sha256='0'*64)])
def test_resealed_or_edited_recipe_cannot_change_test_dimensions_executable_or_resources(frozen,change):
    document=plan(frozen);change(document);path=frozen[-1]/'plan.json';path.write_text(json.dumps(document))
    with pytest.raises(module.QualificationRejected):module.read_plan(path)


def test_wrong_resources_abort_before_pytest_or_any_model_work(frozen,monkeypatch):
    plan(frozen)
    def refused():raise module.QualificationRejected('Own CPU2/4GiB/swap0 cgroup required')
    monkeypatch.setattr(module,'limits',refused)
    with pytest.raises(module.QualificationRejected,match='cgroup'):module.run_phase(frozen[-1]/'plan.json','act')
    assert not (frozen[-1]/'act').exists()


def test_old_reference_or_math_reloads_cannot_be_silently_relabelled(frozen):
    plan(frozen);root=frozen[0]
    path=root/'shared/training'/module.REFERENCE_NAMES['act'];path.write_text('{}')
    with pytest.raises((module.QualificationRejected,KeyError)):module.read_plan(frozen[-1]/'plan.json')
    result=dict(optimizer_steps=8,trained=True,weights_changed=True,save_reload_passed=True,physical_io=False,
        reload_max_abs_error=0,source_kind='SIMULATION',checkpoint_sha256={'model.safetensors':'b'*64})
    with pytest.raises(module.QualificationRejected,match='exact weights'):
        module.validate_result(result,'act',dict(model_weights_sha256='a'*64))
    result['checkpoint_sha256']['model.safetensors']='a'*64;result['reload_max_abs_error']=1e-12
    with pytest.raises(module.QualificationRejected,match='tolerance'):module.validate_result(result,'act',dict(model_weights_sha256='a'*64))


def test_cli_plan_import_is_stdlib_only_no_torch_or_mujoco():
    code="import runpy,sys;runpy.run_path("+repr(str(PATH))+");assert 'torch' not in sys.modules;assert 'mujoco' not in sys.modules;assert 'numpy' not in sys.modules"
    outcome=subprocess.run([sys.executable,'-c',code],text=True,capture_output=True)
    assert outcome.returncode==0,outcome.stdout+outcome.stderr


@pytest.mark.parametrize('phase,directory,filename',[
    ('act','test_real_official_ACT6_eight_0','actual8-qualification.json'),
    ('diffusion','test_same_accepted_whole_skill0','qualification.json'),
    ('fused106','test_actual_eight_official_ACT0/state/jobs/fixture','result.json')])
def test_result_locator_typed_artifact_actual_truncated_names_aliases_ambiguity_and_escape(tmp_path,phase,directory,filename):
    output=tmp_path/phase;root=output/'pytest';path=root/directory;path.mkdir(parents=True)
    body=dict(trained=True,weights_changed=True,save_reload_passed=True,optimizer_steps=8,checkpoint_sha256={},physical_io=False)
    if phase=='fused106':body.update(job_kind='rosmaster_skill_train',feature_dimension=106,output_action_dimension=9)
    else:body.update(action_contract_id='so101_follower_calibrated_position6_v1',algorithm='official ACT6 fixture',backend=None if phase=='act' else 'diffusion')
    result=path/filename;result.write_text(json.dumps(body))
    (root/'arbitrary_current_alias').symlink_to(path,target_is_directory=True)
    assert module.one_result(output,phase)==result.resolve()
    another=root/'arbitrary_1';another.mkdir();(another/filename).write_text(json.dumps(body))
    with pytest.raises(module.QualificationRejected,match='found 2'):module.one_result(output,phase)
    (another/filename).unlink();another.rmdir();(root/'arbitrary_current_alias').unlink()
    outside=tmp_path/'outside';outside.mkdir();(outside/filename).write_text(json.dumps(body))
    (root/'arbitrary_current_alias').symlink_to(outside,target_is_directory=True)
    with pytest.raises(module.QualificationRejected,match='escaped'):module.one_result(output,phase)


def test_result_locator_rejects_wrong_phase_schema_even_with_expected_filename(tmp_path):
    output=tmp_path/'fused';root=output/'pytest';root.mkdir(parents=True)
    (root/'result.json').write_text(json.dumps(dict(trained=True,weights_changed=True,save_reload_passed=True,
        optimizer_steps=8,checkpoint_sha256={},physical_io=False,job_kind='rosmaster_skill_train',feature_dimension=9,output_action_dimension=9)))
    with pytest.raises(module.QualificationRejected,match='found 0'):module.one_result(output,'fused106')
