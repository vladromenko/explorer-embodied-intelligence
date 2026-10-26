"""Temporary producer-shaped RGB/9D fixtures; no training or hardware operations."""
import copy
import importlib.util
import json
from pathlib import Path
import sys

import numpy as np
import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'00-control-center/src'),str(ROOT/'01-autonomous-learning/src'),str(ROOT/'shared')]
from explorer.mobile_dataset_evidence import inspect_mobile_import,mobile_evidence_summary,file_sha256
from explorer_sim.mobile_observation_adapter import convert_mobile_observation

SPEC=importlib.util.spec_from_file_location('existing_mobile_observation_fixtures',ROOT/'01-autonomous-learning/tests/test_mobile_observation_adapter.py')
FIX=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(FIX)


def converted_fixture(tmp_path,*,unknown=False,count=14,outcome='success',split='train'):
    path,metadata,rows=FIX.fixture(tmp_path/'original',count=count,unknown=unknown,outcome=outcome,split=split)
    for row in rows:
        camera=copy.deepcopy(row['camera_metadata_raw']);camera['camera_id']='software-camera-fixture-not-hardware'
        FIX.change_raw(path.parent,row,'camera-frame.json',camera)
    FIX.write_rows(path.parent,rows)
    conversion=convert_mobile_observation(path,tmp_path/'converted')
    job={'id':'fixture-import','kind':'mobile_import','project_id':'02','status':'completed','config':{'episode_id':metadata['id']},
        'result':{'episode_id':metadata['id'],'observation_manifest':str(path),'observation_only':not conversion['dataset_available'],
            'frames':conversion['frames'] if conversion['dataset_available'] else count,'dataset_manifest':conversion['dataset_manifest'],
            'qualification':conversion['qualification'],'training_admission':conversion['training_admission']}}
    return job,conversion,path


def test_complete_converted_RGB9D_contract_is_bound_to_actual_retained_originals(tmp_path):
    job,conversion,path=converted_fixture(tmp_path)
    before={str(p):file_sha256(p) for p in tmp_path.rglob('*') if p.is_file()}
    evidence=inspect_mobile_import(job,managed_root=tmp_path,include_manifest=True)
    assert evidence['legacy_RGB9D_contract_admitted'],evidence['reasons']
    assert not evidence['admitted_for_training'] and not evidence['mobile_embodied']['admitted_for_training']
    assert 'mobile_embodied_admission_denied' in evidence['reasons']
    assert evidence['rgb_verified'] and evidence['action9_verified'] and evidence['causal_verified']
    assert evidence['retained_originals_verified'] and evidence['source_quality_usable'] and evidence['operator_success']
    assert evidence['raw_frames_recorded']==evidence['raw_rgb_verified_count']==14
    assert evidence['camera_id']=='software-camera-fixture-not-hardware'
    assert evidence['action_names'][-3:]==['vx','vy','wz']
    assert evidence['episodes'][0]['state_shape'][1]==evidence['episodes'][0]['action_shape'][1]==9
    assert evidence['episodes'][0]['RGB_shape'][-1]==3
    assert evidence['original_session_id'] is None and 'original_operator_session_identity_not_recorded' in evidence['missing_facts']
    assert not evidence['joint_measurements_claimed'] and not evidence['dispatch_confirmed'] and not evidence['physical_success_validated']
    assert not evidence['visual_ACT']['trained_RGB_model_verified']
    assert before=={str(p):file_sha256(p) for p in tmp_path.rglob('*') if p.is_file()}


def test_realistic_unknown_arm_raw_capture_keeps_RGB_but_never_becomes_expert_dataset(tmp_path):
    job,conversion,path=converted_fixture(tmp_path,unknown=True,count=9,outcome='unknown')
    evidence=inspect_mobile_import(job,managed_root=tmp_path)
    assert evidence['completed_recording'] and evidence['raw_frames_recorded']==9 and evidence['raw_rgb_verified_count']==9
    assert not evidence['manifest_path'] and not evidence['rgb_verified'] and not evidence['action9_verified']
    assert not evidence['admitted_for_training'] and not evidence['operator_success']
    assert any('UNKNOWN_AFTER_REBOOT' in reason for reason in evidence['reasons'])
    assert 'observation_only_no_admitted_dataset' in evidence['reasons']


