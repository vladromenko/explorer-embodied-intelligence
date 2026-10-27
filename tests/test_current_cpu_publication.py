"""No model: evidence-chain publication, signature/first-outcome tamper negatives."""
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

import pytest

from test_current_cpu_orchestrator import frozen

SCRIPTS=Path(__file__).parents[1]/'scripts'
sys.path.insert(0,str(SCRIPTS))
import qualify_current_cpu_sources as q
spec=importlib.util.spec_from_file_location('cpu_publication',SCRIPTS/'publish_current_cpu_sources.py')
publication=importlib.util.module_from_spec(spec);spec.loader.exec_module(publication)


def fixture(frozen,tmp_path,monkeypatch):
    root,commit,station,gate,output=frozen
    model=b'explicit software fixture, no real weights';weights=hashlib.sha256(model).hexdigest()
    for name in q.REFERENCE_NAMES.values():
        (root/'shared/training'/name).write_text(json.dumps(dict(optimizer_steps=8,checkpoint_sha256={'model.safetensors':weights})))
    q.git(root,'add','.');q.git(root,'-c','user.name=Software Fixture','-c','user.email=fixture@invalid','commit','-qm','Reference fixture bytes')
    plan=q.prepare_plan(root,output,commit=q.git(root,'rev-parse','HEAD'),station_sha256=station,gate_sha256=gate,label='20261027_PUBLICATION_FIXTURE')
    (output/'orchestrator-source.py').write_bytes((SCRIPTS/'qualify_current_cpu_sources.py').read_bytes())
    library=tmp_path/'library';policy=library/'lerobot/policies/diffusion/modeling_diffusion.py';policy.parent.mkdir(parents=True);policy.write_text('FIXTURE POLICY')
    runtime=dict(executable=sys.executable,executable_sha256=q.sha(Path(sys.executable).resolve()),packages={'lerobot':dict(location=str(library),version='FIXTURE')},official_sources_sha256={'lerobot/policies/diffusion/modeling_diffusion.py':q.sha(policy)})
    limits=dict(memory_max=4*q.GIB,memory_swap_max=0,cpu_quota_cores=2,path='explicit fixture')
    for phase in ('act','diffusion','fused106'):
        folder=output/phase;case=folder/'pytest'/'arbitrary-case';checkpoint=case/'checkpoint';checkpoint.mkdir(parents=True);(checkpoint/'model.safetensors').write_bytes(model)
        result=dict(trained=True,weights_changed=True,save_reload_passed=True,optimizer_steps=8,physical_io=False,reload_max_abs_error=0,checkpoint_dir=str(checkpoint),checkpoint_sha256={'model.safetensors':weights},source_kind='SIMULATION')
        if phase=='fused106':result.update(job_kind='rosmaster_skill_train',feature_dimension=106,output_action_dimension=9)
        else:result.update(action_contract_id='so101_follower_calibrated_position6_v1',algorithm='official ACT6 fixture')
        if phase=='diffusion':
            metadata=dict(implementation_sha256=dict(policy=q.sha(policy)),source_context=dict(diffusion_optimizer=dict(implementation_sha256={str(policy):q.sha(policy)})))
            signature=hashlib.sha256(json.dumps(metadata,sort_keys=True,allow_nan=False).encode()).hexdigest()
            result.update(backend='diffusion',training_resume=dict(signature=signature,resumed_from_step=4,optimizer_steps_executed_this_process=4))
            resume=case/'diffusion_resume';resume.mkdir();(resume/'checkpoint.pt').write_bytes(b'NOT TORCH: explicit signature fixture')
            pointer=dict(checkpoint='checkpoint.pt',sha256=q.sha(resume/'checkpoint.pt'),signature=signature,completed_steps=8);q.write(resume/'latest.json',pointer)
        filename={'act':'actual8-qualification.json','diffusion':'qualification.json','fused106':'result.json'}[phase]
        path=case/filename;q.write(path,result)
        before=dict(runtime=runtime,implementation_sha256=plan['source_sha256'][phase],auxiliary_source_sha256=plan['auxiliary_source_sha256'],launcher_sha256=plan['orchestrator_sha256'],limits=limits)
        failure=None if phase=='diffusion' else dict(error_type='QualificationRejected',detail=publication.KNOWN_LOCATOR_FAILURES[phase])
        after=dict(before,pytest_exit_code=0,failure=failure,elapsed_s=0,process_peak_RSS_bytes=0,cgroup_peak_memory_bytes=0,cgroup_swap_peak_bytes=0,cgroup_memory_events='FIXTURE, NOT MEASURED')
        q.write(folder/'runtime-before.json',before);q.write(folder/'runtime-after.json',after)
        if phase=='diffusion':q.write(folder/'resume-extraction.json',dict(qualification_result_sha256=q.sha(path),completed_steps=8,resume_metadata=metadata,resume_signature=signature,resume_checkpoint_sha256=pointer['sha256'],resume_pointer_sha256=q.sha(resume/'latest.json')))
    fake_python=tmp_path/'runtime/bin/python';fake_python.parent.mkdir(parents=True);(fake_python.parent.parent/'explorer-runtime-binding.json').write_text('FIXTURE')
    monkeypatch.setattr(q,'DP_PYTHON',fake_python)
    return output


