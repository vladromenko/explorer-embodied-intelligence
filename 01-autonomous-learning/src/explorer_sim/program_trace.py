"""ENGINEERING_GLUE, AW0_PROGRAM_TRACE_EXPORT: offline executor-derived phase labels.

Inspired by AW-0 CODE_ANNOTATION Route B at7ad228bebb897bf01705cc10171e1d88cf5e9b22.
No AW/neural code, weights, inference, visual annotation or robot interfaces.
"""
from __future__ import annotations
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import tempfile
import numpy as np

EXECUTOR_SHA = 'b0776fa90b734d703df0841eb7c0919986bf88da301f28e2fca67aee49d6dd64'
UPSTREAM = dict(repo='https://github.com/ZhuoyangLiu2005/agentic-wam', commit='7ad228bebb897bf01705cc10171e1d88cf5e9b22',
    document='docs/CODE_ANNOTATION.md', document_sha256='34c44a5f90cc3d829b4dfbb812cab2074746f78d962c228636f2fd842b988424', route='B_task_program')


def encoded(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode()


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def write(path, value):
    with path.open('xb') as stream:
        stream.write(encoded(value)+b'\n');stream.flush();os.fsync(stream.fileno())


def _source_file(root, relative, expected):
    path=(root/relative).resolve()
    if not path.is_relative_to(root) or not path.is_file() or sha(path)!=expected:
        raise ValueError('Executor source path/checksum mismatch')
    return path


def export_program_trace(source_result, target_dir):
    source=Path(source_result).expanduser().resolve(strict=True);target=Path(target_dir).expanduser().resolve()
    if target==source.parent or target in source.parents or source.parent in target.parents:
        raise ValueError('Trace output must be outside the immutable source job')
    source_bytes=source.read_bytes();source_hash=hashlib.sha256(source_bytes).hexdigest();result=json.loads(source_bytes)
    if result.get('task')!='executed_simulation_skill_cycle' or result.get('status')!='completed_engineering_development_cycle' or result.get('physical_io') is not False:
        raise ValueError('A complete actual simulation skill-cycle result is required')
    checked={str(source):source_hash};composition=result['composition'];producer_known=result.get('context',{}).get('skill_cycle_source_sha256')==EXECUTOR_SHA
    summaries=[name for name in result['artifact_sha256'] if Path(name).name=='summary.json' and Path(name).parent.name=='composition']
    if len(summaries)!=1:
        raise ValueError('One checksum-bound composition summary is required')
    summary=_source_file(source.parent,summaries[0],result['artifact_sha256'][summaries[0]])
    summary_bytes=summary.read_bytes()
    if hashlib.sha256(summary_bytes).hexdigest()!=result['artifact_sha256'][summaries[0]] or json.loads(summary_bytes)!=composition:
        raise ValueError('Composition summary and final executor evidence differ')
    checked[str(summary)]=sha(summary)
    cards=result.get('skill_cards',[]);roles=[]
    for role in ('before','pending'):
        matches=[card for card in cards if (card.get('id','').endswith('-before') if role=='before' else card.get('status')=='simulation_candidate_pending')]
        card=matches[0] if producer_known and len(matches)==1 else None
        lineage=card.get('lineage',{}) if card else {};hashes=lineage.get('checkpoint_sha256')
        valid=bool(card and card.get('task')=='fixed_base_visible_target_reach' and card.get('action_contract',{}).get('physical_ready') is False
            and isinstance(hashes,dict) and hashes and all(isinstance(d,str) and len(d)==64 and set(d)<=set('0123456789abcdef') for d in hashes.values()))
        card=card if valid else None
        roles.append(dict(skill_id=card['id'],checkpoint_sha256=card['lineage']['checkpoint_sha256'],
            weights_sha256=card['lineage'].get('weights_sha256')) if card else None)
    known=producer_known and all(binding is not None for binding in roles)
    program=dict(schema='explorer_declarative_trace_program_v1',statements=[dict(line=index+1,primitive='reach_visible_target',
        target_slot='visible_target_stage'+str(index),library_binding=roles[index]) for index in range(2)],
        library_version_sha256=hashlib.sha256(encoded(roles)).hexdigest(),executor_source_sha256=result.get('context',{}).get('skill_cycle_source_sha256'))
    program_id=hashlib.sha256(encoded(program)).hexdigest();rows=[];events=[];episodes=[];stage_order=[]
    for episode_index,episode in enumerate(composition['episodes']):
        if len(episode['stages'])!=2:
            raise ValueError('This declared program requires two actually saved stages')
        previous=None;begin=len(rows)
        for stage_index,stage in enumerate(episode['stages']):
            name=stage['trace_file']
            if Path(name).name!=name:
                raise ValueError('Stage trace filename is not a basename')
            matches=[key for key,value in result['artifact_sha256'].items() if Path(key).name==name and value==stage['trace_sha256']]
            if len(matches)!=1:
                raise ValueError('Stage trace is missing/ambiguous in immutable executor artifacts')
            path=_source_file(source.parent,matches[0],stage['trace_sha256']);checked[str(path)]=stage['trace_sha256']
            raw=path.read_bytes()
            if hashlib.sha256(raw).hexdigest()!=stage['trace_sha256']:
                raise ValueError('Stage bytes changed before decoding')
            with np.load(io.BytesIO(raw),allow_pickle=False) as saved:
                rgb=saved['rgb'];state=saved['state_command9'];requested=saved['requested_action_normalized'];sent=saved['dispatched_command9']
                before=saved['simulator_time_s'];after=saved['next_simulator_time_s'];n=len(rgb)
                if not n or rgb.dtype!=np.uint8 or rgb.ndim!=4 or rgb.shape[-1]!=3 or any(a.shape!=(n,9) for a in (state,requested,sent)) or any(a.shape!=(n,) for a in (before,after)) or any(not np.isfinite(a).all() for a in (state,requested,sent,before,after)):
                    raise ValueError('Actual RGB/state/request/dispatch/time row alignment differs')
                if np.any(after<=before) or not np.array_equal(before[1:],after[:-1]) or not np.array_equal(state[1:],sent[:-1]) or (stage.get('actual_inference_steps') is not None and stage['actual_inference_steps']!=n):
                    raise ValueError('Actual executor trace causality/counter differs')
                if previous is not None and (before[0]!=previous[0] or not np.array_equal(state[0],previous[1])):
                    raise ValueError('Two saved stages are not command/time continuous')
                if stage_index==1:
                    events.append(dict(episode_index=episode_index,global_frame_index=len(rows),event='target_changed_at_stage_boundary',
                        source='evaluator_assisted_sim_executor_event' if known else 'unverified_executor_transition_record',
                        evidence=episode.get('transition'),previous_stage_success=episode['stages'][0].get('success'),
                        termination_reason=None,allowed_as_actor_input=False))
                for index in range(n):
                    rows.append(dict(global_frame_index=len(rows),episode_index=episode_index,stage_index=stage_index,source_row_index=index,
                        source_trace=matches[0],source_trace_sha256=stage['trace_sha256'],rgb_sha256=hashlib.sha256(rgb[index].tobytes()).hexdigest(),
                        simulator_time_s=float(before[index]),next_simulator_time_s=float(after[index]),
                        state_command9=state[index].tolist(),requested_action_normalized=requested[index].tolist(),dispatched_command9=sent[index].tolist(),
                        program_id=program_id,program_line=stage_index+1 if known else None,phase='reach_'+str(stage_index) if known else None,
                        label_valid=known,label_source='saved_executor_stage_trace',allowed_as_actor_input=False))
                previous=(after[-1],sent[-1].copy())
            stage_order.append(dict(episode_index=episode_index,stage_index=stage_index,first_frame=len(rows)-n,frames=n,
                stage_success=stage.get('success'),declared_inference_steps=stage.get('actual_inference_steps')))
        episodes.append(dict(episode_index=episode_index,first_frame=begin,frames=len(rows)-begin,episode_success=episode.get('success'),goal_seeds=episode.get('goal_seeds')))
    context=dict(source_result_sha256=source_hash,source_files_sha256=checked,exporter_source_sha256=sha(Path(__file__)))
    if target.exists():
        receipt=json.loads((target/'receipt.json').read_text())
        if receipt['context']!=context:
            raise ValueError('Existing trace belongs to different source/exporter')
        for name,digest in receipt['output_sha256'].items():
            _source_file(target,name,digest)
        return receipt
    target.parent.mkdir(parents=True,exist_ok=True)
    staging=Path(tempfile.mkdtemp(prefix='.'+target.name+'-',dir=target.parent))
    try:
        write(staging/'program.json',program)
        with (staging/'trace.jsonl').open('xb') as output:
            for row in rows:
                output.write(encoded(row)+b'\n')
            output.flush();os.fsync(output.fileno())
        write(staging/'events.json',events);write(staging/'episode_order.json',dict(episodes=episodes,stage_order=stage_order,
            frame_order='source_episode_list_then_stage_list_then_native_row'))
        for name,digest in checked.items():
            if sha(name)!=digest:
                raise ValueError('Executor originals changed during trace export')
        receipt=dict(schema='explorer_program_trace_export_v1',context=context,program_id=program_id,frames=len(rows),
            labelled_frames=sum(row['label_valid'] for row in rows),source_episode_count=len(composition['episodes']),
            source_outcomes=[episode.get('success') for episode in composition['episodes']],upstream=UPSTREAM,
            label_role='offline_targets_and_analysis_only',source_actor_inputs_unchanged=True,privileged_transition_events_not_actor_inputs=True,
            clock_domain='source_mujoco_simulation_time_per_episode_not_global_monotonic',state_basis='source_command_estimate',
            dispatched_action_basis='simulator_command_target_not_measured_joint_position',
            source_program_predeclared=False,program_declaration_basis='version_bound_offline_description_of_existing_executor',
            library_checkpoint_hash_basis='inherited_complete_skill_cycle_record_not_reloaded_by_exporter',
            unknown_executor_mapping=not known,executor_version_recognized=producer_known,visual_annotation_performed=False,AW0_executed=False,neural_memory_enabled=False,
            physical_io=False,weights_loaded=False,source_originals_unchanged=True,
            output_sha256={path.name:sha(path) for path in staging.iterdir() if path.is_file()})
        write(staging/'receipt.json',receipt)
        for path in staging.iterdir():
            path.chmod(0o600)
        staging.rename(target)
        return receipt
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def export_skill_cycle_trace(store, source_job_id):
    if not isinstance(source_job_id,str) or Path(source_job_id).name!=source_job_id or source_job_id in ('.','..'):
        raise ValueError('Invalid source job identity')
    job=store.job(source_job_id)
    if not job or job.get('kind')!='skill_cycle' or job.get('status')!='completed':
        raise ValueError('Source job must be a completed skill_cycle')
    return export_program_trace(Path(store.root)/'jobs'/source_job_id/'result.json',
                                Path(store.root)/'artifacts/program_traces'/source_job_id)
