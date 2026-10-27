"""Explicit new driver/component and bounded report lineage, no real State IO."""
import importlib
import importlib.util
from pathlib import Path
import uuid

import pytest

from explorer_shared.contracts import canonical
from explorer_shared.learning.sim_object_route import split_cases
from explorer_shared.learning.sim_temporal_route import CELLS,TemporalChunkDispatcher


def driver():
    spec=importlib.util.spec_from_file_location('numeric_admission_driver_tests',Path(__file__).resolve().parents[1]/'scripts/run_sim_numeric_admission.py');module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module


def test_new_driver_does_not_replace_any_component_in_frozen_6b25_module():
    module=driver();frozen=importlib.import_module('run_sim_temporal_learning')
    assert frozen.TemporalChunkDispatcher is TemporalChunkDispatcher
    assert module.rollout.__kwdefaults__['dispatcher_factory'] is module.NumericAdmissionDispatcher
    assert module.ORIGINAL_COMMIT=='6b25d010c5243409c7bb6aa498035d4894617ad4'


def fixture(tmp_path):
    module=driver();scenarios={}
    for partition,offset,seeds in [('train',0,[701]),('development',1,[711,712]),('sealed_test',3,[721,722])]:
        scenarios[partition]=[dict(id=partition+'-'+str(seed),seed=seed,object_offset_xy_m=[(offset+i)*.001,0.],goal_offset_xy_m=[0.,0.],object_yaw_rad=(offset+i)*.01) for i,seed in enumerate(seeds)]
    models={cell:{'checkpoint_sha256':str(i)*64} for i,cell in enumerate(CELLS)}
    plan=dict(scenarios=scenarios,original_models=models,skill_id='mock-skill',training_source_manifest_sha256='a'*64,original_C_v1_source_commit=module.ORIGINAL_COMMIT)
    (tmp_path/'evaluation-freeze.json').write_bytes(canonical({'EXPLICIT_MOCK':True}));(tmp_path/'protocol.json').write_bytes(canonical(plan))
    paths=[]
    for case in split_cases(plan):
        if case['partition']!='train':
            for cell in CELLS:
                folder=tmp_path/'rollout'/case['partition']/cell/case['id'];folder.mkdir(parents=True)
                row=dict(cell=cell,case=case,trial_id=str(uuid.uuid4()),checkpoint_sha256=models[cell]['checkpoint_sha256'],evaluation_freeze_sha256=module.sha(tmp_path/'evaluation-freeze.json'),
                    teacher_query_count=0,privileged_actor_input=False,chunk_execution_steps=8,frames=800,failure=None,outcome='FAILURE',terminal_score={'full_object_success':False},
                    raw_trace_sha256='b'*64,numerical_admissions=1,initial_state={'manifest_sha256':'c'*64})
                path=folder/'result.json';path.write_bytes(canonical(row));paths.append(path)
    return module,plan,paths


def test_reporting_preserves_all16_negative_results_with_bound_source_refs(tmp_path):
    module,plan,paths=fixture(tmp_path);folder=tmp_path/'summary';folder.mkdir()
    result=module.summary(plan,tmp_path,folder)
    assert result['total_trials']==16 and result['paired_scenario_count']==4 and result['original_v1_outcome']=={'UNKNOWN':1,'NOT_STARTED':15}
    assert all(cell['FAILURE']==2 for partition in result['counts'].values() for cell in partition.values())
    assert all(row['raw_result']['sha256']==module.sha(tmp_path/row['raw_result']['relative_path']) for row in result['trials'])
    assert len(list(folder.glob('skill-card-*.json')))==4


@pytest.mark.parametrize('change',['missing','different_model','duplicate_uuid'])
def test_partial_or_mixed_model_or_reused_uuid_cannot_claim_complete16(tmp_path,change):
    import json
    module,plan,paths=fixture(tmp_path);folder=tmp_path/'summary';folder.mkdir()
    if change=='missing':paths[-1].unlink()
    else:
        row=json.loads(paths[-1].read_bytes())
        if change=='different_model':row['checkpoint_sha256']='f'*64
        else:row['trial_id']=json.loads(paths[0].read_bytes())['trial_id']
        paths[-1].write_bytes(canonical(row))
    with pytest.raises(ValueError):module.summary(plan,tmp_path,folder)
    assert not (folder/'summary.json').exists() and not list(folder.glob('skill-card-*.json'))
