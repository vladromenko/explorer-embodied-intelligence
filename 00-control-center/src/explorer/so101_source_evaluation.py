"""ENGINEERING_GLUE: explicit observed source04 policy evaluation owner.

No constructor starts control, loads a policy or opens camera/serial/network IO.
Execution requires injected existing Station and COMMON native observed-permission
facades; all commands pass the native atomic permission.command boundary.
The agent must never invoke a real authorization/start/action/test itself.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
from io import BytesIO
import json
import math
from pathlib import Path
import re
import time
import uuid

from explorer_shared.contracts import canonical, digest
from explorer_shared.source_archive import _write, sha
from .transfer_source import _metadata, SourcePolicyProbe, CONTRACT, EVALUATION_SCHEMA

ID=re.compile('[0-9a-f]{32}')
REQUEST_SCHEMA='explorer_observed_source_eval_request_v1'
PHASE_REFERENCES={name:'object' for name in ('observe_select','approach','grasp','lift_verify','select_held_object','push_contact','classify_select')}
PHASE_REFERENCES.update({name:'goal' for name in ('transport','place_release','retreat','observe_goal','push','verify_goal','validate_stack_geometry')})
PHASE_REFERENCES.update(lift='object',descend='object',close='object',carry='goal',align_release='goal',release='goal',withdraw='goal',settle='goal')


class EvaluationBlocked(ValueError):
    pass


def source_evaluation_capabilities():
    try:
        from explorer_shared.station_observed_activation import StationObservedPermission
        common=callable(getattr(StationObservedPermission,'command',None))
    except ModuleNotFoundError:
        common=False
    return dict(schema='explorer_source_evaluation_capabilities_v1',implemented=common,
        code_owner='explorer.so101_source_evaluation',common_native_permission_required=True,
        common_permission_provider_implemented=common,backend_history={'act':1,'diffusion':2},
        constructor_emits_actions=False,physical_execution_authorized=False,
        scope='software observed-evaluation owner; actual source object qualification remains per attempt')


class SourceOutcomeInbox:
    """Phase-specific challenges reuse the existing authenticated durable Inbox.

    These are outcome observations, never motion permissions. Backend.confirm
    retains its actor/body/session/expiry/cancellation checks unchanged.
    """
    def __init__(self,folder,*,clock=time.monotonic_ns,clock_domain='source04-evaluation',session_id=None):
        from explorer_shared.handover_adapters import OperatorEvidenceInbox
        self.backend=OperatorEvidenceInbox(folder,clock=clock,clock_domain=clock_domain,session_id=session_id)
        self.clock,self.domain=clock,clock_domain

    @property
    def session_id(self):return self.backend.session_id

    def pending(self,run_id=None):return self.backend.pending(run_id)

    def confirm(self,challenge_id,body,*,authenticated_actor):
        return self.backend.confirm(challenge_id,body,authenticated_actor=authenticated_actor)

    def cancel(self,run_id):return self.backend.cancel_run(run_id)

    def record_status(self,challenge_id,status,*,authenticated_actor,observing=False,note=''):
        """An explicit negative/unknown review never becomes affirmative evidence."""
        from explorer_shared.handover_adapters import _identity,_write as inbox_write
        _identity(authenticated_actor)
        if status not in ('failure','unknown') or type(observing) is not bool:
            raise ValueError('Only explicit failure/unknown uses this review path')
        with self.backend.condition:
            with self.backend._disk_guard():
                row=self.backend._row(challenge_id);now=self.clock()
                if row.get('session_id')!=self.session_id or row.get('state')!='pending' or now>=row['deadline_ns'] or self.backend._cancelled(row['run_id']):
                    raise EvaluationBlocked('Current source outcome review is no longer pending')
                reviewed=dict(row,state='reviewed',source_outcome_status=status,authenticated_actor=authenticated_actor,
                    received_ns=now,observing=observing,affirmed=False,note=str(note)[:1000])
                inbox_write(self.backend.folder/(challenge_id+'.json'),reviewed)
            self.backend.condition.notify_all()
        return reviewed

    def request(self,run_id,object_id,phase_id,question,after_command_id,*,timeout_s=120):
        from explorer_shared.handover_adapters import _identity,_write as inbox_write
        for value in (run_id,object_id,after_command_id):_identity(value)
        if not isinstance(phase_id,str) or not 0<len(phase_id)<=100 or not isinstance(question,str) or not 0<len(question)<=1000 or not 0<timeout_s<=300:
            raise ValueError('Bounded explicit phase/outcome challenge required')
        ident=uuid.uuid4().hex;now=self.clock()
        row=dict(challenge_id=ident,session_id=self.session_id,run_id=run_id,object_id=object_id,
            role='source',phase_id=phase_id,statement='source_object_phase_outcome',question=question,
            after_command_id=after_command_id,created_ns=now,deadline_ns=now+int(timeout_s*1e9),clock_domain=self.domain,state='pending')
        with self.backend.condition:
            with self.backend._disk_guard():
                if self.backend._cancelled(run_id):raise EvaluationBlocked('Stopped source outcome session cannot be revived')
                inbox_write(self.backend.folder/(ident+'.json'),row)
            self.backend.condition.notify_all()
        return row

    def consume(self,challenge_id,*,run_id,phase_id,after_command_id):
        from explorer_shared.handover_adapters import _write as inbox_write
        with self.backend.condition:
            with self.backend._disk_guard():
                row=self.backend._row(challenge_id);now=self.clock()
                if (row.get('session_id')!=self.session_id or row.get('run_id')!=run_id or row.get('phase_id')!=phase_id or
                        row.get('after_command_id')!=after_command_id or row.get('state') not in ('confirmed','reviewed') or
                        now>=row['deadline_ns'] or self.backend._cancelled(run_id) or
                        not row.get('authenticated_actor') or
                        row.get('state')=='confirmed' and (row.get('observing') is not True or row.get('affirmed') is not True)):
                    raise EvaluationBlocked('Current exact authenticated source outcome is not confirmed')
                consumed=dict(row,state='consumed',consumed_ns=now)
                inbox_write(self.backend.folder/(challenge_id+'.json'),consumed)
        return consumed


def request_settings(store,model_job_id,body=None,*,admission_verifier=None):
    body=body or {}
    if set(body)-{'duration_s','object_instance_id','scene_id','goal_instance_id','review_timeout_s'}:
        raise EvaluationBlocked('Source evaluation form accepts task context and finite duration only')
    if not isinstance(model_job_id,str) or not ID.fullmatch(model_job_id):raise EvaluationBlocked('Exact current source model ID required')
    model=_metadata(store,store.job(model_job_id),admission_verifier=admission_verifier)
    spec=model['manifest']['skill_spec'];phases=[row['id'] for row in spec['stages']]
    if spec.get('task_type') in (None,'hold','joint_trajectory','trajectory') or any(name not in PHASE_REFERENCES for name in phases):
        raise EvaluationBlocked('Explicit supported whole object SkillSpec/phase-reference plan required')
    duration=body.get('duration_s',20);review=body.get('review_timeout_s',120)
    if type(duration) not in (int,float) or not math.isfinite(duration) or not 0<duration<=60 or type(review) not in (int,float) or not math.isfinite(review) or not 0<review<=300:
        raise EvaluationBlocked('Finite observed attempt<=60s and review<=300s required')
    context={}
    for key in ('object_instance_id','scene_id','goal_instance_id'):
        value=body.get(key)
        if not isinstance(value,str) or not 0<len(value)<=160 or not re.fullmatch('[A-Za-zА-Яа-яЁё0-9_.: -]+',value):
            raise EvaluationBlocked('Plain bounded object/scene/goal descriptions required')
        context[key]=value
    binding=dict(purpose='source04_eval',model_job_id=model_job_id,skill_id=model['manifest']['skill_id'],
        skill_version=model['manifest']['skill_version'],spec_sha256=model['manifest']['spec_sha256'],
        checkpoint_content_sha256=model['checkpoint_content_sha256'],action_contract_id=CONTRACT,source_domain=model['source_domain'])
    return dict(schema=REQUEST_SCHEMA,source_model_job_id=model_job_id,binding=binding,
        source_domain=model['source_domain'],duration_s=float(duration),review_timeout_s=float(review),
        max_policy_steps=int(math.ceil(duration/.1))+1,
        source_phases=phases,phase_reference={phase:PHASE_REFERENCES[phase] for phase in phases},
        split='development',history_steps=model['history_steps'],backend=model['backend'],
        aggregate_manifest_sha256=model['result']['aggregate_manifest_sha256'],context=context,
        hardware_permission_is_not_granted_by_this_request=True)


def _phase_in_db(db,job):
    requested=job['config'];prefix='source.eval.phase:'+job['id']+':'
    rows=list(db.execute('SELECT event_id,sha256,body FROM events WHERE event_id LIKE ? ORDER BY rowid',(prefix+'%',)))
    latest=None
    for row in rows:
        body=json.loads(row['body']);payload=body['payload']
        if (digest(payload)!=row['sha256'] or body.get('sha256')!=row['sha256'] or body.get('event_id')!=row['event_id'] or
                payload.get('source_eval_job_id')!=job['id'] or payload.get('spec_sha256')!=requested['binding']['spec_sha256'] or
                payload.get('phase_id') not in requested['source_phases']):
            raise EvaluationBlocked('Immutable source phase annotation integrity failed')
        latest=payload
    phase=latest['phase_id'] if latest else requested['source_phases'][0]
    return dict(phase_id=phase,reference=requested['phase_reference'][phase],revision=digest(latest) if latest else 'initial')


def current_phase(store,job_id):
    job=store.job(job_id)
    if not job or job.get('kind')!='so101_skill_eval' or (job.get('config') or {}).get('schema')!=REQUEST_SCHEMA:
        raise EvaluationBlocked('Typed source evaluation phase context required')
    with store.connect() as db:return _phase_in_db(db,job)


def attach_permission(store,job_id,grant_id,expected_control_epoch,expected_owner_session_id,*,permission):
    """Attach an ALREADY user-authorized native grant, after the model is ready."""
    from explorer_shared.contracts import event
    job=store.job(job_id)
    if not job or job.get('kind')!='so101_skill_eval' or job.get('status')!='running':raise EvaluationBlocked('Current prepared source evaluation required')
    binding=job['config']['binding']
    receipt=_grant(permission,grant_id,binding,expected_control_epoch)
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        row=db.execute('SELECT status,result FROM jobs WHERE id=?',(job_id,)).fetchone()
        status=json.loads(row['result']) if row and row['result'] else {}
        if (not row or row['status']!='running' or status.get('phase')!='awaiting_source_observed_grant' or
                status.get('source_owner_session_id')!=expected_owner_session_id):
            raise EvaluationBlocked('Source model preparation ended or another worker session is current')
        payload=dict(type='source.eval.permission',source_eval_job_id=job_id,source_owner_session_id=expected_owner_session_id,
            grant_id=grant_id,expected_control_epoch=expected_control_epoch,binding=binding,permission_receipt=receipt,
            authorization_created_by_this_attach=False)
        ident='source.eval.permission:'+job_id+':'+digest(payload)
        envelope=event('nx','04',payload);envelope['event_id']=ident
        db.execute('INSERT OR IGNORE INTO events VALUES (?,?,?,?,?)',(ident,'nx',time.time(),envelope['sha256'],canonical(envelope).decode()))
    return dict(attached=True,source_owner_session_id=expected_owner_session_id,grant_id=grant_id,expected_control_epoch=expected_control_epoch,
        authorization_created=False)


def current_permission(store,job_id,owner_session_id):
    with store.connect() as db:
        rows=list(db.execute('SELECT event_id,sha256,body FROM events WHERE event_id LIKE ? ORDER BY rowid',('source.eval.permission:'+job_id+':%',)))
    latest=None
    for row in rows:
        envelope=json.loads(row['body']);payload=envelope['payload']
        if digest(payload)!=row['sha256'] or envelope.get('sha256')!=row['sha256'] or envelope.get('event_id')!=row['event_id']:
            raise EvaluationBlocked('Immutable source observed permission attachment changed')
        if payload.get('source_owner_session_id')==owner_session_id:latest=payload
    return latest


def record_phase(store,job_id,body,*,authenticated_actor):
    """Metadata annotation only, exact revision/native epoch; never policy input."""
    from explorer_shared.handover_adapters import _identity
    from explorer_shared.contracts import event
    _identity(authenticated_actor)
    if not isinstance(body,dict) or set(body)!={'phase_id','expected_revision','expected_control_epoch'}:
        raise EvaluationBlocked('Exact phase/revision/current native epoch required')
    if not isinstance(job_id,str) or not ID.fullmatch(job_id):raise EvaluationBlocked('Exact source evaluation ID required')
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        raw=db.execute('SELECT kind,status,config,result FROM jobs WHERE id=?',(job_id,)).fetchone()
        if not raw or raw['kind']!='so101_skill_eval' or raw['status']!='running':raise EvaluationBlocked('Only the current running source evaluation can be annotated')
        job=dict(id=job_id,config=json.loads(raw['config']));status=json.loads(raw['result']) if raw['result'] else {}
        if (status.get('phase')!='observed_native_source_policy' or status.get('control_epoch')!=body['expected_control_epoch'] or
                not status.get('source_owner_session_id')):
            raise EvaluationBlocked('Source native session ended or changed before the annotation')
        current=_phase_in_db(db,job);phases=job['config']['source_phases'];selected=body['phase_id']
        if body['expected_revision']!=current['revision'] or selected not in phases or phases.index(selected) not in (phases.index(current['phase_id']),phases.index(current['phase_id'])+1):
            raise EvaluationBlocked('Source phase revision changed or a phase was skipped')
        if selected!=current['phase_id'] and status.get('current_source_phase')!=current['phase_id']:
            raise EvaluationBlocked('Current phase has no recorded actual policy/native sample yet')
        payload=dict(type='source.eval.phase',source_eval_job_id=job_id,model_job_id=job['config']['source_model_job_id'],
            spec_sha256=job['config']['binding']['spec_sha256'],phase_id=selected,previous_revision=current['revision'],
            control_epoch=status['control_epoch'],source_owner_session_id=status['source_owner_session_id'],
            after_native_command_id=status.get('last_native_command_id'),authenticated_actor=authenticated_actor,
            metadata_only=True,phase_labels_in_actor=False)
        ident='source.eval.phase:'+job_id+':'+digest(payload)
        envelope=event('nx','04',payload);envelope['event_id']=ident
        db.execute('INSERT OR IGNORE INTO events VALUES (?,?,?,?,?)',(ident,'nx',time.time(),envelope['sha256'],canonical(envelope).decode()))
        return dict(phase_id=selected,reference=job['config']['phase_reference'][selected],revision=digest(payload),metadata_only=True)


def _packet(station,model,clock):
    import numpy as np
    from PIL import Image
    from explorer_shared.learning.act_policy import validate_observation
    began=clock();packet=station.observation();observation=packet['observation']
    if (packet.get('schema')!='explorer_so101_cached_observation_v1' or
            packet.get('names')!=model['source_profile']['names'] or packet.get('units')!=model['source_profile']['units']):
        raise EvaluationBlocked('Exact typed native source observation order/units required')
    jpeg,headers=station.frame(observation['observation_id'])
    expected={'x-observation-id':observation['observation_id'],'x-frame-id':observation['frame_id'],
        'x-boot-id':observation['frame_boot_id'],'x-jpeg-sha256':observation['frame_sha256']}
    if not isinstance(jpeg,bytes) or len(jpeg)>2*1024**2 or sha(jpeg)!=observation['frame_sha256'] or any(headers.get(key)!=value for key,value in expected.items()):
        raise EvaluationBlocked('Exact pinned source JPEG/frame/boot/SHA differs')
    with Image.open(BytesIO(jpeg)) as image:
        if image.size!=(observation['width'],observation['height']) or image.width*image.height>1920*1080:raise EvaluationBlocked('Source image dimensions differ')
        rgb=np.asarray(image.convert('RGB'))
    if observation['camera_id'] not in model['source_camera_ids'] or observation.get('source_cutoff_basis')!='native_observation_packet_created':
        raise EvaluationBlocked('Fresh actual source camera packet required; offline replay is not execution')
    validate_observation(rgb,packet['measured_native6'],packet['valid_mask'],observation,camera_id=observation['camera_id'],
        calibration_sha256=model['source_calibration_sha256'],low=np.asarray(model['source_profile']['low']),high=np.asarray(model['source_profile']['high']))
    if clock()-began>2_000_000_000:raise EvaluationBlocked('Source observation transport expired')
    return packet,jpeg,rgb,began


def _grant(permission,grant_id,binding,epoch):
    receipt=permission.check(grant_id,binding,epoch)
    if not isinstance(receipt,dict) or receipt.get('grant_id')!=grant_id or receipt.get('binding')!=binding or receipt.get('control_epoch')!=epoch or receipt.get('execution_permission') is not True:
        raise EvaluationBlocked('COMMON current native observed permission receipt required')
    return receipt


def _command(permission,grant_id,binding,operation,body):
    reply=permission.command(grant_id,binding,operation,body)
    if not isinstance(reply,dict) or reply.get('native_command_called') is not True or not isinstance(reply.get('status'),dict):
        raise EvaluationBlocked('Atomic COMMON native Station command receipt required')
    return reply['status']


def _alive(store,job_id):
    current=store.job(job_id)
    if not current or current.get('kind')!='so101_skill_eval' or current.get('status')!='running':
        raise EvaluationBlocked('Observed source attempt cancelled, ended or no longer owned by this worker')


def run_so101_skill_eval(store,job,config=None,*,station=None,permission=None,phase_provider=None,inbox=None,
                         loader=None,admission_verifier=None,grant_provider=None,clock=time.monotonic_ns,sleep=time.sleep):
    """Actual explicit bounded learned-policy loop through injected native owner.

    No default transport exists: merely queueing/importing this module cannot
    authorize or connect to a device. Root must bind the COMMON guarded facade.
    """
    import numpy as np
    requested=job.get('config') or {}
    if job.get('kind')!='so101_skill_eval' or not ID.fullmatch(str(job.get('id',''))) or requested.get('schema')!=REQUEST_SCHEMA:
        raise EvaluationBlocked('Typed server source evaluation request required')
    if station is None or permission is None or phase_provider is None:
        raise EvaluationBlocked('A_BOUND_COMMON_STATION_OBSERVED_OWNER_REQUIRED')
    model=_metadata(store,store.job(requested['source_model_job_id']),admission_verifier=admission_verifier)
    expected=request_settings(store,requested['source_model_job_id'],dict(requested['context'],duration_s=requested['duration_s'],review_timeout_s=requested['review_timeout_s']),admission_verifier=admission_verifier)
    if any(requested.get(key)!=value for key,value in expected.items()):raise EvaluationBlocked('Queued source request/model/Spec changed')
    if getattr(station,'execution_domain',None)!=model['source_domain']:raise EvaluationBlocked('SIM source cannot execute on physical Station or change its domain')
    grant_id=requested.get('grant_id');initial_epoch=requested.get('expected_control_epoch')
    if grant_id is not None and (not ID.fullmatch(str(grant_id)) or not isinstance(initial_epoch,str) or not initial_epoch):raise EvaluationBlocked('Server-confirmed exact COMMON grant/native epoch required')
    folder=Path(store.root).resolve()/'jobs'/job['id']
    if folder.resolve()!=folder:raise EvaluationBlocked('Private evaluation folder alias forbidden')
    folder.mkdir(parents=True,exist_ok=True,mode=0o700)
    if (folder/'attempt-context.json').exists():raise EvaluationBlocked('Interrupted observed attempt is retained; use a new attempt/grant')
    inbox=inbox or SourceOutcomeInbox(folder/'outcomes',clock=clock,clock_domain='source04:'+Path('/proc/sys/kernel/random/boot_id').read_text().strip())
    _write(folder/'attempt-context.json',canonical(dict(requested=requested,owner_session_id=inbox.session_id,physical_io=False)))
    binding=dict(schema='explorer_native_source_probe_binding_v1',model_job_id=model['job']['id'],backend=model['backend'],
        checkpoint_path=model['checkpoint_path'],checkpoint_sha256=model['checkpoint_sha256'],checkpoint_content_sha256=model['checkpoint_content_sha256'],
        source_camera_ids=model['source_camera_ids'],source_calibration_sha256=model['source_calibration_sha256'],
        history_steps=model['history_steps'],prediction_steps=model['prediction_steps'])
    rows=[];history=[];owned_epoch=None;start_sent=False;error=None;stop=None;last_phase=-1;runtime_qualified=False;implementation_snapshot=None
    began=clock();sequence=0
    try:
        _alive(store,job['id'])
        if grant_id is not None:
            _write(folder/'grant-before-load.json',canonical(_grant(permission,grant_id,requested['binding'],initial_epoch)))
        else:
            _write(folder/'grant-before-load.json',canonical(dict(permission_not_yet_requested=True,loading_emits_no_native_commands=True)))
        # Loading itself is nonmoving. Permission is checked anew AFTER loading.
        probe=SourcePolicyProbe(binding,camera_id=model['source_camera_ids'][0],loader=loader)
        actor=probe.policy
        runtime_qualified=(type(actor).__module__ in ('explorer_shared.learning.act_policy','explorer_shared.learning.diffusion_policy') and
            type(actor).__name__ in ('NativeACTPolicy','NativeDiffusionPolicy') and getattr(actor,'checkpoint_sha256',None)==model['checkpoint_sha256'])
        import importlib.metadata
        versions={}
        for name in ('lerobot','torch','torchvision','numpy','diffusers'):
            try:versions[name]=importlib.metadata.version(name)
            except importlib.metadata.PackageNotFoundError:versions[name]=None
        _write(folder/'runtime-actor.json',canonical(dict(module=type(actor).__module__,class_name=type(actor).__qualname__,
            checkpoint_bound_native_adapter=runtime_qualified,loader_injected=loader is not None,
            software_diagnostic_only=not runtime_qualified,package_versions=versions)))
        if runtime_qualified and (versions['lerobot']!='0.6.1' or model['backend']=='diffusion' and versions['diffusers']!='0.38.0'):
            raise EvaluationBlocked('Exact qualified native source runtime package versions required')
        import inspect
        from explorer_shared.source_archive import archive_sources,verify_source_archive
        from . import transfer_source as source_adapter
        implementation_files={Path(__file__).resolve(),Path(source_adapter.__file__).resolve(),Path(inspect.getfile(type(actor))).resolve()}
        implementation_snapshot=archive_sources(folder/'implementation-snapshot',
            {str(path):sha(path.read_bytes()) for path in implementation_files},
            context=dict(kind='explicit_evaluation_owner_adapter_not_complete_OS_runtime',physical_actions_emitted=False))
        verify_source_archive(implementation_snapshot['manifest_path'],implementation_snapshot['manifest_sha256'],recheck_originals=True)
        if model['source_domain']=='PHYSICAL' and not runtime_qualified:
            raise EvaluationBlocked('Qualified saved native policy adapter required before any physical native start')
        _alive(store,job['id'])
        if grant_id is None:
            store.update_job(job['id'],progress=.05,result=dict(phase='awaiting_source_observed_grant',source_owner_session_id=inbox.session_id,
                source_model_job_id=model['job']['id'],binding=requested['binding'],model_ready=True,hardware_commands_dispatched=False))
            get_grant=grant_provider or (lambda:current_permission(store,job['id'],inbox.session_id))
            until=clock()+int(requested['review_timeout_s']*1e9)
            while grant_id is None and clock()<until:
                _alive(store,job['id']);selected=get_grant()
                if selected is not None:
                    if (selected.get('source_owner_session_id')!=inbox.session_id or selected.get('binding')!=requested['binding'] or
                            not ID.fullmatch(str(selected.get('grant_id'))) or not selected.get('expected_control_epoch')):
                        raise EvaluationBlocked('Exact current prepared source worker permission attachment required')
                    grant_id=selected['grant_id'];initial_epoch=selected['expected_control_epoch']
                else:sleep(.05)
            if grant_id is None:raise EvaluationBlocked('No current user-observed native grant attached after model preparation')
        prepared_grant=_grant(permission,grant_id,requested['binding'],initial_epoch)
        _write(folder/'grant-after-load.json',canonical(prepared_grant))
        # Reject a different connected calibration/camera BEFORE native start,
        # which can enable holding torque even before a learned action exists.
        initial_packet,initial_jpeg,_,_=_packet(station,model,clock)
        initial_observation=initial_packet['observation']
        displayed=prepared_grant.get('displayed_frame') or {}
        if (prepared_grant.get('follower_calibration_sha256')!=model['source_calibration_sha256'] or
                displayed.get('camera_id')!=initial_observation['camera_id'] or
                prepared_grant.get('boot_id')!=initial_observation['source_cutoff_boot_id'] or
                prepared_grant.get('clock_domain')!=initial_observation['source_cutoff_clock_domain']):
            raise EvaluationBlocked('Observed grant camera/calibration/native boot/clock differs from the model before native start')
        _write(folder/'prestart-observation.json',canonical(initial_packet))
        _write(folder/'prestart-observation.jpg',initial_jpeg)
        _alive(store,job['id'])
        _grant(permission,grant_id,requested['binding'],initial_epoch)
        current=station.status()
        if (current.get('control_epoch')!=initial_epoch or current.get('available') is not True or current.get('configuration_writes')!=0 or
                list(current.get('action_names') or [])!=model['source_profile']['names'] or current.get('action_units')!=model['source_profile']['units']):
            raise EvaluationBlocked('Native epoch/availability/original source coordinate profile differs')
        began=clock();start_sent=True
        status=_command(permission,grant_id,requested['binding'],'policy/start',dict(expected_control_epoch=initial_epoch,observing=True,operator_present=True))
        _write(folder/'native-policy-start-status.json',canonical(status))
        owned_epoch=status['control_epoch'];_grant(permission,grant_id,requested['binding'],owned_epoch)
        while clock()-began<int(requested['duration_s']*1e9) and sequence<requested['max_policy_steps']:
            _alive(store,job['id'])
            _grant(permission,grant_id,requested['binding'],owned_epoch)
            packet,jpeg,rgb,received=_packet(station,model,clock);observation=packet['observation']
            if history and observation['frame_id']==history[-1][0]['observation']['frame_id']:
                sleep(.01)
            else:
                history.append((packet,jpeg,rgb));history=history[-model['history_steps']:]
                for item in history:
                    name='observations/'+item[0]['observation']['observation_id']
                    _write(folder/(name+'.json'),canonical(item[0]));_write(folder/(name+'.jpg'),item[1])
                if len(history)==model['history_steps']:
                    phase=phase_provider();phase_id=phase.get('phase_id');reference=phase.get('reference')
                    if phase_id not in requested['source_phases'] or reference!=requested['phase_reference'][phase_id]:raise EvaluationBlocked('Current explicit source phase/reference required')
                    index=requested['source_phases'].index(phase_id)
                    if index<last_phase or index>last_phase+1:raise EvaluationBlocked('Source phases cannot be skipped or retroactively relabelled')
                    last_phase=index
                    predicted=probe.predict_history(np.stack([item[2] for item in history]),np.asarray([item[0]['measured_native6'] for item in history]),
                        valid_mask=np.asarray([item[0]['valid_mask'] for item in history]),observations=[item[0]['observation'] for item in history])
                    _alive(store,job['id'])
                    _grant(permission,grant_id,requested['binding'],owned_epoch)
                    if clock()-received>2_000_000_000:raise EvaluationBlocked('Native observation expired during model inference')
                    action=predicted['predicted_native6'][0]
                    _write(folder/('predictions/'+str(sequence).zfill(6)+'.json'),canonical(dict(predicted,observation_ids=[item[0]['observation']['observation_id'] for item in history])))
                    action_body=dict(observing=True,operator_present=True,control_epoch=owned_epoch,
                        sequence=sequence,action_names=list(model['source_profile']['names']),action_units=list(model['source_profile']['units']),action=action,duration_s=1,
                        source_frame_monotonic_ns=observation['frame_monotonic_ns'],source_clock_domain=observation['frame_clock_domain'],source_frame_id=observation['frame_id'])
                    _write(folder/('requests/'+str(sequence).zfill(6)+'.json'),canonical(action_body))
                    accepted=_command(permission,grant_id,requested['binding'],'action',action_body)
                    _write(folder/('accepted/'+str(sequence).zfill(6)+'.json'),canonical(dict(status=accepted,level='accepted_not_dispatch_evidence')))
                    # HTTP/action acceptance is never the actual bus-write receipt.
                    dispatched=None;deadline=clock()+500_000_000
                    while dispatched is None and clock()<deadline:
                        _grant(permission,grant_id,requested['binding'],owned_epoch)
                        status=station.status()
                        if status.get('control_epoch')!=owned_epoch or status.get('mode')!='policy' or status.get('configuration_writes')!=0:raise EvaluationBlocked('Source Station stopped, changed owner or altered its settings')
                        candidate=status.get('last_command') or {}
                        if candidate.get('control_epoch')==owned_epoch and candidate.get('sequence')==sequence:
                            dispatched=candidate
                        elif candidate.get('control_epoch')==owned_epoch and candidate.get('sequence',-1)>sequence:
                            raise EvaluationBlocked('Another command superseded this source policy step')
                        else:sleep(.005)
                    if not dispatched:raise EvaluationBlocked('No actual native source bus-write receipt')
                    tick=dispatched.get('monotonic_ns');source_observation=dispatched.get('source_observation') or {}
                    if (dispatched.get('clock_domain')!=observation['source_cutoff_clock_domain'] or type(tick) is not int or tick<observation['source_cutoff_monotonic_ns'] or
                            source_observation.get('frame_id')!=observation['frame_id'] or dispatched.get('requested')!=action or dispatched.get('dispatched')!=action):
                        raise EvaluationBlocked('Actual source dispatch/model/frame/clock differs')
                    command_id=owned_epoch+':'+str(sequence)+':'+str(tick)
                    row=dict(index=sequence,phase_id=phase_id,reference=reference,packet=packet,
                        prediction=predicted,native_dispatch=dispatched,native_dispatch_receipt_id=command_id,
                        model_input_frame_ids=[item[0]['observation']['frame_id'] for item in history],
                        native_status_configuration_writes=status['configuration_writes'],
                        encoded_frame_file='observations/'+observation['observation_id']+'.jpg')
                    _write(folder/('steps/'+str(sequence).zfill(6)+'.json'),canonical(row));rows.append(row);sequence+=1
                    store.update_job(job['id'],progress=min(.8,(clock()-began)/(requested['duration_s']*1e9)*.8),result=dict(
                        phase='observed_native_source_policy',source_model_job_id=model['job']['id'],source_owner_session_id=inbox.session_id,
                        current_source_phase=phase_id,last_native_command_id=command_id,control_epoch=owned_epoch,frames=len(rows)))
                    sleep(.1)
    except Exception as failure:
        error=dict(type=type(failure).__name__,reason=str(failure))
    finally:
        # A failed/late start is cleaned by the exact grant/native owner only.
        if start_sent and owned_epoch is not None:
            try:
                body={'expected_control_epoch':owned_epoch}
                stop=_command(permission,grant_id,requested['binding'],'stop',body)
            except Exception as failure:
                stop=dict(confirmed=False,error_type=type(failure).__name__,reason=str(failure))
        elif start_sent:
            stop=dict(confirmed=False,reason='Native start response unconfirmed; no exact owned epoch exists for scoped STOP')
        if grant_id is not None:
            try:permission.revoke('SOURCE_ATTEMPT_ENDED',expected_grant_id=grant_id,
                expected_control_epoch=owned_epoch or initial_epoch,expected_binding=requested['binding'])
            except Exception as failure:
                if error is None:error=dict(type=type(failure).__name__,reason='Scoped grant revocation failed: '+str(failure))
        _write(folder/'stop-receipt.json',canonical(stop or dict(control_never_started=True)))
    # Actual outcome recording/qualified bundle construction is intentionally a
    # separate nonmoving phase, after the native lease has ended.
    return _finish(store,job,requested,model,folder,rows,error,stop,inbox,clock,sleep,admission_verifier,runtime_qualified,implementation_snapshot)


def _finish(store,job,requested,model,folder,rows,error,stop,inbox,clock,sleep,admission_verifier=None,runtime_qualified=False,implementation_snapshot=None):
    phases=requested['source_phases'];observed={row['phase_id'] for row in rows}
    outcome_rows={};challenges=[]
    started=folder/'native-policy-start-status.json'
    owned_epoch=json.loads(started.read_bytes()).get('control_epoch') if started.is_file() else None
    stop_confirmed=bool(stop and stop.get('mode')=='idle' and owned_epoch and stop.get('control_epoch')!=owned_epoch)
    if not stop_confirmed and error is None:error=dict(type='ScopedStopNotConfirmed',reason='Native owner end was not confirmed')
    if set(phases)!=observed and error is None:error=dict(type='IncompleteSourcePhases',reason='Every whole-source phase must have actual policy/native samples')
    for phase in phases:
        matching=[row for row in rows if row['phase_id']==phase]
        outcome_rows[phase]=dict(status='unknown',independent_of_action_commands=False,method='not_confirmed',sample_count=len(matching))
    whole=dict(status='unknown',independent_of_action_commands=False,method='not_confirmed')
    if error is None:
        for phase in phases+['__whole_task__']:
            matching=rows if phase=='__whole_task__' else [row for row in rows if row['phase_id']==phase]
            after=matching[-1]['native_dispatch_receipt_id']
            question=('Подтвердите по сохранённому видео полный предметный исход самостоятельной попытки модели SO101.' if phase=='__whole_task__' else
                'Подтвердите по сохранённому видео успешный предметный исход фазы «'+phase+'» самостоятельной модели SO101; команда или закрытие захвата сами по себе не являются доказательством.')
            # Inbox's ASCII correlation alias is per run. It is NOT a scientific
            # object-instance identity or proof that two actual objects differ.
            object_alias='source-object-'+job['id']
            question+=' Предмет: «'+requested['context']['object_instance_id']+'».'
            challenges.append(inbox.request(job['id'],object_alias,phase,question,after,timeout_s=requested['review_timeout_s']))
        _write(folder/'outcome-challenges.json',canonical(challenges))
        store.update_job(job['id'],progress=.9,result=dict(phase='source_outcome_review_after_native_stop',source_owner_session_id=inbox.session_id,
            source_model_job_id=model['job']['id'],control_stopped=True,pending_evidence=[row['challenge_id'] for row in challenges],frames=len(rows)))
        deadline=clock()+int(requested['review_timeout_s']*1e9);pending=list(challenges)
        while pending and clock()<deadline:
            current=store.job(job['id'])
            if current and current['status'] in ('cancel_requested','cancelled'):
                inbox.cancel(job['id']);error=dict(type='OutcomeReviewCancelled',reason='Raw attempt retained without qualification');break
            remaining=[]
            for challenge in pending:
                try:
                    confirmation=inbox.consume(challenge['challenge_id'],run_id=job['id'],phase_id=challenge['phase_id'],after_command_id=challenge['after_command_id'])
                except EvaluationBlocked:
                    remaining.append(challenge)
                else:
                    status=confirmation.get('source_outcome_status','success')
                    evidence=dict(status=status,independent_of_action_commands=confirmation.get('observing') is True,
                        method='authenticated_operator_video_confirmation',confirmation=confirmation)
                    if challenge['phase_id']=='__whole_task__':whole=evidence
                    else:outcome_rows[challenge['phase_id']]=evidence
            pending=remaining
            if pending:sleep(.05)
    try:
        from explorer_shared.source_archive import verify_source_archive
        if implementation_snapshot is not None:
            verify_source_archive(implementation_snapshot['manifest_path'],implementation_snapshot['manifest_sha256'],recheck_originals=True)
        final=_metadata(store,store.job(requested['source_model_job_id']),admission_verifier=admission_verifier)
        if final['checkpoint_content_sha256']!=model['checkpoint_content_sha256']:raise EvaluationBlocked('Source checkpoint changed')
    except Exception as failure:
        if error is None:error=dict(type='SourceAdmissionChangedAfterAttempt',reason=str(failure))
    success=bool(runtime_qualified and error is None and whole['status']=='success' and all(outcome_rows[p]['status']=='success' for p in phases))
    receipt=dict(schema=EVALUATION_SCHEMA,source_model_job_id=model['job']['id'],source_domain=model['source_domain'],
        skill_id=model['manifest']['skill_id'],skill_version=model['manifest']['skill_version'],spec_sha256=model['manifest']['spec_sha256'],
        checkpoint_content_sha256=model['checkpoint_content_sha256'],aggregate_manifest_sha256=model['result']['aggregate_manifest_sha256'],
        source_calibration_sha256=model['source_calibration_sha256'],camera_id=model['source_camera_ids'][0],
        execution_kind=('fresh_observation_learned_policy_rollout' if rows else 'qualified_policy_attempt_without_confirmed_dispatch') if runtime_qualified else 'software_injected_predictor_diagnostic',raw_episode_replay_only=False,
        qualified_saved_model_runtime_loaded=runtime_qualified,qualified_saved_model_runtime_executed=runtime_qualified and bool(rows),
        learned_model_inference_recorded=runtime_qualified and bool(list((folder/'predictions').glob('*.json'))),
        independent_full_task_success=success,per_phase=outcome_rows,end_to_end=whole,error=error,
        separate_user_observed_test=model['source_domain']=='PHYSICAL' and bool(rows),existing_controller_settings_preserved=bool(rows) and all(row['native_status_configuration_writes']==0 for row in rows),
        context=requested['context'],object_correlation_alias_is_not_independent_instance_proof=True,
        implementation_snapshot=implementation_snapshot,complete_OS_runtime_reproducibility_claimed=False,
        native_stop_confirmed=stop_confirmed,source_owner_session_id=inbox.session_id,
        phase_labels_in_actor=False,text_in_actor=False,policy_outputs_dispatched=bool(rows),steps=[])
    for row in rows:
        observation=row['packet']['observation'];dispatch=row['native_dispatch']
        receipt['steps'].append(dict(index=row['index'],phase_id=row['phase_id'],reference=row['reference'],
            actual_dispatched_native6=dispatch['dispatched'],native_dispatch_receipt_id=row['native_dispatch_receipt_id'],
            prediction_kind='fresh_observation_learned_policy_inference',checkpoint_content_sha256=model['checkpoint_content_sha256'],
            camera_id=observation['camera_id'],frame_id=observation['frame_id'],frame_sha256=observation['frame_sha256'],
            model_input_frame_ids=row['model_input_frame_ids'],
            observation_stamp=dict(clock_domain=observation['source_cutoff_clock_domain'],boot_id=observation['source_cutoff_boot_id'],monotonic_ns=observation['source_cutoff_monotonic_ns']),
            dispatch_stamp=dict(clock_domain=dispatch['clock_domain'],boot_id=observation['source_cutoff_boot_id'],monotonic_ns=dispatch['monotonic_ns'])))
    _write(folder/'evaluation-receipt.json',canonical(receipt))
    source_trace=None
    if success:
        bundle=folder/'source-bundle';bundle.mkdir()
        inventory={}
        def retain(name,data):
            _write(bundle/name,data);inventory[name]=sha(data)
        for name in model['checkpoint_sha256']:retain('checkpoint/'+name,(Path(model['checkpoint_path'])/name).read_bytes())
        retain('lineage/source-aggregate-manifest.json',Path(model['manifest_path']).read_bytes())
        retain('lineage/source-internal-view-manifest.json',Path(model['view_path']).read_bytes())
        retain('lineage/source-training-result.json',(Path(store.root)/'jobs'/model['job']['id']/'result.json').read_bytes())
        retain('implementation/so101_source_evaluation.py',Path(__file__).read_bytes())
        from . import transfer_source as source_adapter
        retain('implementation/transfer_source.py',Path(source_adapter.__file__).read_bytes())
        retain('evaluation-receipt.json',(folder/'evaluation-receipt.json').read_bytes())
        frames=[]
        for row in rows:
            observation=row['packet']['observation'];name='rgb/'+str(row['index']).zfill(6)+'.jpg'
            retain(name,(folder/row['encoded_frame_file']).read_bytes())
            frames.append(dict(native_action6=row['native_dispatch']['dispatched'],phase_id=row['phase_id'],reference=row['reference'],
                source_kind='QUALIFIED_SOURCE_MODEL',source_rgb_file=name,minimum_target_steps=1))
        # Retain originals, requested/accepted/dispatched/measured distinctions and
        # every raw contextual observation, including DP's pre-first-action frame.
        for tree in ('steps','requests','accepted','predictions','observations','outcomes'):
            for path in sorted((folder/tree).rglob('*')):
                if path.is_file() and path.name!='.inbox.lock':retain('raw/'+tree+'/'+path.relative_to(folder/tree).as_posix(),path.read_bytes())
        for path in sorted((folder/'implementation-snapshot').rglob('*')):
            if path.is_file():retain('implementation-snapshot/'+path.relative_to(folder/'implementation-snapshot').as_posix(),path.read_bytes())
        for name in ('attempt-context.json','runtime-actor.json','grant-before-load.json','grant-after-load.json','prestart-observation.json','prestart-observation.jpg','native-policy-start-status.json','stop-receipt.json'):
            retain('raw/'+name,(folder/name).read_bytes())
        source_trace=dict(schema='explorer_skill_transfer_source_trace_v1',experiment_slug='so101_source_learning',source_kind='QUALIFIED_SOURCE_MODEL',
            source_domain=model['source_domain'],task_type=model['manifest']['skill_spec']['task_type'],
            skill_id=model['manifest']['skill_id'],skill_version=model['manifest']['skill_version'],action_contract_id=CONTRACT,action_names=list(model['source_profile']['names']),
            split=dict(partition='development',episode_id=job['id'],session_id=inbox.session_id,scene_id=requested['context']['scene_id'],group_id=job['id']),
            qualification=dict(independent_closed_loop_object_success=True,source_model_sha256=model['checkpoint_sha256']['model.safetensors'],
                source_checkpoint_content_sha256=model['checkpoint_content_sha256'],processor_sha256=model['checkpoint_sha256']['explorer_preprocessing.json'],
                evaluation_receipt_sha256=inventory['evaluation-receipt.json'],evaluation_run_ids=[job['id']],raw_episode_replay_only=False),
            source_files_sha256=inventory,frames=frames)
        _write(bundle/'source-trace.json',canonical(source_trace))
        from explorer_shared.skill_transfer import load_source_trace
        load_source_trace(bundle/'source-trace.json',sha((bundle/'source-trace.json').read_bytes()))
    result=dict(receipt,steps=len(rows),source_trace_manifest=str(folder/'source-bundle/source-trace.json') if success else None,
        source_trace_sha256=sha((folder/'source-bundle/source-trace.json').read_bytes()) if success else None,
        evaluation_receipt_sha256=sha((folder/'evaluation-receipt.json').read_bytes()),
        source_kind='SIMULATION' if model['source_domain']=='SIMULATION' else 'SO101_RECORDED_DEMONSTRATIONS',
        physical_io=model['source_domain']=='PHYSICAL' and bool(rows),physical_success_claimed=model['source_domain']=='PHYSICAL' and success,
        target_physical_execution_authorized=False,target_model_trained=False,
        full_trace_retained=bool(rows),hardware_ack_not_dispatch_evidence=True)
    _write(folder/'result.json',canonical(result))
    return result
