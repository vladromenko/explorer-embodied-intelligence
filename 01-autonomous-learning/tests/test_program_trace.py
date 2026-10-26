"""File-only phase export checks; synthetic fixtures are not robot/model execution evidence."""
import hashlib
import json
from pathlib import Path
import sys

import numpy as np
import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from explorer_sim.program_trace import EXECUTOR_SHA,export_program_trace,export_skill_cycle_trace,sha


def fixture(tmp_path, *, producer=EXECUTOR_SHA, unknown_outcome=False, bad_rows=False, jump=False):
    root=tmp_path/'source';folder=root/'attempts/one/composition';folder.mkdir(parents=True)
    artifacts={};stages=[]
    for stage in range(2):
        state=np.zeros((2,9),np.float32)+stage*2
        state[1]=state[0]+1
        sent=state+1;before=np.array([0,.08])+stage*.16+(1 if jump and stage else 0)
        path=folder/f'episode_000_stage{stage}.npz'
        np.savez(path,rgb=np.full((2,4,4,3),stage,np.uint8),state_command9=state[:-1] if bad_rows else state,
            requested_action_normalized=np.zeros((2,9),np.float32),dispatched_command9=sent,
            simulator_time_s=before,next_simulator_time_s=before+.08)
        digest=sha(path);artifacts[str(path.relative_to(root))]=digest
        stages.append({'trace_file':path.name,'trace_sha256':digest,'actual_inference_steps':2,'success':None if unknown_outcome else False})
    composition={'episodes':[{'stages':stages,'success':None if unknown_outcome else False,'goal_seeds':[1,2],
        'transition':{'simulator_time_s':.16,'physics_reset':False,'integration_state_unchanged':True}}]}
    (folder/'summary.json').write_text(json.dumps(composition));artifacts[str((folder/'summary.json').relative_to(root))]=sha(folder/'summary.json')
    cards=[{'id':'source-before','task':'fixed_base_visible_target_reach','status':'simulation_source_evaluated',
        'action_contract':{'physical_ready':False},'lineage':{'checkpoint_sha256':{'model.safetensors':'a'*64}}},
        {'id':'new-pending','task':'fixed_base_visible_target_reach','status':'simulation_candidate_pending',
         'action_contract':{'physical_ready':False},'lineage':{'checkpoint_sha256':{'model.safetensors':'b'*64}}}]
    result={'task':'executed_simulation_skill_cycle','status':'completed_engineering_development_cycle','physical_io':False,
        'context':{'skill_cycle_source_sha256':producer},'composition':composition,'artifact_sha256':artifacts,'skill_cards':cards}
    path=root/'result.json';path.write_text(json.dumps(result));return path


def test_exact_row_alignment_program_bindings_failures_and_no_actor_inputs(tmp_path):
    source=fixture(tmp_path);before={str(p):sha(p) for p in source.parent.rglob('*') if p.is_file()}
    out=tmp_path/'trace';receipt=export_program_trace(source,out)
    rows=[json.loads(line) for line in (out/'trace.jsonl').read_text().splitlines()]
    assert receipt['frames']==receipt['labelled_frames']==4
    assert [r['program_line'] for r in rows]==[1,1,2,2]
    assert [r['global_frame_index'] for r in rows]==[0,1,2,3]
    assert rows[0]['requested_action_normalized']!=rows[0]['dispatched_command9']
    assert rows[0]['rgb_sha256']==hashlib.sha256(bytes(4*4*3)).hexdigest()
    assert all(not r['allowed_as_actor_input'] and 'goal_xyz' not in r and 'success' not in r for r in rows)
    program=json.loads((out/'program.json').read_text())
    assert [s['library_binding']['skill_id'] for s in program['statements']]==['source-before','new-pending']
    event,=json.loads((out/'events.json').read_text())
    assert event['global_frame_index']==2 and event['source']=='evaluator_assisted_sim_executor_event'
    assert event['previous_stage_success'] is False and event['termination_reason'] is None
    assert receipt['source_outcomes']==[False] and not receipt['AW0_executed'] and not receipt['weights_loaded']
    order=json.loads((out/'episode_order.json').read_text())
    assert len(order['episodes'])==1 and len(order['stage_order'])==2
    assert [stage['first_frame'] for stage in order['stage_order']]==[0,2]
    assert {str(p):sha(p) for p in source.parent.rglob('*') if p.is_file()}==before
    assert export_program_trace(source,out)==receipt
    (out/'trace.jsonl').write_text('corrupt')
    with pytest.raises(ValueError,match='checksum'):
        export_program_trace(source,out)


@pytest.mark.parametrize('producer,missing_card', [('unknown-executor',False),(EXECUTOR_SHA,True)])
def test_unknown_version_or_binding_stays_unlabelled(tmp_path,producer,missing_card):
    source=fixture(tmp_path,producer=producer,unknown_outcome=True)
    if missing_card:
        value=json.loads(source.read_text());value['skill_cards']=[];source.write_text(json.dumps(value))
    out=tmp_path/'trace';receipt=export_program_trace(source,out)
    rows=[json.loads(line) for line in (out/'trace.jsonl').read_text().splitlines()]
    assert receipt['labelled_frames']==0 and receipt['frames']==4 and receipt['source_outcomes']==[None]
    assert all(r['program_line'] is None and r['phase'] is None and not r['label_valid'] for r in rows)


@pytest.mark.parametrize('options,reason',[({'bad_rows':True},'alignment'),({'jump':True},'continuous')])
def test_misaligned_rows_or_noncontinuous_stages_refused(tmp_path,options,reason):
    with pytest.raises(ValueError,match=reason):
        export_program_trace(fixture(tmp_path,**options),tmp_path/'trace')


def test_source_hash_and_source_target_isolation(tmp_path):
    source=fixture(tmp_path)
    with pytest.raises(ValueError,match='outside'):
        export_program_trace(source,source.parent/'sidecar')
    trace=next(source.parent.rglob('*.npz'));trace.write_bytes(b'changed')
    with pytest.raises(ValueError,match='checksum'):
        export_program_trace(source,tmp_path/'trace')


def test_job_wrapper_requires_complete_skill_cycle(tmp_path):
    class Store:
        root=tmp_path
        def job(self,identity):return {'kind':'skill_cycle','status':'running'}
    with pytest.raises(ValueError,match='completed'):
        export_skill_cycle_trace(Store(),'source')
