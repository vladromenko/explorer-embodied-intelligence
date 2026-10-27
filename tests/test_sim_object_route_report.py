"""Reporting only: immutable negatives survive a bounded registry projection."""
from copy import deepcopy

import pytest

from explorer_shared.contracts import canonical,digest
from explorer_shared.learning.sim_object_route_report import compact_skill_card


def fixture():
    rows=[];artifacts={}
    for index in range(8):
        row=dict(case={'id':'mock-'+str(index)},strategy='chunk1' if index%2 else 'chunk8',outcome='FAILURE',frames=800,
            failure=None,terminal_score={'full_object_success':False},checkpoint_sha256='a'*64,paired_policy_freeze_sha256='b'*64,
            raw_trace_sha256='c'*64,initial_state={'manifest_sha256':'d'*64},teacher_query_count=0,privileged_actor_input=False,
            learned_target_actor_executed=True,independent_evaluation={'mock_large_raw_contact_trace':'x'*20000})
        rows.append(row);artifacts[(row['case']['id'],row['strategy'])]=dict(relative_path='rollout/'+str(index)+'/result.json',sha256=digest(row),bytes=len(canonical(row)))
    card=dict(id='mock-candidate',source_type='simulation',evaluation=dict(trials=rows,whole_skill_mastered=False,outcomes={'FAILURE':8}))
    return card,artifacts


def test_compact_card_keeps_all_negative_outcomes_and_exact_source_refs_without_mutation():
    card,artifacts=fixture();old=canonical(card);projected=compact_skill_card(card,raw_card_sha256=digest(card),trial_artifacts=artifacts)
    assert canonical(card)==old and len(canonical(projected))<16000<len(old)
    assert len(projected['evaluation']['trials'])==8 and all(r['outcome']=='FAILURE' for r in projected['evaluation']['trials'])
    assert all(r['raw_result']['sha256']==artifacts[(r['case']['id'],r['strategy'])]['sha256'] for r in projected['evaluation']['trials'])
    assert not projected['projection_lineage']['physical_execution_qualified']


@pytest.mark.parametrize('change',['missing','duplicate','hash','path'])
def test_projection_rejects_dropped_negative_duplicate_or_wrong_original_binding(change):
    card,artifacts=fixture()
    if change=='missing':artifacts.pop(next(iter(artifacts)))
    elif change=='duplicate':card['evaluation']['trials'][0]=deepcopy(card['evaluation']['trials'][1])
    elif change=='hash':next(iter(artifacts.values()))['sha256']='0'*64
    else:next(iter(artifacts.values()))['relative_path']='../../other/result.json'
    with pytest.raises(ValueError):compact_skill_card(card,raw_card_sha256=digest(card),trial_artifacts=artifacts)
