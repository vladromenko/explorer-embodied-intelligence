"""Thin06 original import and aggregate over shared native9 transfer/inspection.

No recorder, converter or trainer is duplicated. Source review and expert
outcome remain separate; sealed payloads stay outside the fitted aggregate.
"""
from copy import deepcopy
import json
from pathlib import Path

from explorer_shared.contracts import ACTION_NAMES,ACTION_UNITS,canonical,digest
from explorer_shared.immutable_dataset_bundle import write_immutable_bundle
from explorer_shared.learning.artifacts import sha
from explorer_shared.learning.rosmaster_skill_pipeline import CanonicalCorpus
from explorer_shared.native_capture_identity import normalize_capture_context,native_context_sha256
from explorer_shared.transfer_target_capture import validate_target_capture_context
from . import transfer_target as target
from .mobile_dataset_evidence import inspect_mobile_import

IMPORT_KIND='transfer_target_import'


def run_import(store,job,config):
    if (job.get('kind'),job.get('project_id'))!=(IMPORT_KIND,'06'):
        raise ValueError('Exact target06 native original import required')
    settings=job.get('config') or {};context=validate_target_capture_context(settings.get('source_capture_context'))
    wizard,_=target.source_for_wizard(store,settings.get('wizard_id'))
    if (wizard['domain']!='physical' or context['wizard_id']!=wizard['wizard_id'] or
            context['source_model_binding']!=wizard['source_model_binding'] or context['skill_version']!=wizard['target_version']):
        raise ValueError('Current source/wizard/native target context differs')
    from .job_runner import approved_native_capture_import
    result=approved_native_capture_import(store,job,config)
    original=json.loads(Path(result['embodied_manifest']).read_bytes())
    if original.get('skill_context')!=normalize_capture_context(context):
        raise ValueError('Imported original does not match the exact known target context')
    target.source_for_wizard(store,wizard['wizard_id'])
    return dict(result,wizard_id=wizard['wizard_id'],target_capture_context_sha256=native_context_sha256(normalize_capture_context(context)),
        target_original_identity_retained=True,physical_success_inferred=False)


def build_aggregate(store,wizard_id,import_job_ids):
    wizard,_=target.source_for_wizard(store,wizard_id)
    if (not isinstance(import_job_ids,list) or not 2<=len(import_job_ids)<=1000 or
            any(not isinstance(value,str) for value in import_job_ids) or len(set(import_job_ids))!=len(import_job_ids)):
        raise ValueError('Choose unique completed target original import IDs')
    files={};episodes=[];sealed=[];originals=set();spec=None
    for ident in import_job_ids:
        job=store.job(ident)
        if not job or (job['kind'],job['project_id'],job['status'])!=(IMPORT_KIND,'06','completed'):
            raise ValueError('Completed typed target06 originals required')
        if job.get('config',{}).get('wizard_id')!=wizard_id:raise ValueError('Target import belongs to another wizard')
        report=inspect_mobile_import(job,managed_root=store.root,include_manifest=True)
        inspected=report['mobile_embodied'];envelope=inspected.get('envelope') or {};approval=report.get('source_approval') or {}
        if (report.get('admitted_for_training') is not True or report.get('operator_success') is not True or
                approval.get('verified') is not True or envelope.get('episode_scope')!='whole_episode' or
                (inspected.get('input_adapter_provenance') or {}).get('verified') is not True):
            raise ValueError('Target original needs full source-bound expert outcome AND separate current pinned approval')
        path=Path(job['result']['embodied_manifest']).resolve();original=json.loads(path.read_bytes())
        context=validate_target_capture_context(original.get('skill_context'))
        if (context['wizard_id']!=wizard_id or context['source_model_binding']!=wizard['source_model_binding'] or
                context['skill_version']!=wizard['target_version']):raise ValueError('Original target source/wizard/version differs')
        if spec is None:spec=deepcopy(context['skill_spec'])
        if context['skill_spec']!=spec:raise ValueError('One immutable target SkillSpec required')
        original_id=envelope['episode_id']
        if original_id in originals:raise ValueError('Duplicate original cannot inflate target cohort')
        originals.add(original_id);partition=inspected['split']['partition']
        if partition=='sealed_test':
            sealed.append(dict(original_episode_id=original_id,split=partition,source_archive_sha256=job['result']['source_archive_sha256'],
                source_review_sha256=approval['review_sha256'],original_manifest_sha256=sha(path)))
        else:
            namespace='episodes/'+digest(dict(original=original_id,archive=job['result']['source_archive_sha256']))[:32]
            mapping={}
            artifacts=list(inspected['source_artifacts'])+[dict(relative_name='__envelope__/original-document.json',path=str(path),sha256=sha(path))]
            for artifact in artifacts:
                source=Path(artifact['path']).resolve()
                if not source.is_relative_to(store.root.resolve()):raise ValueError('Managed original artifact required')
                relative=namespace+'/embodied/'+artifact['relative_name'];files[relative]=(source,artifact['sha256']);mapping[artifact['relative_name']]=relative
            admission=namespace+'/canonical-admission.json';raw=canonical(inspected);files[admission]=(raw,digest(inspected))
            source_approval=namespace+'/source-approval.json';approval_path=Path(job['result']['source_approval_path']).resolve()
            if not approval_path.is_relative_to(store.root.resolve()):raise ValueError('Managed immutable review snapshot required')
            files[source_approval]=(approval_path,job['result']['source_approval_file_sha256'])
            episodes.append(dict(source_episode_id=original_id,split=partition,skill_id=spec['skill_id'],skill_version=spec['version'],
                variant_id=context['variant_id'],source_project_id='06',source_kind=inspected['source_kind'],
                teaching_session_id=context['session_id'],scene=inspected['split']['scene_id'],episode_scope='whole_episode',
                target_phase_ids=[row['id'] for row in spec['stages']],canonical_admission_file=admission,
                canonical_admission_sha256=files[admission][1],source_artifact_map=mapping,
                source_approval_required=True,source_format='native_capture_v1',source_approval_file=source_approval,
                source_review_sha256=approval['review_sha256'],source_archive_sha256=job['result']['source_archive_sha256']))
    core=dict(schema='explorer_unified_embodied_skill_dataset_v1',workflow_family='cross_embodiment_target_shared_ACT9_v1',
        skill_id=spec['skill_id'],skill_version=spec['version'],skill_spec=spec,skill_spec_sha256=digest(spec),
        action_contract_id=spec['action_contract_id'],action_names=list(ACTION_NAMES),action_units=list(ACTION_UNITS),simultaneous_9d=True,
        compatible_source_projects=['06'],source_model_binding=wizard['source_model_binding'],target_domain=wizard['domain'],
        episodes=episodes,sealed_sources_excluded=sealed,source_files_sha256={name:expected for name,(_,expected) in files.items()},
        physical_success_inferred=False,source_action_head_reused=False)
    from .native_capture_cohort import validate_current_approvals
    validate_current_approvals(store,core)
    content=digest(core);manifest=dict(core,aggregate_content_id=content)
    destination=store.root/'datasets'/'transfer-target'/wizard_id/content
    path=write_immutable_bundle(destination,canonical(manifest),files)
    corpus=CanonicalCorpus(path)  # Same RGB/full9D/phase/outcome/global split admission as02/03.
    target.source_for_wizard(store,wizard_id)
    return dict(dataset_manifest=str(path),dataset_manifest_sha256=corpus.sha256,aggregate_content_id=content,
        train_original_episode_ids=[row['descriptor']['source_episode_id'] for row in corpus.train],
        development_original_episode_ids=[row['descriptor']['source_episode_id'] for row in corpus.development],sealed_original_episode_ids=corpus.sealed_ids,
        physical_success_inferred=False)