def change_episode(conversion,mutate):
    manifest_path=Path(conversion['dataset_manifest']);manifest=json.loads(manifest_path.read_text());descriptor=manifest['episodes'][0]
    archive=manifest_path.parent/descriptor['file']
    with np.load(archive,allow_pickle=False) as raw:data={key:raw[key].copy() for key in raw.files}
    mutate(data);np.savez_compressed(archive,**data);descriptor['sha256']=file_sha256(archive)
    manifest_path.write_text(json.dumps(manifest))


def test_future_frame_and_wrong_RGB_cannot_be_admitted_even_when_new_container_SHA_is_declared(tmp_path):
    first=tmp_path/'future';first.mkdir();job,conversion,path=converted_fixture(first)
    change_episode(conversion,lambda data:data['frame_wall_unix_ns'].__setitem__(-1,data['action_wall_unix_ns'][-1]+1))
    evidence=inspect_mobile_import(job,managed_root=first)
    assert not evidence['admitted_for_training'] and any('causal' in reason.lower() for reason in evidence['reasons'])
    second=tmp_path/'wrong-image';second.mkdir();job,conversion,path=converted_fixture(second)
    change_episode(conversion,lambda data:data['rgb'].__setitem__((0,0,0,0),data['rgb'][0,0,0,0]^255))
    evidence=inspect_mobile_import(job,managed_root=second)
    assert not evidence['admitted_for_training'] and any('exact retained camera JPEG' in reason for reason in evidence['reasons'])


def test_failure_unknown_and_original_development_stay_out_of_BC_but_keep_diagnostic_data(tmp_path):
    for index,(outcome,split) in enumerate([('failure','train'),('unknown','train'),('success','development')]):
        folder=tmp_path/str(index);folder.mkdir();job,conversion,path=converted_fixture(folder,outcome=outcome,split=split)
        evidence=inspect_mobile_import(job,managed_root=folder)
        assert evidence['rgb_verified'] and evidence['action9_verified'] and evidence['causal_verified']
        assert not evidence['admitted_for_training']
        if split=='development':
            assert evidence['source_quality_usable'] and evidence['operator_success']
            assert evidence['development_episode_ids'] and not evidence['training_episode_ids']


def test_schema_unit_corruption_and_outside_managed_paths_are_rejected(tmp_path):
    job,conversion,path=converted_fixture(tmp_path)
    manifest_path=Path(conversion['dataset_manifest']);manifest=json.loads(manifest_path.read_text())
    manifest['action_units'][0]='normalized_-100_100';manifest_path.write_text(json.dumps(manifest))
    evidence=inspect_mobile_import(job,managed_root=tmp_path)
    assert not evidence['action9_verified'] and not evidence['admitted_for_training']
    outside=inspect_mobile_import(job,managed_root=tmp_path/'different')
    assert not outside['admitted_for_training'] and any('managed state root' in reason for reason in outside['reasons'])


def test_observation_only_summary_does_not_fabricate_qualified_physical_models():
    jobs=[{'id':str(index),'kind':'mobile_import','status':'completed','config':{'episode_id':str(index)},
           'result':{'frames':frames,'observation_only':True,'training_admission':{'source_quality_usable':False,'operator_success_label':False},
                     'qualification':{'rejection_counts':{'unknown_or_unqualified_arm_history:UNKNOWN_AFTER_REBOOT':frames}}}}
          for index,frames in enumerate([9,9,18])]
    summary=mobile_evidence_summary(jobs)
    assert summary['counts']=={'imported':3,'raw_frames_recorded':36,'admitted_for_training':0,
        'observation_or_rejected':3,'operator_labelled_success':0,'trained_RGB9D_models':0}
    assert not summary['physical_success_validated']
    assert all(not item['admitted_for_training'] for item in summary['imports'])


