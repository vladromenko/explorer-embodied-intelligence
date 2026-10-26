"""Pinned-source admission proof with immutable software-only receipt fixtures."""
import hashlib
import json
from pathlib import Path

import pytest

from explorer.qualification_bindings import (PIPELINES,PACKAGES,publish_gpu_binding,qualified_gpu_binding,
    resolve_job_release,pipeline_hashes,runtime_identity)
from explorer.store import Store


def releases(tmp_path):
    values=[]
    for letter in ('a','b'):
        folder=tmp_path/'releases'/(letter*40);folder.mkdir(parents=True)
        (folder/'RELEASE_COMMIT').write_text(letter*40+'\n')
        for name in set(name for names in PIPELINES.values() for name in names):
            path=folder/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text('software-fixture-'+letter+'-'+name)
        values.append(folder)
    return values


def runtime(tmp_path):
    root=tmp_path/'gpu-venv';executable=root/'bin/python';executable.parent.mkdir(parents=True)
    executable.write_bytes(b'software-fixture-interpreter-not-executed')
    (root/'pyvenv.cfg').write_text('version = software-fixture\n')
    for name in PACKAGES:
        package=root/'lib/python3.12/site-packages'/(name+'-fixture.dist-info');package.mkdir(parents=True)
        (package/'METADATA').write_text('Name: '+name+'\nVersion: fixture-1\n')
        (package/'RECORD').write_text('software-fixture-record-'+name)
    return executable


def qualification(tmp_path,release,kind='manipulation_student',name='latest.json'):
    path=tmp_path/name
    receipt={'passed':True,'optimizer_steps':8,'weights_changed':True,'scope':'SOFTWARE_FIXTURE_NO_ACTUAL_GPU_QUALIFICATION',
        'qualified_job_kinds':[kind],'pipeline_source_sha256':pipeline_hashes(release,kind),
        'state_dimension':9,'action_dimension':9,
        'framework_versions':{'torch':'fixture-1','torchvision':'fixture-1'},'lerobot':'fixture-1'}
    path.write_text(json.dumps(receipt));return path


def test_runtime_identity_follows_literal_shared_dependencies_and_preserves_gpu_package_precedence(tmp_path):
    import shutil
    executable=runtime(tmp_path)
    primary=executable.parent.parent/'lib/python3.12/site-packages'
    linked=tmp_path/'shared-venv/lib/python3.12/site-packages';linked.mkdir(parents=True)
    for name in ('lerobot','mujoco'):
        shutil.move(str(primary/(name+'-fixture.dist-info')),linked)
    shadow=linked/'torch-cpu.dist-info';shadow.mkdir()
    (shadow/'METADATA').write_text('Name: torch\nVersion: wrong-CPU-shadow\n')
    path_file=primary/'shared-dependencies.pth';path_file.write_text(str(linked)+'\n')
    identity=runtime_identity(executable)
    assert identity['distribution_records']['torch']['version']=='fixture-1'
    assert identity['distribution_records']['lerobot']['metadata_path'].startswith(str(linked))
    assert identity['site_path_files_sha256'][str(path_file)]==hashlib.sha256(path_file.read_bytes()).hexdigest()
    before=identity
    path_file.write_text(str(linked)+'\n# declared environment changed\n')
    assert runtime_identity(executable)!=before


def test_same_site_duplicate_distribution_is_still_rejected(tmp_path):
    executable=runtime(tmp_path)
    duplicate=executable.parent.parent/'lib/python3.12/site-packages/torch-other.dist-info'
    duplicate.mkdir();(duplicate/'METADATA').write_text('Name: torch\nVersion: ambiguous-other\n')
    with pytest.raises(ValueError,match='Ambiguous'):
        runtime_identity(executable)


def test_qualified_old_source_remains_admitted_after_deploy_and_mutable_latest_receipt_change(tmp_path):
    old,current=releases(tmp_path);python=runtime(tmp_path);registry=tmp_path/'qualification-registry'
    receipt=qualification(tmp_path,old)
    first=publish_gpu_binding(receipt,old,'manipulation_student',python,registry)
    store=Store(tmp_path/'state');job=store.create_job('manipulation_student','01',{'device':'cuda'})
    assert resolve_job_release({'root':str(old)},store,job)==old
    qualification(tmp_path,current)  # Existing mutable latest receipt is replaced.
    publish_gpu_binding(receipt,current,'manipulation_student',python,registry)
    resumed=resolve_job_release({'root':str(current)},store,store.job(job['id']))
    assert resumed==old
    selected=qualified_gpu_binding(store.job(job['id']),resumed,python,registry)
    assert selected['execution_commit']==old.name and selected['binding_sha256']==first['binding_sha256']
    assert selected['qualification_receipt_sha256']==first['binding']['qualification_receipt_sha256']


def test_current_source_receipt_cannot_admit_an_unqualified_older_pinned_pipeline(tmp_path):
    old,current=releases(tmp_path);python=runtime(tmp_path);registry=tmp_path/'qualification-registry'
    publish_gpu_binding(qualification(tmp_path,current),current,'manipulation_student',python,registry)
    with pytest.raises(ValueError,match='No immutable'):
        qualified_gpu_binding({'kind':'manipulation_student'},old,python,registry)
    with pytest.raises(ValueError,match='source differs'):
        publish_gpu_binding(qualification(tmp_path,current),old,'manipulation_student',python,registry)