def note_source_review(store,wizard_id,import_job_id,wrapper,*,actor):
    """Retain a trusted post-seal Source reply in the SAME review journal02/03 use.

    The caller fetches the authenticated fixed Source endpoint; browser JSON is
    not a Source receipt. Historical DATA may be rejected even if its model is
    now unavailable. This never rewrites raw outcomes or issues a native command.
    """
    import time
    from explorer_shared.contracts import event
    from .transfer_calibration import session
    from .native_capture_approval import validate_source_approval
    wizard=session(store,wizard_id);job=store.job(import_job_id)
    if not job or (job['kind'],job['project_id'],job['status'])!=(IMPORT_KIND,'06','completed'):
        raise ValueError('Known completed target original required for Source review')
    if job['config'].get('wizard_id')!=wizard_id:raise ValueError('Known target wizard/import binding required')
    path=Path(job['result']['embodied_manifest']).resolve()
    if not path.is_relative_to(store.root.resolve()):raise ValueError('Managed original required')
    original=json.loads(path.read_bytes());context=validate_target_capture_context(original.get('skill_context'))
    if context['wizard_id']!=wizard_id or context['source_model_binding']!=wizard['source_model_binding']:
        raise ValueError('Historical target original context differs')
    identity=original['recording_id'];archive=job['result']['source_archive_sha256']
    checked=validate_source_approval(wrapper,identity,archive,actor=actor,require_approved=False)
    payload=dict(type='skill.workflow.capture.reviewed',capture_id=context['capture_id'],original_episode_id=identity,
        source_review=checked['source_review'],source_review_sha256=checked['review_sha256'],archive_sha256=archive,
        decision=checked['decision'],actor_id=actor,source_wrapper=deepcopy(wrapper),wizard_id=wizard_id,
        native_payload_modified=False,approval_does_not_change_outcomes=True,automatic_training_started=False)
    event_id='skill.workflow.capture.reviewed:'+context['capture_id']+':'+checked['review_sha256']
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        from .skill_workflow import _records
        rows=[row['payload'] for row in _records(db,'capture.reviewed') if row['payload']['original_episode_id']==identity]
        latest=rows[-1] if rows else None
        if latest and latest['source_review_sha256']!=checked['review_sha256']:
            if checked['source_review']['previous_review_sha256']!=latest['source_review_sha256']:
                raise ValueError('Source review must advance the exact current known review chain')
        elif not latest and checked['review_sha256']!=job['result']['source_review_sha256']:
            if checked['source_review']['previous_review_sha256']!=job['result']['source_review_sha256']:
                raise ValueError('Source review must advance the imported original approval')
        previous=db.execute('SELECT body FROM events WHERE event_id=?',(event_id,)).fetchone()
        if previous:
            if json.loads(previous['body'])['payload']!=payload:raise ValueError('Conflicting immutable target Source review')
        else:
            document=event('nx','06',payload);document['event_id']=event_id
            db.execute('INSERT INTO events VALUES (?,?,?,?,?)',(event_id,'nx',time.time(),document['sha256'],canonical(document).decode()))
    return dict(source_review_retained=True,review_sha256=checked['review_sha256'],decision=checked['decision'],
                raw_originals_modified=False,optimizer_started=False,control_authority_adopted=False)