def embodied_fixture(tmp_path):
    # Reuse the authoritative contract's complete *software* source fixture.
    # It contains no invented prior-command9 and no legacy NPZ dependency.
    try:
        import explorer_shared.embodied_episode as contract
    except ImportError:
        pytest.skip('authoritative embodied module not integrated in this worktree yet')
    source=Path(contract.__file__).resolve().parents[2]/'tests/test_embodied_episode.py'
    spec=importlib.util.spec_from_file_location('authoritative_embodied_evidence_fixtures',source)
    fixture=importlib.util.module_from_spec(spec);spec.loader.exec_module(fixture)
    envelope,payloads=fixture.package()
    folder=tmp_path/'native';folder.mkdir()
    for name,raw in payloads.items():(folder/name).write_bytes(raw)
    path=folder/'episode.json';path.write_text(json.dumps(envelope))
    job={'id':'canonical-software-fixture','kind':'mobile_import','status':'completed','config':{'episode_id':envelope['episode_id']},
         'result':{'episode_id':envelope['episode_id'],'embodied_manifest':str(path)}}
    return job,path,envelope,payloads


def test_canonical_whole_9d_admission_does_not_require_legacy_state9_or_npz(tmp_path):
    job,path,envelope,payloads=embodied_fixture(tmp_path)
    original={str(item):item.read_bytes() for item in tmp_path.rglob('*') if item.is_file()}
    evidence=inspect_mobile_import(job,managed_root=tmp_path,include_manifest=True)
    assert evidence['admitted_for_training'],evidence['reasons']
    assert not evidence['legacy_RGB9D_contract_admitted']
    assert evidence['legacy_RGB9D_view']['reasons'] and evidence['reasons']==[]
    assert evidence['completed_recording'] and evidence['operator_success'] and evidence['source_quality_usable']
    assert evidence['original_session_id']=='session-1' and evidence['scene_id']=='scene-1'
    assert evidence['mobile_embodied']['raw_simultaneous_actions'][0]['values']==[0.]*9
    assert evidence['mobile_embodied']['raw_simultaneous_actions'][0]['mask']==[True]*9
    assert 'preceding_command9' not in envelope['samples'][0]['channels']
    assert not evidence['visual_ACT']['trained_RGB_model_verified'] and not evidence['physical_success_validated']
    assert {Path(name).name for name in evidence['embodied_source_files']}==set(payloads)
    assert original=={str(item):item.read_bytes() for item in tmp_path.rglob('*') if item.is_file()}


def test_canonical_source_binding_rejects_payload_corruption_and_wrong_original_id(tmp_path):
    job,path,envelope,payloads=embodied_fixture(tmp_path)
    job['config']['episode_id']='different-native-recording'
    evidence=inspect_mobile_import(job,managed_root=tmp_path)
    assert not evidence['admitted_for_training'] and any('different original episode' in reason for reason in evidence['reasons'])
    job['config']['episode_id']=envelope['episode_id']
    (path.parent/'rgb.bin').write_bytes(b'tampered')
    evidence=inspect_mobile_import(job,managed_root=tmp_path)
    assert not evidence['admitted_for_training'] and any('payload SHA mismatch' in reason for reason in evidence['reasons'])


def test_valid_canonical_native_source_is_independent_of_broken_optional_legacy_view(tmp_path):
    job,path,envelope,payloads=embodied_fixture(tmp_path)
    broken=tmp_path/'optional-legacy.json';broken.write_text('not-json')
    job['result']['dataset_manifest']=str(broken)
    evidence=inspect_mobile_import(job,managed_root=tmp_path)
    assert evidence['admitted_for_training'],evidence['reasons']
    assert not evidence['legacy_RGB9D_contract_admitted']
    assert any('evidence_inspection_failed' in reason for reason in evidence['legacy_RGB9D_view']['reasons'])


def test_raw_JPEG_integrity_and_camera_frame_binding_failures_are_separate_facts(tmp_path):
    job,conversion,path=converted_fixture(tmp_path,unknown=True,count=9,outcome='unknown')
    rows=[json.loads(line) for line in (path.parent/'samples.jsonl').read_text().splitlines()]
    camera=copy.deepcopy(rows[3]['camera_metadata_raw']);camera['jpeg_sha256']='0'*64
    FIX.change_raw(path.parent,rows[3],'camera-frame.json',camera);FIX.write_rows(path.parent,rows)
    evidence=inspect_mobile_import(job,managed_root=tmp_path)
    assert evidence['raw_rgb_verified_count']==9
    assert evidence['raw_integrity_errors']==['RGB_camera_SHA_binding_mismatch:row_3']
    assert not evidence['admitted_for_training'] and not evidence['rgb_verified']
