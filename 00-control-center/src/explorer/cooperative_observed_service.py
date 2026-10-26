"""ENGINEERING_GLUE: metadata intent -> exact USER grants -> observed-test binding.

Fixed cached/status APIs only until explicit challenge/authorize/observe/revoke.
No native begin/resume/select/input/lease/action is called by this service.
Binding a test never attaches a transport or promotes shadow output to dispatch.
"""
from collections import OrderedDict
from copy import deepcopy
import hashlib
import re
import threading
import time
import uuid

from explorer_shared.contracts import digest
from explorer_shared.station_observed_http import StationObservedHttp
from explorer_shared.station_observed_activation import CONTRACT


class CooperativeObservedService:
    def __init__(self,workflow,requests,binary,*,inspect_native,has_prior_evidence,clock=time.monotonic_ns):
        self.workflow,self.requests,self.binary=workflow,requests,binary
        self.inspect_native,self.prior,self.clock=inspect_native,has_prior_evidence,clock
        self.lock=threading.RLock();self.setups={};self.challenges=OrderedDict();self.ready=OrderedDict()
        self.observers=OrderedDict();self.permissions=OrderedDict();self.source_cache={}
    def _context(self,row,spec):
        return {'session_id':row['session_id'],'epoch':row['epoch'],'process_session':row['process_session'],
            'skill_id':spec['skill_id'],'skill_version':spec['version'],'spec_sha256':spec['spec_sha256'],
            'object_id':row['protocol_object_id'],'input_modes':{role:row[role+'_input_mode'] for role in ('giver','receiver')},
            'references':deepcopy(spec['subskills'])}
    def _fresh(self,row,spec,*,intent=False):
        current=self.workflow.snapshot(row['session_id'])
        if (self.workflow.closed or current['epoch']!=row['epoch'] or current['process_session']!=self.workflow.process or
                current['spec_sha256']!=spec['spec_sha256'] or current['status'] in ('STOPPED','INTERRUPTED','COMPLETED')):
            raise ValueError('Current original cooperative intent/process/epoch required')
        if intent and (current['execution_domain'] not in ('observation_only','shadow') or current['protocol_state']!='PREPARE' or
                current.get('pending_operation') or current.get('recording_id') or current.get('last_recording_id') or
                current.get('dispatch_receipts') or current.get('protocol_trace') or self.prior(current)):
            raise ValueError('Create a fresh metadata intent; recorded/previous-shadow/started sessions cannot become dispatch authority')
        return current
    def receiver_binding(self,row,spec):
        ref=spec['subskills']['receiver'];result={'purpose':'cooperative05','skill_id':ref['skill_id'],
            'skill_version':ref['skill_version'],'spec_sha256':ref['spec_sha256'],'action_contract_id':CONTRACT,
            'source_domain':'PHYSICAL','input_mode':'policy' if row['receiver_input_mode']=='policy' else 'leader',
            'cooperative_session_id':row['session_id'],'coordinator_epoch':row['epoch'],'object_id':row['protocol_object_id']}
        if row['receiver_input_mode']=='policy':result.update(model_job_id=ref.get('model_id'),checkpoint_content_sha256=ref.get('checkpoint_sha256'))
        return result
    @staticmethod
    def giver_mode(row):return 'cooperative_policy' if row['giver_input_mode']=='policy' else row['giver_input_mode']
    def _refs(self,row,spec):
        blockers=[]
        for role,reference in spec['subskills'].items():
            try:
                state=self.inspect_native(role,reference)
                if (state.get('confirmed') is not True or state.get('skill_id')!=reference['skill_id'] or
                        state.get('skill_version')!=reference['skill_version'] or state.get('spec_sha256')!=reference['spec_sha256']):
                    raise ValueError('Original confirmed native SkillSpec changed')
                model=state.get('model') or {}
                if row[role+'_input_mode']=='policy' and (model.get('model_id')!=reference.get('model_id') or
                        model.get('checkpoint_sha256')!=reference.get('checkpoint_sha256')):
                    raise ValueError('Actual pinned native policy model required')
            except (ValueError,OSError,KeyError,TypeError) as error:blockers.append(role+':'+str(error))
        return blockers
    def _sources(self,row,spec):
        result={};blockers=[];began=self.clock()
        paths={'giver':'/legacy/explorer/retarget/status','receiver':'/station/observed-test/status'}
        for role in ('giver','receiver'):
            try:result[role]=self.requests[role]('GET',paths[role],None)
            except (ValueError,OSError,KeyError,TypeError) as error:
                result[role]={'source_api_error':str(error)};blockers.append(role+':CURRENT_OBSERVED_PERMISSION_SOURCE_UNAVAILABLE')
        if self.clock()-began>2_000_000_000:blockers.append('CURRENT_SOURCE_STATUS_RECEIVE_WINDOW_EXPIRED')
        return result,blockers
    def _current_grants(self,row,spec,sources,*,require_idle=False):
        giver=sources['giver'];activation=giver.get('activation') or {};receiver=sources['receiver'];grant=receiver.get('grant') or {}
        blockers=[]
        if (giver.get('execution_permission') is not True or activation.get('execution_permission') is not True or
                activation.get('input_mode')!=self.giver_mode(row) or not activation.get('authorization_id')):
            blockers.append('giver:EXACT_CURRENT_USER_OBSERVED_INPUT_PERMISSION_REQUIRED')
        else:
            with self.lock:
                same_intent=any(record['role']=='giver' and record['context']==self._context(row,spec) and
                    record['grants'].get('authorization_id')==activation['authorization_id'] for record in self.permissions.values())
            if not same_intent:blockers.append('giver:CURRENT_INTENT_EXPLICIT_USER_CONFIRMATION_REQUIRED')
        if (receiver.get('schema')!='explorer_station_observed_permission_status_v1' or receiver.get('execution_permission') is not True or
                grant.get('binding')!=self.receiver_binding(row,spec) or grant.get('execution_permission') is not True):
            blockers.append('receiver:EXACT_CURRENT_COOPERATIVE_PI_USER_GRANT_REQUIRED')
        if require_idle and (giver.get('active') is not False or giver.get('stop_latched') is not True or grant.get('native_mode')!='idle'):
            blockers.append('BIND_METADATA_TEST_BEFORE_EXPLICIT_NATIVE_BEGIN_RESUME')
        identities={'giver':{'authorization_id':activation.get('authorization_id'),'input_mode':activation.get('input_mode'),
                'actor_id':(activation.get('authorization_receipt') or {}).get('actor_id'),
                'control_epoch':giver.get('control_epoch'),'owner_generation':giver.get('owner_generation')},
            'receiver':{'grant_id':grant.get('grant_id'),'binding_sha256':grant.get('binding_sha256'),
                'provider_process':grant.get('provider_process'),'actor_id':grant.get('actor_id'),'control_epoch':grant.get('control_epoch')}}
        return identities,blockers
    def snapshot(self,row,spec,*,refresh=False):
        context=self._context(row,spec);context_sha=digest(context);blockers=self._refs(row,spec)
        try:self._fresh(row,spec,intent=not row.get('observed_test_binding'))
        except ValueError as error:blockers.append(str(error))
        if refresh:
            sources,missing=self._sources(row,spec)
            with self.lock:
                self.source_cache[context_sha]={'sources':deepcopy(sources),'missing':list(missing),'received_ns':self.clock()}
                while len(self.source_cache)>64:self.source_cache.pop(next(iter(self.source_cache)))
        else:
            with self.lock:cached=deepcopy(self.source_cache.get(context_sha))
            if cached and 0<=self.clock()-cached['received_ns']<=2_000_000_000:sources,missing=cached['sources'],cached['missing']
            else:sources,missing={'giver':{},'receiver':{}},['REFRESH_CURRENT_OBSERVED_SOURCE_STATUS']
        blockers.extend(missing)
        grants,grant_missing=self._current_grants(row,spec,sources,require_idle=not row.get('observed_test_binding'))
        choices={};permission_choices={}
        with self.lock:
            for role in ('giver','receiver'):
                identity=digest({'context':context_sha,'role':role})
                self.setups[identity]={'context':context,'row':deepcopy(row),'spec':deepcopy(spec),'role':role}
                while len(self.setups)>64:self.setups.pop(next(iter(self.setups)))
                choices[role]={'choice_id':identity,'role':role,'input_mode':row[role+'_input_mode'],
                    'label':'ROSMASTER · текущий наблюдаемый ввод' if role=='giver' else 'SO101 · текущий наблюдаемый ввод',
                    'challenge_supported':not blockers,'permission_current':not any(item.startswith(role+':') for item in grant_missing)}
                if not any(item.startswith(role+':') for item in grant_missing) and not missing:
                    permission_id=digest({'context':context_sha,'role':role,'grant':grants[role]})
                    self.permissions[permission_id]={'role':role,'context':context,'grants':deepcopy(grants[role]),
                        'binding':self.receiver_binding(row,spec) if role=='receiver' else None,
                        'spec':deepcopy(spec),'actor':grants[role]['actor_id']}
                    permission_choices[role]={'permission_choice_id':permission_id,'role':role,
                        'display_observation_supported':role=='receiver','metadata_revoke_supported':True}
            while len(self.permissions)>64:self.permissions.popitem(last=False)
            ready_id=None
            if not blockers and not grant_missing and not row.get('observed_test_binding'):
                fingerprint=digest({'context':context_sha,'grants':grants});ready_id=fingerprint
                self.ready[ready_id]={'context':context,'grants':grants,'created_ns':self.clock()}
                while len(self.ready)>32:self.ready.popitem(last=False)
        return {'schema':'explorer_cooperative_observed_setup_v1','metadata_intent_only':not bool(row.get('observed_test_binding')),
            'setup_choices':choices,'permission_choices':permission_choices,'bind_choice_id':ready_id,'bind_observed_test_available':ready_id is not None,
            'blockers':blockers+grant_missing,'source_permission_status':sources,'current_grant_identities':grants,
            'native_begin_resume_or_transport_attached':False,'explicit_user_observed_execution_current':False,
            'native_operator_lease_renewed':False,'compute_warm_is_authority':False}
    def _setup(self,choice_id,row,spec):
        self._fresh(row,spec,intent=not row.get('observed_test_binding'))
        with self.lock:selected=deepcopy(self.setups.get(choice_id))
        if not selected or selected['context']!=self._context(row,spec):raise ValueError('Current server-issued source setup choice required')
        reasons=self._refs(row,spec)
        if reasons:raise ValueError(';'.join(reasons))
        return selected['role']
    def challenge(self,choice_id,row,spec,*,actor):
        role=self._setup(choice_id,row,spec)
        if role=='giver':native=self.requests[role]('GET','/legacy/explorer/retarget/activation/challenge',None)
        else:native=StationObservedHttp(self.requests[role],self.receiver_binding(row,spec)).challenge()
        self._fresh(row,spec,intent=not row.get('observed_test_binding'))
        challenge=native.get('challenge_id');frame=native.get('frame') or {}
        if (not re.fullmatch('[0-9a-f]{32}',str(challenge)) or not re.fullmatch('[0-9a-f]{64}',str(frame.get('jpeg_sha256')))):
            raise ValueError('Exact native pinned camera challenge required')
        identity=uuid.uuid4().hex;record={'choice_id':identity,'role':role,'context':self._context(row,spec),'native':native,
            'created_ns':self.clock(),'actor':actor,'revoked':False}
        with self.lock:
            self.challenges[identity]=record
            while len(self.challenges)>32:self.challenges.popitem(last=False)
        return {'challenge_choice_id':identity,'role':role,'frame':deepcopy(frame),
            'frame_url':'/api/cooperative/observed-frame/'+identity,'required_confirmations':native.get('required_confirmations'),
            'native_control_started':False,'native_command_sent':False}
    def _challenge(self,identity,actor):
        with self.lock:record=deepcopy(self.challenges.get(identity))
        if (not record or record['revoked'] or record['actor']!=actor or not 0<=self.clock()-record['created_ns']<=2_000_000_000):
            raise ValueError('Current exact displayed native challenge for this operator required')
        context=record['context'];row=self.workflow.snapshot(context['session_id']);spec=self.workflow.skill(row['skill_id'],row['skill_version'])['spec']
        if context!=self._context(row,spec):raise ValueError('Displayed challenge belongs to an old cooperative session/epoch')
        self._fresh(row,spec,intent=not row.get('observed_test_binding'))
        return record,row,spec
    def frame(self,identity,*,actor):
        with self.lock:is_observer=identity in self.observers
        if is_observer:return self._observation_frame(identity,actor=actor)
        record,_,_=self._challenge(identity,actor);role=record['role'];challenge=record['native']['challenge_id']
        path='/native-activation/frame/'+challenge if role=='giver' else '/station/observed-test/challenges/'+challenge+'/frame'
        raw,headers=self.binary(role,path);lower={key.lower():value for key,value in headers.items()}
        if (not isinstance(raw,bytes) or len(raw)>8*1024**2 or hashlib.sha256(raw).hexdigest()!=record['native']['frame']['jpeg_sha256'] or
                lower.get('x-jpeg-sha256')!=record['native']['frame']['jpeg_sha256'] or
                lower.get('x-frame-id')!=record['native']['frame'].get('frame_id') or
                lower.get('x-boot-id')!=record['native']['frame'].get('boot_id')):raise ValueError('Exact displayed native JPEG bytes/SHA differ')
        self._challenge(identity,actor)
        with self.lock:self.challenges[identity]['frame_served_sha256']=hashlib.sha256(raw).hexdigest()
        return raw,headers

    def _permission(self,choice_id,row,spec,actor):
        self._fresh(row,spec)
        with self.lock:selected=deepcopy(self.permissions.get(choice_id))
        if not selected or selected['context']!=self._context(row,spec) or selected['actor']!=actor:
            raise ValueError('Exact current operator/native permission choice required')
        return selected

    def display_observation(self,choice_id,row,spec,*,actor):
        selected=self._permission(choice_id,row,spec,actor)
        if selected['role']!='receiver':raise ValueError('Only Pi common permission needs exact displayed-frame observation renewal')
        grant=selected['grants'];facade=StationObservedHttp(self.requests['receiver'],self.receiver_binding(row,spec))
        current=facade.check(grant['grant_id'],facade.binding,grant['control_epoch'])
        packet=self.requests['receiver']('GET','/station/observation',None);frame=packet.get('observation') or {}
        if (packet.get('schema')!='explorer_so101_cached_observation_v1' or
                not re.fullmatch('[0-9a-f]{32}',str(frame.get('observation_id'))) or
                not isinstance(frame.get('frame_id'),str) or not frame['frame_id'] or
                not re.fullmatch('[0-9a-f]{64}',str(frame.get('frame_sha256'))) or
                frame.get('frame_boot_id')!=current['boot_id'] or frame.get('frame_clock_domain')!=current['clock_domain'] or
                frame.get('follower_calibration_sha256')!=current['follower_calibration_sha256']):
            raise ValueError('Exact current native Pi cached JPEG/boot/calibration required')
        self._permission(choice_id,row,spec,actor)
        ident=uuid.uuid4().hex
        with self.lock:
            self.observers[ident]={'context':self._context(row,spec),'permission_choice_id':choice_id,'actor':actor,
                'frame':deepcopy(frame),'created_ns':self.clock(),'revoked':False}
            while len(self.observers)>32:self.observers.popitem(last=False)
        return {'observation_choice_id':ident,'role':'receiver','frame':deepcopy(frame),
            'frame_url':'/api/cooperative/observed-frame/'+ident,'native_operator_lease_renewed':False}

    def _observer(self,identity,actor):
        with self.lock:record=deepcopy(self.observers.get(identity))
        if not record or record['revoked'] or record['actor']!=actor or not 0<=self.clock()-record['created_ns']<=2_000_000_000:
            raise ValueError('Current exact displayed observation choice required')
        context=record['context'];row=self.workflow.snapshot(context['session_id']);spec=self.workflow.skill(row['skill_id'],row['skill_version'])['spec']
        if context!=self._context(row,spec):raise ValueError('Old observation cannot renew another cooperative epoch')
        selected=self._permission(record['permission_choice_id'],row,spec,actor)
        return record,row,spec,selected

    def _observation_frame(self,identity,*,actor):
        record,_,_,_=self._observer(identity,actor);frame=record['frame']
        raw,headers=self.binary('receiver','/station/observations/'+frame['observation_id']+'/frame')
        lower={key.lower():value for key,value in headers.items()}
        if (not isinstance(raw,bytes) or len(raw)>8*1024**2 or hashlib.sha256(raw).hexdigest()!=frame['frame_sha256'] or
                lower.get('x-jpeg-sha256')!=frame['frame_sha256'] or lower.get('x-frame-id')!=frame['frame_id'] or
                lower.get('x-boot-id')!=frame['frame_boot_id']):raise ValueError('Displayed native Pi observation bytes/SHA changed')
        self._observer(identity,actor)
        with self.lock:self.observers[identity]['frame_served_sha256']=hashlib.sha256(raw).hexdigest()
        return raw,headers

    def observe(self,identity,row,spec,body,*,actor):
        record,current,_,selected=self._observer(identity,actor)
        if current['session_id']!=row['session_id'] or record['context']!=self._context(row,spec):raise ValueError('Exact current session observation required')
        frame=record['frame'];checksum=body.get('displayed_jpeg_sha256')
        if checksum!=frame['frame_sha256'] or record.get('frame_served_sha256')!=checksum or body.get('observing') is not True:
            raise ValueError('Explicit current operator must view and confirm the exact fresh Pi source image')
        grant=selected['grants'];facade=StationObservedHttp(self.requests['receiver'],self.receiver_binding(row,spec))
        result=facade.observe(grant['grant_id'],{'observing':True,'frame_id':frame['frame_id'],
            'displayed_jpeg_sha256':checksum,'expected_control_epoch':grant['control_epoch']},authenticated_actor=actor)
        try:
            with self.workflow.lock:
                self._observer(identity,actor)
                with self.lock:self.observers[identity]['revoked']=True
        except ValueError:
            facade.revoke('LATE_COOPERATIVE_OBSERVATION',expected_grant_id=grant['grant_id'],expected_control_epoch=grant['control_epoch'])
            raise
        return {'role':'receiver','native_permission_receipt':result,'native_command_sent':False,'native_operator_lease_renewed':False}

    def _revoke(self,selected,reason):
        role=selected['role'];grant=selected['grants']
        if role=='giver':return self.requests[role]('POST','/legacy/explorer/retarget/activation/revoke',
            {'expected_authorization_id':grant['authorization_id']})
        return StationObservedHttp(self.requests[role],selected['binding']).revoke(reason,
            expected_grant_id=grant['grant_id'],expected_control_epoch=grant['control_epoch'])

    def revoke(self,choice_id,row,spec,*,actor):
        selected=self._permission(choice_id,row,spec,actor)
        with self.lock:
            for record in self.observers.values():
                if record['permission_choice_id']==choice_id:record['revoked']=True
        return self._revoke(selected,'EXPLICIT_COOPERATIVE_OPERATOR_REVOKE')

    def revoke_session(self,row,reason='COOPERATIVE_SESSION_STOPPED'):
        selected=[];seen=set()
        with self.lock:
            for record in list(self.challenges.values())+list(self.observers.values()):
                if record['context']['session_id']==row['session_id'] and record['context']['epoch']==row['epoch']:record['revoked']=True
            for record in self.permissions.values():
                key=(record['role'],record['grants'].get('authorization_id',record['grants'].get('grant_id')),record['grants'].get('control_epoch'))
                if record['context']['session_id']==row['session_id'] and record['context']['epoch']==row['epoch'] and key not in seen:
                    seen.add(key);selected.append(deepcopy(record))
        results=[]
        for record in selected:
            try:results.append({'role':record['role'],'result':self._revoke(record,reason)})
            except (ValueError,OSError,KeyError,TypeError) as error:results.append({'role':record['role'],'metadata_revoke_return_unknown':str(error)})
        return results
    def authorize(self,identity,row,spec,body,*,actor):
        record,current,current_spec=self._challenge(identity,actor)
        if current['session_id']!=row['session_id'] or self._context(row,spec)!=record['context']:raise ValueError('Exact current source challenge required')
        role=record['role'];native=record['native'];displayed=body.get('displayed_jpeg_sha256')
        if displayed!=native['frame']['jpeg_sha256']:raise ValueError('User must confirm the exact displayed source JPEG')
        if record.get('frame_served_sha256')!=displayed:raise ValueError('Load the exact source challenge image before USER confirmation')
        if role=='giver':
            request={key:body.get(key) for key in ('observing','neutral','space_clear','user_observed_test_confirmed')}
            request.update(challenge_id=native['challenge_id'],displayed_jpeg_sha256=displayed,actor_id=actor,
                input_mode=self.giver_mode(row),owner_chassis_test_authorized=False)
            result=self.requests[role]('POST','/legacy/explorer/retarget/activation/authorize',request)
        else:
            request={key:body.get(key) for key in ('observing','robot_workspace_checked','user_observed_test_confirmed')}
            request.update(challenge_id=native['challenge_id'],displayed_jpeg_sha256=displayed,expected_control_epoch=native['control_epoch'])
            result=StationObservedHttp(self.requests[role],self.receiver_binding(row,spec)).authorize(request,authenticated_actor=actor)
        permission_id=result.get('authorization_id' if role=='giver' else 'grant_id')
        if not isinstance(permission_id,str) or not permission_id or result.get('execution_permission') is not True:
            raise ValueError('Native API did not return an exact current USER permission; refresh source status')
        proof=(result.get('authorization_receipt') or {}) if role=='giver' else result
        if (proof.get('challenge_id')!=native['challenge_id'] or proof.get('actor_id')!=actor or
                role=='receiver' and result.get('binding')!=self.receiver_binding(row,spec)):
            if role=='giver':self.requests[role]('POST','/legacy/explorer/retarget/activation/revoke',{'expected_authorization_id':permission_id})
            else:StationObservedHttp(self.requests[role],self.receiver_binding(row,spec)).revoke('INVALID_COOPERATIVE_AUTHORIZATION_RECEIPT',
                expected_grant_id=permission_id,expected_control_epoch=result.get('control_epoch'))
            raise ValueError('Native USER receipt did not match the exact source challenge/operator/model')
        try:
            with self.workflow.lock:
                self._challenge(identity,actor)
                with self.lock:
                    self.challenges[identity]['revoked']=True
                    permission_choice=digest({'context':record['context'],'role':role,'permission':permission_id})
                    self.permissions[permission_choice]={'role':role,'context':record['context'],'actor':actor,'spec':deepcopy(spec),
                        'binding':self.receiver_binding(row,spec) if role=='receiver' else None,
                        'grants':{('authorization_id' if role=='giver' else 'grant_id'):permission_id,'control_epoch':result.get('control_epoch')}}
                    while len(self.permissions)>64:self.permissions.popitem(last=False)
        except ValueError:
            # Metadata-only CAS revokes only the just-returned old grant.
            if role=='giver':
                self.requests[role]('POST','/legacy/explorer/retarget/activation/revoke',{'expected_authorization_id':result.get('authorization_id')})
            else:
                StationObservedHttp(self.requests[role],self.receiver_binding(row,spec)).revoke('LATE_COOPERATIVE_AUTHORIZE',
                    expected_grant_id=result.get('grant_id'),expected_control_epoch=result.get('control_epoch'))
            raise
        return {'role':role,'native_permission_receipt':result,'native_command_sent':False,'transport_attached':False}
    def bind(self,choice_id,row,spec,*,actor):
        self._fresh(row,spec,intent=True)
        with self.lock:selected=deepcopy(self.ready.get(choice_id))
        if not selected or selected['context']!=self._context(row,spec):raise ValueError('Current server-issued observed-test binding choice required')
        if not 0<=self.clock()-selected['created_ns']<=2_000_000_000:raise ValueError('Observed binding choice expired; refresh both current sources')
        sources,blockers=self._sources(row,spec);grants,missing=self._current_grants(row,spec,sources,require_idle=True)
        if blockers or missing or grants!=selected['grants'] or any(value.get('actor_id')!=actor for value in grants.values()):
            raise ValueError('Both exact current USER grants/model/native sources required for this operator')
        with self.workflow.lock:
            current=self._fresh(row,spec,intent=True)
            current.update(execution_domain='user_observed_physical_test',observed_test_binding={
                'binding_id':uuid.uuid4().hex,'choice_id':choice_id,'actor_id':actor,'context':selected['context'],
                'native_grants':grants,'received_clock_domain':self.workflow.domain,'received_monotonic_ns':self.clock(),
                'metadata_binding_only':True,'native_operator_lease_renewed':False,'transports_attached':False})
            self.workflow._save(current)
        with self.lock:self.ready.pop(choice_id,None)
        return current
