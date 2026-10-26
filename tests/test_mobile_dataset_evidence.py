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
    assert evidence['admitted_for_training'],evidence['reasons']
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