def test_three_profile_publication_preserves_reporting_failures_and_original_bytes(frozen,tmp_path,monkeypatch):
    output=fixture(frozen,tmp_path,monkeypatch);original={str(p):q.sha(p) for p in output.rglob('*') if p.is_file()}
    destination=tmp_path/'publication';summary=publication.publish(output/'plan.json',output/'act',destination)
    assert summary['profiles']['act']['original_reporting_wrapper_exit_code']==1
    assert summary['profiles']['fused106']['original_reporting_wrapper_exit_code']==1
    assert summary['profiles']['diffusion']['original_reporting_wrapper_exit_code']==0
    assert not summary['actual_new_PID_controller_qualification_in_this_receipt']
    assert all(not row['actual_model_or_optimizer_executed_by_revalidator'] for row in summary['profiles'].values())
    assert all(q.sha(path)==expected for path,expected in original.items())
    assert q.read(destination/'DIFFUSION_NATIVE6_ACTIVE_BINDING.json')['schema']=='explorer_native6_diffusion_active_binding_v1'
    with pytest.raises(q.QualificationRejected,match='New publication'):publication.publish(output/'plan.json',output/'act',destination)


def test_unknown_failure_and_modified_resume_signature_cannot_be_published(frozen,tmp_path,monkeypatch):
    output=fixture(frozen,tmp_path,monkeypatch);after_path=output/'fused106/runtime-after.json';after=q.read(after_path)
    after['failure']['detail']='A real failed model';after_path.write_bytes(q.canonical(after))
    with pytest.raises(q.QualificationRejected,match='Unrecognized'):publication.publish(output/'plan.json',output/'act',tmp_path/'rejected')
    assert not (tmp_path/'rejected').exists()

    after['failure']['detail']=publication.KNOWN_LOCATOR_FAILURES['fused106'];after_path.write_bytes(q.canonical(after))
    extraction_path=output/'diffusion/resume-extraction.json';extraction=q.read(extraction_path);extraction['resume_metadata']['unmeasured']='tampered';extraction_path.write_bytes(q.canonical(extraction))
    with pytest.raises(q.QualificationRejected,match='checkpoint|signature'):publication.publish(output/'plan.json',output/'act',tmp_path/'rejected')
    assert not (tmp_path/'rejected').exists()


def test_native6_only_publication_does_not_read_or_claim_a_new_fused106_proof(frozen,tmp_path,monkeypatch):
    output=fixture(frozen,tmp_path,monkeypatch)
    # A nonselected producer has no newly measured result. Its old files stay
    # untouched and must not be adopted into this native6-only publication.
    (output/'fused106/runtime-after.json').unlink()
    summary=publication.publish(output/'plan.json',output/'act',tmp_path/'native6',native6_only=True)
    assert set(summary['profiles'])=={'act','diffusion'} and not summary['fused106_requalified']
    assert summary['native6_only'] and not list((tmp_path/'native6').glob('ROSMASTER_ACT106*'))
    assert (output/'fused106/runtime-before.json').is_file()
