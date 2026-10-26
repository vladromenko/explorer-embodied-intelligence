"""Recorded-study integrity and real independent restore, with no simulator."""
import base64
from dataclasses import replace
import hashlib
import io
import json
from pathlib import Path
import tarfile

import pytest

from explorer_shared.study_evidence import (ContextFile,StudyDefinition,StudyRejected,
    canonical,inspect_study,sha_file,trusted_benchmark)
from explorer_shared.study_bundle import (archive_study_bundle,export_study_bundle,
    restore_study_bundle,unpack_study_archive,verify_study_bundle)
from explorer.study_registry import registered_definition,registered_studies

BENCHMARK=Path(__file__).resolve().parents[1]/'research/benchmark/evaluate.py'
BENCHMARK_SHA='352c62c3d58207dadb368f9250b5b14195addf12046d533077b9451f1d715f1a'
PNG=base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aD1sAAAAASUVORK5CYII=')


def write(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_bytes(value if isinstance(value,bytes) else canonical(value))
    return sha_file(path)


@pytest.fixture
def study(tmp_path):
    root=tmp_path/'original';campaign=root/'campaign';campaign.mkdir(parents=True)
    evaluator=root/'evaluator.py';write(evaluator,BENCHMARK.read_bytes())
    benchmark=trusted_benchmark(ContextFile(evaluator,BENCHMARK_SHA,'trusted_evaluator'))
    scenarios=[dict(id='synthetic-success',seed=1),dict(id='synthetic-failure',seed=2)]
    protocol=dict(protocol_id='synthetic-no-physics',schema_version='1.0',frozen_at='synthetic',
        task_ids=['placement'],conditions=['SCRIPTED_SIM'],success_predicates={'placement':'terminal'},
        exclusion_reasons=[],physical_io=False,learned_policy_claimed=False,scenarios=scenarios,
        benchmark_implementation_sha256=BENCHMARK_SHA,target_phase_criteria={'settle':['settled_in_goal']},
        split_manifest=dict(train=dict(episode_ids=['source-train'],session_ids=['source-session']),
            development=dict(episode_ids=[s['id'] for s in scenarios],session_ids=['dev1','dev2']),
            sealed_test=dict(episode_ids=[],session_ids=[])))
    protocol['sha256']=benchmark.protocol_hash(protocol)
    protocol_file_sha=write(campaign/'frozen-protocol.json',protocol);trials=[];audit_rows=[]
    for number,scenario in enumerate(scenarios):
        identity=scenario['id'];base=campaign/identity;success=number==0
        write(base/'trial-started.json',dict(scenario=scenario));write(base/'parameters.json',dict(scenario=scenario))
        source_trace_sha=write(base/'source-trace.json',dict(schema='synthetic_geometric_source',partition='train'))
        mesh_sha=write(base/'source-archive/objects/mesh.stl',b'synthetic mesh bytes; never executed')
        archive=dict(schema='explorer_source_archive_v1',scope='explicit_source_inventory_bytes_not_complete_OS_or_scientific_success',
            context=dict(scenario_id=identity,frozen_protocol_sha256=protocol['sha256'],physical_io=False,source_model_learned=False),
            files=[dict(file='objects/mesh.stl',sha256=mesh_sha,bytes=(base/'source-archive/objects/mesh.stl').stat().st_size,
                original_locator='/missing/original/mesh.stl',resolved_path='/missing/original/mesh.stl')])
        archive['total_source_bytes']=sum(row['bytes'] for row in archive['files'])
        archive_sha=write(base/'source-archive/manifest.json',archive)
        write(base/'source-verification.json',dict(originals_rechecked=True,synthetic=True))
        scene_sha=write(base/'target-model.xml',b'<mujoco><asset><mesh file="/missing/original/mesh.stl"/></asset></mujoco>')
        stamp=lambda tick:dict(clock_domain=identity,boot_id=identity,monotonic_ns=tick)
        renderer=dict(frame_id=identity+':0',stamp=stamp(10),camera_id='synthetic')
        dispatch=dict(dispatched_action9=[0.]*9,requested_action9=[0.]*9,pre_action_stamp=stamp(10),
            dispatch_stamp=stamp(20),physical_io=False,finite_dynamics_step_completed=True,
            attainment_inferred_from_commands=False,native_integration_state=[0.,1.,2.])
        rgb_sha=write(base/'target/rgb/000000.png',PNG)
        trace=[dict(index=0,dispatch=dispatch,requested_action9=[0.]*9,preceding_command9=[0.]*9,
            actual_dispatched_action9=[0.]*9,phase_id='settle',learned_target_actor_executed=False,
            renderer_receipt=renderer,target_rgb_file='rgb/000000.png',target_rgb_sha256=rgb_sha)]
        write(base/'target/observations/000000.json',dict(preceding_command9=[0.]*9,renderer_receipt=renderer,observation_stamp=stamp(10)))
        write(base/'target/requests/000000.json',dict(actual_requested_action9=[0.]*9,renderer_receipt=renderer,
            target_rgb_file='rgb/000000.png',target_rgb_sha256=rgb_sha,phase_id='settle'))
        write(base/'target/dispatch/000000.json',dispatch)
        trace_sha=write(base/'target/target-trace.json',trace)
        report=dict(source='target_finite_physics_evaluator',independent_of_action_commands=True,end_to_end_success=success,
            terminal_goal_verified=success,object_facts=dict(settled_in_goal=success,withdrawn=True),
            per_phase=dict(settle=dict(success=success)))
        result=dict(source_kind='simulation',physical_io=False,learned_target_actor_executed=False,seed=scenario['seed'],
            source_method='SCRIPTED_SIM',source_model_full_skill_claim=False,target_trace_sha256=trace_sha,frames=1,
            source_manifest_sha256=source_trace_sha,source_files_sha256={'/missing/original/mesh.stl':mesh_sha},
            target_provider=dict(target_model_sha256=scene_sha,cad_mesh_sha256={'/missing/original/mesh.stl':mesh_sha}),
            independent_evaluation=report,failure=None if success else 'settle failed',full_target_simulation_success=success,
            source_split=dict(partition='train'),optimizer_partition_allowed=True,eligible_as_simulation_expert=success)
        result_sha=write(base/'target/result.json',result)
        trial=dict(trial_id=identity,run_id='synthetic',task_id='placement',condition='SCRIPTED_SIM',
            protocol_sha256=protocol['sha256'],source_domain='SIMULATION',checkpoint_sha256='a'*64,
            checkpoint_basis='SCRIPTED_SIM_implementation_SHA_not_a_learned_checkpoint',evidence_artifact_ids=[identity],
            validity='VALID',scenario=scenario,outcome='SUCCESS' if success else 'FAILURE',
            raw_result=dict(frames=1,failure=result['failure'],target_result_sha256=result_sha,per_phase=report['per_phase'],terminal=report['object_facts']))
        write(base/'trial.json',trial);trials.append(trial)
        audit_rows.append(dict(scenario=scenario,result_sha256=result_sha,target_trace_sha256=trace_sha,
            source_archive_manifest_sha256=archive_sha,source_archive_files=1,full_task_success=success))
    trials_sha=write(campaign/'trials.jsonl',b'\n'.join(canonical(row) for row in trials)+b'\n')
    report_sha=write(campaign/'benchmark-report.json',benchmark.summarize_trials(protocol,trials))
    audit=root/'audit.json';audit_sha=write(audit,dict(frozen_protocol_sha256=protocol['sha256'],
        frozen_protocol_file_sha256=protocol_file_sha,trials_sha256=trials_sha,benchmark_report_sha256=report_sha,attempts=audit_rows))
    return StudyDefinition('synthetic-study',campaign,protocol['sha256'],ContextFile(audit,audit_sha,'audit'),
        ContextFile(evaluator,BENCHMARK_SHA,'trusted_evaluator'))


def test_all_outcomes_target_partition_and_cad_accounted(study):
    report=inspect_study(study).document
    assert [row['outcome'] for row in report['attempts']]==['SUCCESS','FAILURE']
    assert report['benchmark_recomputation']['verified']
    assert report['benchmark_recomputation']['report']['groups'][0]['counts']==dict(SUCCESS=1,FAILURE=1,UNKNOWN=0)
    assert report['completeness']=='PARTIAL' and report['target_optimizer_allowed'] is False
    assert all(row['legacy_raw_admission']['optimizer_partition_allowed'] is True and row['target_optimizer_allowed'] is False for row in report['attempts'])
    assert all(row['scene_asset_bindings'] and row['native_integration_state_length']==3 for row in report['attempts'])
    assert len([gap for gap in report['dependency_gaps'] if gap['required_for_original_reexecution']])==4


def test_independent_tar_restore_without_originals(study,tmp_path):
    inspection=inspect_study(study);original={name:sha_file(path) for name,path in inspection.sources.items()}
    bundle=tmp_path/'bundle';receipt=export_study_bundle(inspection,bundle)
    archive=tmp_path/'study.tar';tar_receipt=archive_study_bundle(bundle,archive,expected_manifest_sha256=receipt['manifest_sha256'])
    moved=study.campaign_root.parent.rename(tmp_path/'original-now-unavailable')
    assert not study.campaign_root.exists()
    unpacked=tmp_path/'unpacked'
    unpack_study_archive(archive,unpacked,expected_archive_sha256=tar_receipt['archive_sha256'],expected_manifest_sha256=receipt['manifest_sha256'])
    restored=tmp_path/'restored';verified=restore_study_bundle(unpacked,restored,expected_manifest_sha256=receipt['manifest_sha256'])
    assert verified['benchmark_recomputed'] and verified['completeness']=='PARTIAL'
    assert verified['original_paths_read'] is False and verified['physics_executed'] is False
    assert {name:sha_file(restored/name) for name in original}==original
    assert (moved/'campaign/synthetic-failure/target/result.json').is_file()
    assert not list(restored.rglob('__pycache__'))


@pytest.mark.parametrize('member',['synthetic-success/target/rgb/000000.png','synthetic-failure/source-archive/objects/mesh.stl','frozen-protocol.json'])
def test_changed_original_rejected(study,member):
    path=study.campaign_root/member;path.write_bytes(path.read_bytes()+b'changed')
    with pytest.raises((StudyRejected,ValueError)):inspect_study(study)


def test_missing_negative_receipt_stays_partial_never_excluded(study):
    (study.campaign_root/'synthetic-failure/target/result.json').unlink()
    report=inspect_study(study).document
    assert len(report['attempts'])==2 and report['attempts'][1]['outcome']=='UNKNOWN'
    assert report['benchmark_recomputation']['verified'] is False
    assert any(row['code']=='RECORDED_ATTEMPT_FILES_MISSING' for row in report['dependency_gaps'])


def test_unregistered_started_attempt_retained(study):
    write(study.campaign_root/'additional-started/trial-started.json',dict(outcome='UNKNOWN'))
    report=inspect_study(study).document
    assert any(row['code']=='UNREGISTERED_STARTED_ATTEMPT' for row in report['dependency_gaps'])
    assert any(row['logical_path']=='campaign/additional-started/trial-started.json' for row in report['file_manifest'])
    assert len(report['recorded_attempt_ids'])==2


def test_tampered_object_or_unaccounted_file_rejected(study,tmp_path):
    bundle=tmp_path/'bundle';receipt=export_study_bundle(inspect_study(study),bundle)
    extra=bundle/'unaccounted';extra.write_bytes(b'new')
    with pytest.raises(StudyRejected,match='Unaccounted'):verify_study_bundle(bundle,expected_manifest_sha256=receipt['manifest_sha256'])
    extra.unlink();obj=next((bundle/'objects').iterdir());obj.write_bytes(b'changed')
    with pytest.raises(StudyRejected,match='object bytes'):verify_study_bundle(bundle,expected_manifest_sha256=receipt['manifest_sha256'])


@pytest.mark.parametrize('field,value',[('completeness','COMPLETE'),('target_optimizer_allowed',True),('learned_policy_executed',True)])
def test_self_hashed_false_claim_cannot_pass_restore(study,tmp_path,field,value):
    bundle=tmp_path/'bundle';export_study_bundle(inspect_study(study),bundle)
    manifest=json.loads((bundle/'manifest.json').read_bytes());manifest['inspection'][field]=value
    body={k:v for k,v in manifest['inspection'].items() if k!='inspection_sha256'}
    manifest['inspection']['inspection_sha256']=hashlib.sha256(canonical(body)).hexdigest()
    digest=write(bundle/'manifest.json',manifest)
    with pytest.raises(StudyRejected):restore_study_bundle(bundle,tmp_path/'restored',expected_manifest_sha256=digest)


@pytest.mark.parametrize('name,kind',[('../escape','file'),('/absolute','file'),('objects/'+'0'*64+'.blob','symlink')])
def test_tar_traversal_and_links_rejected(tmp_path,name,kind):
    archive=tmp_path/'bad.tar'
    with tarfile.open(archive,'w') as stream:
        info=tarfile.TarInfo(name)
        if kind=='symlink':info.type=tarfile.SYMTYPE;info.linkname='/etc/passwd';stream.addfile(info)
        else:info.size=1;stream.addfile(info,io.BytesIO(b'x'))
    with pytest.raises(StudyRejected):unpack_study_archive(archive,tmp_path/'unpacked',expected_archive_sha256=sha_file(archive),expected_manifest_sha256='0'*64)
    assert not (tmp_path/'escape').exists()


def test_only_server_registered_study_ids_and_allowlisted_evaluator(study,tmp_path):
    assert len(registered_studies())==1 and 'campaign_root' not in registered_studies()[0]
    for identity in ['/etc/passwd','../campaign',dict(path='campaign')]:
        with pytest.raises(StudyRejected):registered_definition(identity)
    untrusted=tmp_path/'untrusted.py';write(untrusted,b'raise RuntimeError("must never execute")')
    with pytest.raises(StudyRejected):inspect_study(replace(study,benchmark=ContextFile(untrusted,sha_file(untrusted),'untrusted')))
