"""ENGINEERING_GLUE: read-only dependency graph for registered recorded studies.

No environment, model, device, database or archived Python program is executed.
The sole executed evaluator is a server-allowlisted pure benchmark source file.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
from types import SimpleNamespace
import json
import math
from pathlib import Path, PurePosixPath
import stat
import xml.etree.ElementTree as ET

TRUSTED_BENCHMARKS = frozenset({'352c62c3d58207dadb368f9250b5b14195addf12046d533077b9451f1d715f1a'})
MAX_FILES = 20000
MAX_BYTES = 1024**3
MAX_FILE_BYTES = 64*1024**2


class StudyRejected(ValueError):
    pass


def canonical(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode()


def sha_file(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream,'sha256').hexdigest()


def relative_name(value):
    if not isinstance(value,str) or not value or '\\' in value:
        raise StudyRejected('Nonempty portable POSIX member required')
    path=PurePosixPath(value)
    if path.is_absolute() or any(part in ('','.','..') for part in path.parts) or str(path)!=value:
        raise StudyRejected('Study member escapes its declared scope')
    return value


def regular(path,root=None):
    path=Path(path)
    if path.is_symlink() or not stat.S_ISREG(path.stat().st_mode):
        raise StudyRejected('Study input must be a regular original file')
    if root is not None and not path.resolve().is_relative_to(Path(root).resolve()):
        raise StudyRejected('Study input escaped its server-owned root')
    if path.stat().st_size>MAX_FILE_BYTES:
        raise StudyRejected('Study input exceeds per-file byte bound')
    return path


def read_json(path):
    regular(path)
    def invalid(value):raise StudyRejected('Nonfinite JSON is not recorded evidence')
    return json.loads(Path(path).read_bytes(),parse_constant=invalid)


@dataclass(frozen=True)
class ContextFile:
    path: Path
    sha256: str
    role: str


@dataclass(frozen=True)
class StudyDefinition:
    study_id: str
    campaign_root: Path
    protocol_sha256: str
    audit: ContextFile
    benchmark: ContextFile
    context: dict[str,ContextFile] = field(default_factory=dict)


@dataclass
class StudyInspection:
    document: dict
    sources: dict[str,Path]
    definition: StudyDefinition


def trusted_benchmark(context):
    path=regular(context.path);source=path.read_bytes()
    if context.sha256 not in TRUSTED_BENCHMARKS or hashlib.sha256(source).hexdigest()!=context.sha256:
        raise StudyRejected('Only the registered exact pure benchmark implementation may run')
    namespace={'__name__':'_explorer_frozen_benchmark_'+context.sha256,'__file__':str(path)}
    # Compile the verified bytes themselves. No loader cache writes and no
    # second path read between verification and execution are permitted.
    exec(compile(source,str(path),'exec'),namespace)
    return SimpleNamespace(**namespace)


def inspect_study(definition):
    """Account for every recorded attempt and original byte, without physics.

    COMPLETE means the requested original-runtime replay dependencies are closed,
    not merely that the included files have hashes. The current V9 source archive
    explicitly records a limited runtime inventory and must remain PARTIAL.
    """
    root=Path(definition.campaign_root).resolve()
    if not root.is_dir():raise StudyRejected('Registered campaign root is unavailable')
    sources={};files={};gaps=[];edges=[];attempts=[];total_bytes=0
    def gap(code,scope,detail,*,required=True):
        gaps.append(dict(code=code,scope=scope,detail=detail,required_for_original_reexecution=required))
    def add(path,name,role,expected=None):
        nonlocal total_bytes
        name=relative_name(name);path=regular(path,root if name.startswith('campaign/') else None)
        checksum=sha_file(path)
        if expected is not None and checksum!=expected:raise StudyRejected('Frozen study SHA changed: '+name)
        entry=dict(logical_path=name,sha256=checksum,bytes=path.stat().st_size,role=role)
        if name in files and files[name]!=entry:raise StudyRejected('Conflicting study member identity')
        if name not in files:total_bytes+=entry['bytes']
        files[name]=entry;sources[name]=path
        if len(files)>MAX_FILES or total_bytes>MAX_BYTES:raise StudyRejected('Bounded study inventory exceeded')
        return checksum
    # Every file below the single server-selected campaign root is retained,
    # including started/error/amendment and negative artifacts.
    for path in sorted(root.rglob('*')):
        if path.is_symlink():raise StudyRejected('Symlinks cannot change recorded study scope')
        if path.is_file():add(path,'campaign/'+path.relative_to(root).as_posix(),'recorded_original')
    for name,claim in dict(definition.context,**{'public-audit.json':definition.audit,'frozen-benchmark.py':definition.benchmark}).items():
        add(claim.path,'context/'+relative_name(name),claim.role,claim.sha256)
    audit=read_json(definition.audit.path)
    protocol=read_json(root/'frozen-protocol.json');benchmark=trusted_benchmark(definition.benchmark)
    benchmark.verify_protocol(protocol)
    if protocol['sha256']!=definition.protocol_sha256 or audit['frozen_protocol_sha256']!=definition.protocol_sha256:
        raise StudyRejected('Registered study protocol identity changed')
    if protocol.get('physical_io') is not False or protocol.get('learned_policy_claimed') is not False:
        raise StudyRejected('This registered study is SCRIPTED_SIM only')
    if definition.benchmark.sha256!=protocol['benchmark_implementation_sha256']:
        raise StudyRejected('Benchmark differs from the frozen protocol')
    if sha_file(root/'frozen-protocol.json')!=audit['frozen_protocol_file_sha256']:
        raise StudyRejected('Original frozen protocol bytes changed')
    if sha_file(root/'trials.jsonl')!=audit['trials_sha256'] or sha_file(root/'benchmark-report.json')!=audit['benchmark_report_sha256']:
        raise StudyRejected('Frozen trial table or report changed')
    if 'campaign_receipt_sha256' in audit and sha_file(root/'campaign-qualification.json')!=audit['campaign_receipt_sha256']:
        raise StudyRejected('Original campaign qualification receipt changed')
    campaign_driver=definition.context.get('campaign-driver.py')
    if campaign_driver is not None and protocol.get('driver_sha256')!=campaign_driver.sha256:
        raise StudyRejected('Frozen driver differs from its registered original bytes')
    derived=definition.context.get('derived-contact-recomputation.json')
    if derived is not None and audit.get('contact_recomputation_sha256')!=derived.sha256:
        raise StudyRejected('Derived contact analysis differs from the frozen context')
    audit_attempts={row['scenario']['id']:row for row in audit['attempts']}
    scenarios=protocol['scenarios'];ids=[row['id'] for row in scenarios]
    if not ids or len(ids)>100 or len(ids)!=len(set(ids)) or len(audit['attempts'])!=len(audit_attempts) or set(ids)!=set(audit_attempts):raise StudyRejected('All preregistered attempts must remain in the denominator')
    development=set(protocol['split_manifest']['development']['episode_ids'])
    if not set(ids).issubset(development):raise StudyRejected('Target study attempts must retain their development partition')
    rows=[json.loads(line) for line in (root/'trials.jsonl').read_text().splitlines() if line.strip()]
    by_trial={row['trial_id']:row for row in rows}
    if len(rows)!=len(by_trial) or set(by_trial)!=set(ids):raise StudyRejected('Trial table omitted or duplicated a started attempt')
    reconstructed=[]
    for scenario in scenarios:
        identity=scenario['id'];base=root/relative_name(identity);expected=audit_attempts[identity]
        required=['trial-started.json','trial.json','parameters.json','target-model.xml','source-trace.json',
            'source-verification.json','source-archive/manifest.json','target/result.json','target/target-trace.json']
        missing=[name for name in required if not (base/name).is_file()]
        if missing:
            gap('RECORDED_ATTEMPT_FILES_MISSING',identity,missing)
            attempts.append(dict(attempt_id=identity,outcome='UNKNOWN',missing=missing,target_partition='development',target_optimizer_allowed=False))
        else:
            started,trial,parameters,result,trace,archive=[read_json(base/name) for name in
                ('trial-started.json','trial.json','parameters.json','target/result.json','target/target-trace.json','source-archive/manifest.json')]
            if started.get('frozen_protocol_sha256')!=protocol['sha256'] or started.get('physical_io') is not False:
                raise StudyRejected('Started attempt is not bound to this frozen simulation protocol')
            if 'driver_sha256' in protocol and (started.get('driver_sha256')!=protocol['driver_sha256'] or trial.get('checkpoint_sha256')!=protocol['driver_sha256']):
                raise StudyRejected('Scripted implementation checkpoint differs from frozen driver')
            if started['scenario']!=scenario or parameters['scenario']!=scenario or trial.get('scenario')!=scenario:
                raise StudyRejected('Scenario/RNG seed changed across its original receipts')
            for name,key in [('target/result.json','result_sha256'),('target/target-trace.json','target_trace_sha256'),('source-archive/manifest.json','source_archive_manifest_sha256')]:
                if sha_file(base/name)!=expected[key]:raise StudyRejected('Original attempt bytes differ from frozen audit: '+identity+'/'+name)
            if result.get('source_method')!='SCRIPTED_SIM':raise StudyRejected('Original geometric source method changed')
            if result['source_kind']!='simulation' or result['physical_io'] is not False or result['learned_target_actor_executed'] is not False:
                raise StudyRejected('Recorded domain or actor method changed')
            if result['seed']!=scenario['seed'] or result['target_trace_sha256']!=sha_file(base/'target/target-trace.json') or result['frames']!=len(trace):
                raise StudyRejected('Result/trace/frame/RNG binding changed')
            context=archive.get('context',{})
            if context.get('scenario_id')!=identity or context.get('frozen_protocol_sha256')!=protocol['sha256'] or context.get('physical_io') is not False or context.get('source_model_learned') is not False:
                raise StudyRejected('Archived source context differs from the frozen SCRIPTED_SIM attempt')
            if result.get('source_model_full_skill_claim') is not False:
                raise StudyRejected('Geometric source cannot become a learned full skill')
            bindings={};archive_bytes=0
            for source in archive['files']:
                name=relative_name(source['file']);path=regular(base/'source-archive'/name,base/'source-archive')
                if sha_file(path)!=source['sha256'] or path.stat().st_size!=source['bytes']:
                    raise StudyRejected('Captured source/CAD/runtime member changed')
                bindings[source['original_locator']]=dict(source,logical_path='campaign/'+identity+'/source-archive/'+name)
                bindings[source['resolved_path']]=bindings[source['original_locator']];archive_bytes+=source['bytes']
            if archive_bytes!=archive['total_source_bytes'] or len(archive['files'])!=expected['source_archive_files']:
                raise StudyRejected('Original source archive accounting changed')
            verification=read_json(base/'source-verification.json')
            if verification.get('physical_io') is not False or verification.get('trial_result_sha256')!=expected['result_sha256'] or verification.get('after_trial',{}).get('manifest_sha256')!=expected['source_archive_manifest_sha256'] or verification.get('after_trial',{}).get('files')!=len(archive['files']) or verification.get('after_trial',{}).get('originals_rechecked') is not True:
                raise StudyRejected('Recorded post-trial source-verification receipt is unbound')
            source_trace=read_json(base/'source-trace.json')
            if result['source_manifest_sha256']!=sha_file(base/'source-trace.json'):
                raise StudyRejected('Geometric source trace binding changed')
            for locator,checksum in result['source_files_sha256'].items():
                if locator not in bindings or bindings[locator]['sha256']!=checksum:
                    gap('RESULT_SOURCE_REFERENCE_NOT_ARCHIVED',identity,locator)
            asset_bindings=[]
            scene=ET.fromstring((base/'target-model.xml').read_bytes())
            for element in scene.iter():
                locator=element.get('file')
                if locator is not None:
                    match=bindings.get(locator)
                    if match is None:gap('SCENE_ASSET_NOT_RESOLVED',identity,dict(tag=element.tag,file=locator))
                    else:asset_bindings.append(dict(tag=element.tag,original_locator=locator,logical_path=match['logical_path'],sha256=match['sha256']))
            if result['target_provider']['target_model_sha256']!=sha_file(base/'target-model.xml'):
                raise StudyRejected('Original scene XML differs from the recorded model')
            for locator,checksum in result['target_provider']['cad_mesh_sha256'].items():
                if locator not in bindings or bindings[locator]['sha256']!=checksum:
                    gap('TARGET_CAD_NOT_ARCHIVED',identity,locator)
            expected_stems={str(index).zfill(6) for index in range(len(trace))}
            for kind in ('rgb','requests','dispatch','observations'):
                stream=base/'target'/kind
                if {path.stem for path in stream.iterdir() if path.is_file()}!=expected_stems:
                    raise StudyRejected('Raw stream omitted or added an unaligned frame')
            seen=set();clock=None;last_stamp=None;native_state_length=None
            for index,row in enumerate(trace):
                number=str(index).zfill(6)
                observation,request,dispatch=[read_json(base/'target'/kind/(number+'.json')) for kind in ('observations','requests','dispatch')]
                image=regular(base/'target'/relative_name(row['target_rgb_file']),base/'target')
                if row['index']!=index or row['dispatch']!=dispatch or row['target_rgb_sha256']!=sha_file(image):
                    raise StudyRejected('Raw dispatch/RGB does not match recorded trace')
                if row['requested_action9']!=request['actual_requested_action9'] or observation['preceding_command9']!=row['preceding_command9']:
                    raise StudyRejected('Observation/request/dispatch alignment changed')
                if request['renderer_receipt']!=row['renderer_receipt'] or observation['renderer_receipt']!=row['renderer_receipt'] or observation['observation_stamp']!=row['renderer_receipt']['stamp'] or request['target_rgb_file']!=row['target_rgb_file'] or request['target_rgb_sha256']!=row['target_rgb_sha256']:
                    raise StudyRejected('Original camera/frame/observation binding changed')
                if dispatch['requested_action9']!=row['requested_action9'] or row['actual_dispatched_action9']!=dispatch['dispatched_action9'] or request['phase_id']!=row['phase_id']:
                    raise StudyRejected('Original action/phase request was replaced')
                if dispatch['physical_io'] is not False or dispatch['finite_dynamics_step_completed'] is not True or dispatch['attainment_inferred_from_commands'] is not False or row['learned_target_actor_executed'] is not False:
                    raise StudyRejected('Original finite SCRIPTED_SIM dispatch provenance changed')
                state=dispatch['native_integration_state']
                if not isinstance(state,list) or not state or any(type(v) not in (int,float) or not math.isfinite(v) for v in state):
                    raise StudyRejected('Recorded native integration-state bytes are incomplete')
                if native_state_length is not None and len(state)!=native_state_length:raise StudyRejected('Native integration-state shape changed within a trial')
                native_state_length=len(state)
                action=dispatch['dispatched_action9']
                if len(action)!=9 or any(type(v) not in (int,float) or not math.isfinite(v) for v in action):
                    raise StudyRejected('Full finite simultaneous9D dispatch is required')
                captured=row['renderer_receipt']['stamp'];before=dispatch['pre_action_stamp'];after=dispatch['dispatch_stamp']
                domain=(captured['clock_domain'],captured['boot_id'])
                if any((stamp['clock_domain'],stamp['boot_id'])!=domain for stamp in (before,after)) or not captured['monotonic_ns']<=before['monotonic_ns']<after['monotonic_ns']:
                    raise StudyRejected('Recorded frame/action causal clocks changed')
                if clock is not None and (clock!=domain or captured['monotonic_ns']<last_stamp):raise StudyRejected('Recorded attempt clock/boot or sequence changed')
                frame_id=row['renderer_receipt']['frame_id']
                if frame_id in seen:raise StudyRejected('A source frame was replayed as another independent target observation')
                seen.add(frame_id);clock=domain;last_stamp=after['monotonic_ns']
            report=result['independent_evaluation'];phases=protocol['target_phase_criteria']
            terminal=(report.get('terminal_goal_verified') is True and report.get('object_facts',{}).get('settled_in_goal') is True and report.get('object_facts',{}).get('withdrawn') is True)
            success=(not result.get('failure') and report.get('source')=='target_finite_physics_evaluator' and report.get('independent_of_action_commands') is True and report.get('end_to_end_success') is True and terminal and all((report.get('per_phase',{}).get(phase) or {}).get('success') is True for phase in phases))
            if result['full_target_simulation_success'] is not success or expected['full_task_success'] is not success:
                raise StudyRejected('Outcome is inconsistent with frozen independent terminal/phase evidence')
            extra=dict(frames=result['frames'],failure=result['failure'],target_result_sha256=sha_file(base/'target/result.json'),per_phase=report['per_phase'],terminal=report['object_facts'])
            rebuilt=dict(trial,raw_result=extra,outcome='SUCCESS' if success else 'FAILURE')
            if canonical(rebuilt)!=canonical(trial) or canonical(trial)!=canonical(by_trial[identity]):
                raise StudyRejected('Raw result does not reconstruct the original benchmark trial')
            reconstructed.append(rebuilt)
            attempts.append(dict(attempt_id=identity,outcome=rebuilt['outcome'],frames=len(trace),seed=scenario['seed'],
                full_rng_state_recorded=False,initial_native_state_recorded=False,post_step_native_states=len(trace),native_integration_state_length=native_state_length,
                artifacts={name:dict(logical_path='campaign/'+identity+'/'+name,sha256=sha_file(base/name)) for name in required},
                raw_streams={kind:dict(count=len(trace),logical_directory='campaign/'+identity+'/target/'+kind) for kind in ('rgb','requests','dispatch','observations')},
                scripted_checkpoint_basis=trial.get('checkpoint_basis'),scripted_checkpoint_sha256=trial['checkpoint_sha256'],
                scene_model_sha256=result['target_provider']['target_model_sha256'],scene_asset_bindings=asset_bindings,
                recorded_source_inventory_items=len(archive['files']),source_runtime_scope=archive['scope'],
                source_kind='SCRIPTED_SIM',learned_policy_executed=False,target_partition='development',target_optimizer_allowed=False,
                legacy_raw_admission={name:result.get(name) for name in ('source_split','optimizer_partition_allowed','eligible_as_simulation_expert','eligible_for_target_dataset')},
                legacy_raw_optimizer_flag_basis='source_partition_only_never_target_admission',source_action_dimension=6,target_action_dimension=9))
            edges.append(dict(from_id='protocol',to_id=identity,relation='preregistered_development_attempt'))
            gap('ORIGINAL_RUNTIME_INVENTORY_NOT_CLOSED',identity,dict(recorded_scope=archive['scope'],unclosed=['full_OS_and_system_libraries','NumPy_compiled_runtime','MuJoCo_Python_extension_and_remaining_transitive_dependencies'],portable_payload_integrity_verified=True))
            gap('FULL_INITIAL_RNG_AND_NATIVE_STATE_NOT_RECORDED',identity,'Seed, scenario and post-step integration states are retained; no full initial RNG/native-state receipt')
    for directory in sorted(root.iterdir()):
        if directory.is_dir() and (directory/'trial-started.json').is_file() and directory.name not in ids:
            gap('UNREGISTERED_STARTED_ATTEMPT',directory.name,'Retained in file graph but not silently inserted into the frozen denominator')
    recomputed=None
    if len(reconstructed)==len(ids):
        recomputed=benchmark.summarize_trials(protocol,reconstructed)
        if canonical(recomputed)!=(root/'benchmark-report.json').read_bytes():raise StudyRejected('Trusted raw→frozen benchmark recomputation differs')
    else:gap('FROZEN_BENCHMARK_RECOMPUTATION_INCOMPLETE','benchmark','Missing original attempt dependencies')
    gap('NO_LEARNED_OR_PHYSICAL_STUDY_CLAIM','scope','Geometric SCRIPTED_SIM, four development scenes, stationary base; H1/generalization/physical skill not established',required=False)
    document=dict(schema='explorer_recorded_study_inspection_v1',study_id=definition.study_id,
        frozen_protocol_sha256=protocol['sha256'],frozen_benchmark_sha256=definition.benchmark.sha256,
        completeness='PARTIAL' if any(row['required_for_original_reexecution'] for row in gaps) else 'COMPLETE',
        recorded_attempt_ids=ids,attempts=attempts,dependency_gaps=gaps,dependency_edges=edges,
        context_artifacts=[dict(logical_path='context/'+name,sha256=entry.sha256,role=entry.role,executed=False) for name,entry in sorted(definition.context.items())],
        executed_implementation=dict(logical_path='context/frozen-benchmark.py',sha256=definition.benchmark.sha256,scope='allowlisted_pure_statistical_evaluator_only'),
        file_manifest=[files[name] for name in sorted(files)],file_count=len(files),total_logical_bytes=sum(row['bytes'] for row in files.values()),
        benchmark_recomputation=dict(verified=recomputed is not None,report=recomputed,scope='Recorded independent result/phase/terminal evidence→original trial rows→allowlisted frozen statistical evaluator; no dynamics/contact recomputation'),
        dependency_closure=dict(recorded_required_payloads='PARTIAL' if any(row['code']=='RECORDED_ATTEMPT_FILES_MISSING' for row in gaps) else 'VERIFIED',
            frozen_benchmark='VERIFIED' if recomputed is not None else 'PARTIAL',
            scene_and_cad_assets='PARTIAL' if any(row['code'] in ('SCENE_ASSET_NOT_RESOLVED','TARGET_CAD_NOT_ARCHIVED') for row in gaps) else 'VERIFIED',
            original_runtime='PARTIAL',full_initial_rng_and_reset_state='NOT_RECORDED',original_dynamics_reexecution='UNQUALIFIED'),
        target_partition='development',target_optimizer_allowed=False,source_method='SCRIPTED_SIM',learned_policy_executed=False,
        physical_success_claimed=False,physical_io=False,master_prompt_completed=False)
    document['inspection_sha256']=hashlib.sha256(canonical(document)).hexdigest()
    return StudyInspection(document,sources,definition)