def test_shared_student_source_or_installed_runtime_record_changes_revoke_matching_admission(tmp_path):
    old,_=releases(tmp_path);python=runtime(tmp_path);registry=tmp_path/'qualification-registry'
    publish_gpu_binding(qualification(tmp_path,old),old,'manipulation_student',python,registry)
    student=old/'01-autonomous-learning/src/explorer_sim/student.py';original=student.read_bytes()
    student.write_bytes(original+b'changed-shared-normalization')
    with pytest.raises(ValueError,match='pinned source'):
        qualified_gpu_binding({'kind':'manipulation_student'},old,python,registry)
    student.write_bytes(original)
    record=next((python.parent.parent/'lib').glob('python*/site-packages/torch-*.dist-info/RECORD'))
    record.write_text('installed-framework-record-changed')
    with pytest.raises(ValueError,match='installed runtime'):
        qualified_gpu_binding({'kind':'manipulation_student'},old,python,registry)


def test_backend_only_passed_flag_is_not_a_new_pipeline_qualification(tmp_path):
    old,_=releases(tmp_path);python=runtime(tmp_path)
    path=tmp_path/'backend-only.json';path.write_text(json.dumps({'passed':True,'optimizer_steps':2,'weights_changed':True,
        'scope':'Six-axis synthetic backend only, no source lineage'}))
    with pytest.raises(ValueError,match='backend-only'):
        publish_gpu_binding(path,old,'sim_pilot',python,tmp_path/'registry')


def test_actual_nine_axis_result_source_manifest_is_archived_and_survives_original_path_removal(tmp_path):
    old,_=releases(tmp_path);python=runtime(tmp_path);registry=tmp_path/'registry'
    proof=tmp_path/'actual-result-fixture.json'
    hashes={'/retired-worktree/'+name:value for name,value in pipeline_hashes(old,'manipulation_student').items()}
    proof.write_text(json.dumps({'provenance':{'context':{'source_sha256':hashes,'versions':{'numpy':'fixture-1'}}}}))
    proof_sha=hashlib.sha256(proof.read_bytes()).hexdigest()
    receipt=tmp_path/'nine-axis.json';receipt.write_text(json.dumps({'passed':True,'optimizer_steps':8,'weights_changed':True,
        'scope':'SOFTWARE_FIXTURE_OF_EXISTING_NINE_AXIS_RECEIPT','state_dimension':9,'action_dimension':9,
        'official_saved_processors_executed':True,'result_path':str(proof),'result_sha256':proof_sha}))
    binding=publish_gpu_binding(receipt,old,'manipulation_student',python,registry)
    receipt.unlink();proof.unlink()
    selected=qualified_gpu_binding({'kind':'manipulation_student'},old,python,registry)
    assert selected['binding_sha256']==binding['binding_sha256']
    assert (registry/'proofs'/(proof_sha+'.json')).is_file()
    with pytest.raises(ValueError,match='backend-only'):
        original=json.loads((registry/'receipts'/(selected['qualification_receipt_sha256']+'.json')).read_text())
        copied=tmp_path/'source-receipt.json';copied.write_text(json.dumps(original))
        # A ninth-axis manipulation trace does not silently qualify the reach pipeline.
        original['result_path']=str(registry/'proofs'/(proof_sha+'.json'));copied.write_text(json.dumps(original))
        publish_gpu_binding(copied,old,'sim_pilot',python,registry)


def test_archived_receipt_or_binding_content_tampering_blocks_dispatch_selection(tmp_path):
    old,_=releases(tmp_path);python=runtime(tmp_path);registry=tmp_path/'registry'
    created=publish_gpu_binding(qualification(tmp_path,old),old,'manipulation_student',python,registry)
    receipt=registry/'receipts'/(created['binding']['qualification_receipt_sha256']+'.json')
    receipt.write_text('{"passed":true}')
    with pytest.raises(ValueError,match='receipt changed'):
        qualified_gpu_binding({'kind':'manipulation_student'},old,python,registry)


def test_missing_original_pinned_release_cannot_fall_back_to_current(tmp_path):
    old,current=releases(tmp_path);store=Store(tmp_path/'state');job=store.create_job('manipulation_student','01',{})
    resolve_job_release({'root':str(old)},store,job)
    (old/'RELEASE_COMMIT').unlink()
    with pytest.raises(ValueError,match='commit-addressed'):
        resolve_job_release({'root':str(current)},store,job)


def test_pinned_release_cannot_silently_follow_an_alias_to_another_commit(tmp_path):
    import shutil
    old,current=releases(tmp_path);store=Store(tmp_path/'state');job=store.create_job('manipulation_student','01',{})
    resolve_job_release({'root':str(old)},store,job)
    shutil.rmtree(old);old.symlink_to(current,target_is_directory=True)
    with pytest.raises(ValueError,match='identity changed'):
        resolve_job_release({'root':str(current)},store,job)
