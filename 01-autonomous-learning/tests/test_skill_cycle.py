"""Real store boundary/actual CAD stage-transition checks; no physical transports."""
import json
import os
from pathlib import Path
import sys

import numpy as np
import pytest

ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT/'01-autonomous-learning/src'),str(ROOT/'00-control-center/src'),str(ROOT/'shared')]
from explorer.store import Store
from explorer_sim.skill_cycle import (TASK,acceptance,contract,integration_state,load_policy,
                                      next_goal_without_reset,plan,put_card,retrieve,store_checkpoint)
from explorer_sim.pilot import ReachEnv,sha
from explorer_sim.student import preprocessing_config


def card(tmp_path,ident='one',score=.2):
    path=tmp_path/ident
    path.mkdir()
    model=path/'model.safetensors';model.write_bytes(b'opaque bytes; never loaded in these store tests')
    return dict(id=ident,task=TASK,status='simulation_source_evaluated',source_type='sim_dagger_act',
        action_contract=contract({'mesh':'abc'}),evaluation={'success_rate':score},
        lineage={'checkpoint_dir':str(path),'checkpoint_sha256':{'model.safetensors':sha(model)}},limitations=[])


def test_actual_sqlite_card_storage_retrieval_compatibility_ranking_and_hash(tmp_path):
    store=Store(tmp_path/'state')
    one=card(tmp_path,'one',.2);two=card(tmp_path,'two',.4)
    put_card(store,one);put_card(store,two)
    assert retrieve(store,one['action_contract'],{'one','two'})['id']=='two'
    assert len(store.skills())==2
    put_card(store,one)
    assert len(store.skills())==2
    with pytest.raises(ValueError,match='different body'):
        put_card(store,dict(one,status='physically_ready'))
    incompatible=dict(one['action_contract'],embodiment='another_robot')
    with pytest.raises(ValueError,match='compatible'):
        retrieve(store,incompatible,{'one','two'})
    (Path(two['lineage']['checkpoint_dir'])/'model.safetensors').write_bytes(b'corrupt')
    with pytest.raises(ValueError,match='changed'):
        retrieve(store,one['action_contract'],{'one','two'})


@pytest.mark.parametrize('wrong', [{'state_basis':'measured_state'},{'mean':[0,0,0]},{'action_transform':'different units'}])
def test_preprocessing_mismatch_refused_before_model_deserialization(tmp_path,wrong):
    value=card(tmp_path)
    path=Path(value['lineage']['checkpoint_dir'])/'explorer_preprocessing.json'
    path.write_text(json.dumps(dict(preprocessing_config(),**wrong)))
    value['lineage']['checkpoint_sha256']['explorer_preprocessing.json']=sha(path)
    with pytest.raises(ValueError,match='preprocessing'):
        load_policy(value)


def test_actual_checkpoint_bytes_stored_independently_from_source(tmp_path):
    source=tmp_path/'original';source.mkdir()
    (source/'model.safetensors').write_bytes(b'byte-copy fixture only')
    hashes={'model.safetensors':sha(source/'model.safetensors')}
    store=Store(tmp_path/'state')
    saved=store_checkpoint(store,source,hashes)
    assert saved.is_relative_to(store.root/'artifacts/skill_library')
    assert (saved/'model.safetensors').read_bytes()==(source/'model.safetensors').read_bytes()
    assert (saved/'model.safetensors').stat().st_ino!=(source/'model.safetensors').stat().st_ino
    assert store_checkpoint(store,source,hashes)==saved
    (saved/'model.safetensors').write_bytes(b'corrupt')
    with pytest.raises(ValueError,match='changed'):
        store_checkpoint(store,source,hashes)


def test_fresh_disjoint_predeclared_seed_roles_and_gate():
    source={'result':{'seed_plan':{'blocked_source_seeds':[41,2041],'collection':[54041],'development':[62041]},
                      'source_context':{'settings':{'seed':52041}}}}
    value=plan({},source)
    selected=value['seeds']['acceptance']+value['seeds']['retention']+[x for p in value['seeds']['composition'] for x in p]
    assert len(selected)==len(set(selected))
    assert not set(selected)&set(value['blocked_source_seeds'])
    assert value['adaptation_steps']==64 and value['gate']['declared_before_trials']
    assert value['gate']['min_candidate_successes']==4
    assert not value['sealed_final_test'] and not value['development_observations_fit']
    with pytest.raises(ValueError,match='overlap'):
        plan({'seed':52041},source)


def test_acceptance_rejects_poor_candidate_or_failed_composition_or_missing_retention():
    gate={'min_candidate_successes':4,'min_composition_successes':2,'max_retention_success_drop':0}
    n=lambda count:{'successes':count}
    assert not acceptance(gate,n(1),n(2),n(2),n(2),n(2))['accepted']
    assert not acceptance(gate,n(1),n(4),n(2),n(2),n(0))['accepted']
    assert not acceptance(gate,n(1),n(4),n(2),None,n(2))['accepted']
    assert acceptance(gate,n(1),n(4),n(2),n(2),n(2))['accepted']
    assert not acceptance(gate,n(1),n(4),n(2),n(1),n(2))['accepted']


def test_actual_mujoco_second_goal_preserves_entire_integration_state():
    assets=os.environ.get('EXPLORER_SIM_MESH_DIR')
    if not assets:
        pytest.skip('Real checksum-verified CAD assets required')
    env=ReachEnv(assets,horizon=8)
    try:
        env.reset(seed=102041)
        env.step(np.array([.3,-.2,.1],np.float32))
        before=integration_state(env);command=env.command.copy();stamp=env.data.time;goal=env.goal.copy()
        transition=next_goal_without_reset(env,112041)
        assert np.array_equal(before,integration_state(env))
        assert np.array_equal(command,env.command) and env.data.time==stamp
        assert not np.array_equal(goal,env.goal)
        assert not transition['physics_reset'] and transition['integration_state_unchanged']
        assert env.steps==env.hold==0
    finally:
        env.close()
