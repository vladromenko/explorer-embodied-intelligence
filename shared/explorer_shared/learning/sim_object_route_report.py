"""ENGINEERING_GLUE: compact registry projection of immutable SIM trial evidence.

No policy, training, evaluator or original artifact is changed. Raw trajectories
remain separately SHA-bound instead of duplicating megabytes inside a SkillCard.
"""
from copy import deepcopy
from pathlib import PurePosixPath

from explorer_shared.contracts import canonical,digest


def compact_skill_card(raw_card,*,raw_card_sha256,trial_artifacts):
    if digest(raw_card)!=raw_card_sha256 or raw_card.get('source_type')!='simulation':raise ValueError('Exact original SIM SkillCard SHA required')
    rows=raw_card['evaluation']['trials'];seen=set();trials=[]
    for row in rows:
        key=(row['case']['id'],row['strategy'])
        if key in seen or key not in trial_artifacts:raise ValueError('Every paired raw trial must be referenced exactly once')
        seen.add(key);artifact=trial_artifacts[key];path=PurePosixPath(artifact['relative_path'])
        if path.is_absolute() or '..' in path.parts or digest(row)!=artifact['sha256'] or type(artifact['bytes']) is not int or artifact['bytes']<=0:
            raise ValueError('Exact relative original raw trial/SHA/bytes required')
        trials.append(dict(case=deepcopy(row['case']),strategy=row['strategy'],outcome=row['outcome'],frames=row['frames'],
            failure=deepcopy(row['failure']),terminal_score=deepcopy(row['terminal_score']),
            checkpoint_sha256=row['checkpoint_sha256'],paired_policy_freeze_sha256=row['paired_policy_freeze_sha256'],
            raw_trace_sha256=row['raw_trace_sha256'],raw_result=deepcopy(artifact),
            initial_state_manifest_sha256=row['initial_state']['manifest_sha256'],teacher_query_count=row['teacher_query_count'],
            privileged_actor_input=row['privileged_actor_input'],learned_target_actor_executed=row['learned_target_actor_executed']))
    if len(rows)!=8 or seen!=set(trial_artifacts):raise ValueError('All eight preregistered paired trials must survive projection')
    result={key:deepcopy(value) for key,value in raw_card.items() if key!='evaluation'}
    result['evaluation']={key:deepcopy(value) for key,value in raw_card['evaluation'].items() if key!='trials'}
    result['evaluation']['trials']=trials
    result['projection_lineage']=dict(schema='explorer_SIM_skill_card_compact_projection_v1',original_card_sha256=raw_card_sha256,
        raw_scientific_artifacts_unchanged=True,raw_trial_count=len(rows),physical_execution_qualified=False)
    if len(canonical(result))>256*1024:raise ValueError('Compact registry metadata exceeds bound; raw payloads remain separate')
    return result
