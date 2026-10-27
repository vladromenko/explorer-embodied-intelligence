"""ENGINEERING_GLUE: separate explicit USER native controls; no auto authority.

The constructor and cached snapshot never command devices. Refresh is GET-only.
Choice IDs pin current native identities; begin/resume/attach are independent
authenticated USER actions. Models and camera polling never renew native leases.
"""
from collections import OrderedDict
from copy import deepcopy
import threading
import uuid

from explorer_shared.cooperative_contract05 import digest
from explorer_shared.cooperative_action_constraint05 import stationary_constraint
from explorer_shared.cooperative_transport05 import NativeGiverPolicyTransport,NativeGiverManualTransport
from explorer_shared.cooperative_receiver_transport05 import NativeReceiverTransport
from explorer_shared.station_observed_http import StationObservedHttp

OPERATIONS=('native_begin','native_recenter','native_resume','native_attach','native_lease','native_stop','native_reset_safe')


class CooperativeNativeSessions:
    def __init__(self,service):
        self.service=service;self.workflow=service.workflow;self.clock=service.clock
        self.lock=threading.RLock();self.choices=OrderedDict();self.cache={};self.pending=set();self.owned=OrderedDict();self.cancelled=set()
    def _fresh(self,row,spec,actor=None):
        current=self.workflow.snapshot(row['session_id']);bound=current.get('observed_test_binding') or {}
        with self.lock:cancelled=(row['session_id'],row['epoch']) in self.cancelled
        context=self.service.observed_setup._context(current,spec)
        if (cancelled or self.service.closed or self.workflow.closed or current['epoch']!=row['epoch'] or
                current['process_session']!=self.workflow.process or current['execution_domain']!='user_observed_physical_test' or
                current['skill_id']!=spec['skill_id'] or current['skill_version']!=spec['version'] or current['spec_sha256']!=spec['spec_sha256'] or
                digest({key:value for key,value in spec.items() if key!='spec_sha256'})!=spec['spec_sha256'] or
                current['status'] in ('STOPPED','INTERRUPTED','COMPLETED') or bound.get('context')!=context or
                actor is not None and bound.get('actor_id')!=actor):
            raise ValueError('Fresh exact USER-bound cooperative intent/process/epoch required')
        return current
    def _source(self,role):
        request=self.service._request(role);began=self.clock()
        if role=='giver':
            source=request('GET','/legacy/explorer/cooperative/status',None)
            permission=request('GET','/legacy/explorer/retarget/activation/status',None)
        else:
            permission=request('GET','/station/observed-test/status',None);source=request('GET','/station/status',None)
        if not 0<=self.clock()-began<=2_000_000_000:raise ValueError('Current native source receive window expired')
        return {'source':source,'permission':permission}
    def _check_permission(self,role,row,spec,pair):
        bound=row['observed_test_binding'];expected=bound['native_grants'][role];permission=pair['permission']
        if role=='giver':
            proof=permission.get('authorization_receipt') or {}
            if (pair['source'].get('schema')!='explorer_native_cooperative_owner_v1' or
                    permission.get('execution_permission') is not True or permission.get('authorization_id')!=expected['authorization_id'] or
                    permission.get('input_mode')!=self.service.observed_setup.giver_mode(row) or proof.get('actor_id')!=bound['actor_id']):
                raise ValueError('Exact current source-bound USER giver permission required')
        else:
            grant=permission.get('grant') or {}
            if (permission.get('execution_permission') is not True or grant.get('execution_permission') is not True or
                    grant.get('grant_id')!=expected['grant_id'] or grant.get('provider_process')!=expected['provider_process'] or
                    grant.get('actor_id')!=bound['actor_id'] or grant.get('binding')!=self.service.observed_setup.receiver_binding(row,spec) or
                    pair['source'].get('control_epoch')!=grant.get('control_epoch') or pair['source'].get('mode')!=grant.get('native_mode')):
                raise ValueError('Exact current source-bound USER Pi permission/epoch required')
    @staticmethod
    def _identity(role,pair):
        source,permission=pair['source'],pair['permission']
        if role=='giver':
            native=source.get('native') or {};boundary=source.get('release_boundary') or {}
            return {'authorization_id':permission.get('authorization_id'),'input_mode':permission.get('input_mode'),
                'native':{key:native.get(key) for key in ('control_epoch','owner_generation','native_owner','active','state','stop_latched')},
                'binding':deepcopy(boundary.get('binding')),'armed':boundary.get('armed'),
                'constraint':deepcopy(source.get('registered_action_constraint')),
                'persistence':deepcopy(boundary.get('persistence')),'quiescence':deepcopy(boundary.get('original_quiescence')),
                'reference_fault':deepcopy(boundary.get('reference_fault'))}
        grant=permission.get('grant') or {}
        return {'grant':{key:deepcopy(grant.get(key)) for key in ('grant_id','binding','provider_process','control_epoch','native_mode','actor_id')},
            'native':{key:source.get(key) for key in ('available','mode','control_epoch','configuration_writes')}}
    def _binding(self,row,pair):
        return {'run_id':row['session_id'],'object_id':row['protocol_object_id'],'coordinator_epoch':row['epoch'],
            'authorization_id':pair['permission']['authorization_id'],'observing':True}
    @staticmethod
    def _same_binding(source,binding):
        actual=(source.get('release_boundary') or {}).get('binding') or {}
        return all(actual.get(key)==binding[key] for key in ('run_id','object_id','coordinator_epoch','authorization_id'))
    def _ready_prediction(self,row,spec,role):
        ready=[item for item in self.service.warm_compute.snapshots(row) if item['compute_ready'] and role in item['required_policy_roles']]
        if len(ready)!=1:raise ValueError('One exact admitted current warm worker for this policy role required')
        return self.service.warm_compute.prediction_adapter(ready[0]['job_id'],row,role)
    def refresh(self,row,spec,*,actor):
        row=self._fresh(row,spec,actor);roles={};context=self.service.observed_setup._context(row,spec)
        for role in ('giver','receiver'):
            operations={name:{'implemented':False,'available':False,'choice_id':None} for name in OPERATIONS};reason=None
            try:
                pair=self._source(role);self._check_permission(role,row,spec,pair)
                source=pair['source'];mode=row[role+'_input_mode'];policy=mode=='policy'
                if role=='giver':
                    caps=source.get('capabilities') or {};native=source.get('native') or {};bound=source.get('release_boundary') or {}
                    supported=caps.get('explicit_user_prepare') is True
                    same=self._same_binding(source,self._binding(row,pair));active=native.get('active') is True
                    stopped=native.get('stop_latched') is True;live=native.get('native_lease_remaining_s',0)>0
                    operations['native_begin'].update(implemented=supported,available=supported and not active and stopped and not bound.get('armed'))
                    operations['native_resume'].update(implemented=supported and (policy or caps.get('explicit_user_manual_resume') is True),
                        available=supported and same and ((policy and active and native.get('state')=='CLUTCHED') or (not policy and not active and stopped)))
                    operations['native_recenter'].update(implemented=policy and supported,available=policy and supported and same and active)
                    operations['native_attach'].update(implemented=supported,available=supported and same and live and not stopped and
                        (active and native.get('state')=='ACTIVE' if policy else not active))
                    operations['native_lease'].update(implemented=policy,available=policy and same and active and native.get('state')=='ACTIVE')
                    operations['native_stop'].update(implemented=supported,available=supported and same)
                    persistence=bound.get('persistence') or {};drained=(bound.get('original_quiescence') or {}).get('ready') is True
                    reset_installed=persistence.get('installed') is True and isinstance(persistence.get('state_id'),str) and type(persistence.get('revision')) is int
                    operations['native_reset_safe'].update(implemented=reset_installed,
                        available=reset_installed and stopped and not active and drained and persistence.get('pending') is False,
                        reason=None if drained else 'ORIGINAL_COMMAND_STREAM_MUST_DRAIN_BEFORE_SAFE_RESET')
                    if bound.get('reference_fault') is not None:
                        for name in ('native_begin','native_resume','native_recenter','native_attach'):
                            operations[name].update(available=False,reason='NEW_USER_OBSERVED_ORIGINAL_REFERENCE_REQUIRED')

                    if mode=='so101_leader':
                        for name,item in operations.items():
                            if name!='native_reset_safe':item.update(implemented=False,available=False,reason='USE_EXISTING_DIRECT_PI_LEADER_SESSION_NO_SECOND_OWNER')
                else:
                    native=pair['source'];grant=pair['permission']['grant']
                    guarded=pair['permission'].get('native_publication_permission_gate_installed') is True
                    interlock=pair['permission'].get('cooperative_hold_interlock') or {}
                    parked=(interlock.get('armed') is True or interlock.get('parked_observation_only') is True or
                        interlock.get('all_target_writes_blocked') is True or interlock.get('persistence_fault') is not None)
                    operations['native_begin'].update(implemented=True,available=guarded and not parked and native.get('mode')=='idle',
                        reason='EXPLICIT_HELD_PHASE_TRANSITION_OR_SAFE_OBJECT_RESET_REQUIRED' if parked else
                            None if guarded else 'NATIVE_USER_PERMISSION_REQUIRED_AT_EVERY_ORIGINAL_WRITE')
                    operations['native_attach'].update(implemented=True,available=guarded and native.get('mode')==('policy' if policy else 'teleoperation'),
                        reason=None if guarded else 'NATIVE_USER_PERMISSION_REQUIRED_AT_EVERY_ORIGINAL_WRITE')
                    operations['native_stop'].update(implemented=True,available=True)
                for name in ('native_begin','native_attach'):
                    if policy and operations[name]['available']:
                        try:self._ready_prediction(row,spec,role)
                        except (ValueError,OSError,KeyError,TypeError) as error:operations[name].update(available=False,reason=str(error))
                with self.lock:
                    for name,item in operations.items():
                        if item['available']:
                            choice=digest({'context':context,'role':role,'operation':name,'native':self._identity(role,pair),'nonce':uuid.uuid4().hex})
                            self.choices[choice]={'context':deepcopy(context),'role':role,'operation':name,
                                'identity':self._identity(role,pair),'created_ns':self.clock(),'used':False}
                            item['choice_id']=choice
                    while len(self.choices)>128:self.choices.popitem(last=False)
            except Exception as error:reason=type(error).__name__+':'+str(error)
            roles[role]={'input_mode':row[role+'_input_mode'],'operations':operations,'reason':reason,
                'no_automatic_user_lease_renewal':True,'policy_inference_is_not_execution_authority':True}
        result={'schema':'explorer_cooperative_native_controls_v1','roles':roles,'current_received_ns':self.clock(),
            'native_controls_started_by_snapshot':False,'native_operations_are_separate_USER_actions':True}
        with self.lock:
            if (row['session_id'],row['epoch']) in self.cancelled:raise ValueError('Late native controls refresh after cooperative STOP')
            self.cache[row['session_id']]=(row['epoch'],deepcopy(result))
            while len(self.cache)>64:self.cache.pop(next(iter(self.cache)))
        return result
    def snapshot(self,row):
        with self.lock:cached=deepcopy(self.cache.get(row['session_id']))
        if not cached or cached[0]!=row['epoch'] or not 0<=self.clock()-cached[1]['current_received_ns']<=2_000_000_000:
            return {'schema':'explorer_cooperative_native_controls_v1','roles':{},'reason':'EXPLICIT_NATIVE_REFRESH_REQUIRED',
                'native_controls_started_by_snapshot':False}
        return cached[1]
    def _choose(self,operation,row,spec,body,actor):
        allowed={'session_id','expected_epoch','role','native_control_choice_id','observing','user_observed_test_confirmed','neutral'}
        if operation=='native_reset_safe':allowed.add('object_safe_no_dual_hold')
        if not isinstance(body,dict) or set(body)-allowed:raise ValueError('Only fixed native USER IDs and confirmation fields required')
        if operation=='native_reset_safe' and (body.get('role')!='giver' or body.get('object_safe_no_dual_hold') is not True):
            raise ValueError('Separate giver safe-object confirmation required; no native movement is implied')
        current=self._fresh(row,spec,actor);role=body.get('role')
        if current.get('pending_operation') and operation not in ('native_lease','native_stop'):
            raise ValueError('Finish/cancel current protocol step before changing native control binding')
        if (role not in ('giver','receiver') or body.get('observing') is not True or body.get('user_observed_test_confirmed') is not True or
                operation in ('native_begin','native_recenter') and body.get('neutral') is not True):raise ValueError('Separate explicit USER current role/observation/test/neutral confirmation required')
        with self.lock:
            choice=self.choices.get(body.get('native_control_choice_id'))
            if (choice is None or choice['used'] or choice['role']!=role or choice['operation']!=operation or
                    choice['context']!=self.service.observed_setup._context(row,spec) or
                    not 0<=self.clock()-choice['created_ns']<=2_000_000_000 or (row['session_id'],role) in self.pending):
                raise ValueError('Fresh unconsumed server native control choice required')
            choice['used']=True;self.pending.add((row['session_id'],role));selected=deepcopy(choice)
        return selected
    def _save(self,row,spec,role,operation,result,actor):
        with self.workflow.lock:
            current=self._fresh(row,spec,actor)
            current.setdefault('native_user_control_receipts',[]).append({'role':role,'operation':operation,'source_response':deepcopy(result),
                'received_nx_ns':self.clock(),'received_clock_domain':self.workflow.domain,'actor_id':actor,
                'basis':'parsed_authenticated_source_reply_not_hardware_attainment'})
            current['native_user_control_receipts']=current['native_user_control_receipts'][-64:];self.workflow._save(current)
    def _remember_owned(self,row,spec,role,operation,pair,result,actor):
        key=(row['session_id'],row['epoch'],role)
        with self.lock:
            if operation=='native_stop':self.owned.pop(key,None)
            elif operation in ('native_begin','native_resume','native_recenter','native_attach'):
                self.owned[key]={'row':deepcopy(row),'spec':deepcopy(spec),'role':role,'operation':operation,
                    'pair':deepcopy(pair),'result':deepcopy(result),'actor':actor}
                self.owned.move_to_end(key)
                while len(self.owned)>128:self.owned.popitem(last=False)
            cancelled=key[:2] in self.cancelled
        if cancelled:raise ValueError('Native USER operation returned after this cooperative STOP')
    def cancel_owned_sessions(self,row,*,actor=None,reason='EXPLICIT_COOPERATIVE_STOP'):
        """Revoke local choices first, then exact returned native owners.

        Pass the saved pre-STOP row, including when workflow already cancelled
        its epoch. No current source epoch is adopted: the cached authenticated
        reply is the STOP CAS basis.
        Source boundary rejects newer grants/generations; armed latches persist.
        """
        bound=row.get('observed_test_binding') or {};owner=bound.get('actor_id')
        if (row.get('process_session')!=self.workflow.process or actor is not None and owner is not None and actor!=owner):
            raise ValueError('Exact owned cooperative process/operator required for native cancellation')
        context=(row['session_id'],row['epoch'])
        with self.lock:
            self.cancelled.add(context);self.cache.pop(row['session_id'],None)
            for choice in self.choices.values():
                if (choice['context']['session_id'],choice['context']['epoch'])==context:choice['used']=True
            records={key[2]:deepcopy(value) for key,value in self.owned.items() if key[:2]==context}
        results={}
        for role in ('giver','receiver'):
            record=records.get(role)
            if record is None:results[role]={'known_owned_native_session':False,'source_stop_requested':False}
            elif record['actor']!=owner:results[role]={'known_owned_native_session':False,'source_stop_requested':False,'reason':'OWNED_ACTOR_CHANGED'}
            else:
                try:
                    response=self._cleanup(role,record['row'],record['spec'],record['pair'],record['result'],record['operation'])
                    results[role]={'known_owned_native_session':True,'source_cleanup_receipt':deepcopy(response),
                        'hardware_attainment_or_object_safety_claimed':False}
                except Exception as error:
                    results[role]={'known_owned_native_session':True,'delivery_unknown':True,'error':type(error).__name__+':'+str(error),
                        'requested_only_exact_returned_source_scope':True}
        return {'session_id':row['session_id'],'coordinator_epoch':row['epoch'],'reason':str(reason)[:160],'roles':results,
            'native_lease_automatically_renewed':False,'dual_hold_interlocks_unarmed':False}
    def command(self,operation,row,spec,body,*,actor):
        selected=self._choose(operation,row,spec,body,actor);role=selected['role'];pair=None;result={};known_reply=False
        request=self.service._request(role)
        try:
            pair=self._source(role);self._check_permission(role,row,spec,pair)
            if self._identity(role,pair)!=selected['identity']:raise ValueError('Original native owner changed after USER choice')
            self._fresh(row,spec,actor)
            if role=='receiver':
                grant=pair['permission']['grant'];facade=StationObservedHttp(request,grant['binding'])
                if operation in ('native_begin','native_attach') and pair['permission'].get('native_publication_permission_gate_installed') is not True:
                    raise ValueError('USER observation permission must be checked at every original native write')
                if operation=='native_begin':result=facade.command(grant['grant_id'],grant['binding'],
                    'policy/start' if row['receiver_input_mode']=='policy' else 'teleop/start',{'expected_control_epoch':grant['control_epoch']})
                elif operation=='native_stop':
                    result=facade.command(grant['grant_id'],grant['binding'],'stop',{'expected_control_epoch':grant['control_epoch']})
                elif operation=='native_attach':result=self._attach(role,row,spec,pair,actor)
                else:raise ValueError('Pi has no invented resume/lease/reset operation; use original start/current observation')
            else:
                binding=self._binding(row,pair);source=pair['source'];native=source['native'];mode=row['giver_input_mode']
                def post(name,value):
                    self._fresh(row,spec,actor)
                    return request('POST','/legacy/explorer/cooperative/'+name,value)
                if operation=='native_begin':
                    result={'prepare':post('prepare',{**binding,'neutral':True})}
                    result['bind']=post('bind',binding)
                    if mode=='policy':
                        model={key:spec['subskills']['giver'][key] for key in ('model_id','checkpoint_sha256','spec_sha256')}
                        result['constraint']=post('constraint/register',{**binding,'constraint':stationary_constraint(spec,row,model)})
                        result['begin']=post('begin',{**binding,'neutral':True,'model':model})
                    elif mode=='so101_leader':raise ValueError('SO101 giver requires the existing direct Pi leader session; no second measured-leader owner is created')
                elif operation=='native_resume':result=post('resume' if mode=='policy' else 'manual/resume',
                    {**binding,'control_epoch':native['control_epoch'],'expected_native_generation':native['owner_generation']})
                elif operation=='native_recenter':result=post('recenter',{**binding,'control_epoch':native['control_epoch']})
                elif operation=='native_lease':result=post('lease',{**binding,'control_epoch':native['control_epoch']})
                elif operation=='native_attach':result=self._attach(role,row,spec,pair,actor)
                elif operation=='native_reset_safe':
                    persistence=(source.get('release_boundary') or {}).get('persistence') or {}
                    result=post('guard/reset',{'authorization_id':pair['permission']['authorization_id'],'observing':True,
                        'object_safe_no_dual_hold':True,'expected_state_id':persistence['state_id'],'expected_revision':persistence['revision']})
                elif operation=='native_stop':result=post('stop',{**binding,'control_epoch':native['control_epoch'] if mode=='policy' else None,
                    'expected_native_generation':native['owner_generation']})
                else:raise ValueError('Known exact native USER operation required')
            known_reply=operation in ('native_recenter','native_resume','native_attach') or operation=='native_begin' and (role=='receiver' or 'bind' in result)
            self._remember_owned(row,spec,role,operation,pair,result,actor)
            self._save(row,spec,role,operation,result,actor)
            controls=self.refresh(row,spec,actor=actor)
            return {'role':role,'operation':operation,'native_receipt':result,'automatic_lease_renewal':False,
                'native_controls':controls,'API_result_claims_hardware_attainment':False,
                **({'native_command_sent':False,'native_lease_or_user_observation_renewed':False} if operation=='native_reset_safe' else {})}
        except Exception:
            if pair is not None and known_reply:
                try:self._cleanup(role,row,spec,pair,result,operation)
                except Exception:pass  # exact cleanup delivery unknown; original exception remains, no automatic replay.
            raise
        finally:
            with self.lock:self.pending.discard((row['session_id'],role))
    def _cleanup(self,role,row,spec,pair,result,operation):
        request=self.service._request(role)
        if role=='receiver':
            initial=pair['permission']['grant']
            try:current=request('GET','/station/observed-test/status',None).get('grant') or {}
            except Exception:current={}  # Source STOP still enforces exact cached grant/epoch CAS.
            if current and (current.get('grant_id')!=initial['grant_id'] or current.get('binding')!=initial['binding'] or
                    current.get('provider_process')!=initial['provider_process']):return {'new_native_grant_not_owned':True}
            grant=result.get('grant') or initial
            if current and current.get('control_epoch')!=grant['control_epoch']:return {'new_native_epoch_not_owned':True}
            facade=StationObservedHttp(request,grant['binding'])
            return facade.command(grant['grant_id'],grant['binding'],'stop',{'expected_control_epoch':grant['control_epoch']})
        binding=self._binding(row,pair)
        activation=request('GET','/legacy/explorer/retarget/activation/status',None)
        if activation.get('authorization_id') not in (None,binding['authorization_id']):return {'new_native_authorization_not_owned':True}
        source=request('GET','/legacy/explorer/cooperative/status',None)
        bound=(source.get('release_boundary') or {}).get('binding') or {};native=source.get('native') or {}
        expected=(pair['source']['native'] if operation=='native_attach' else result.get('begin',result.get('native',
            (result.get('prepare') or {}).get('native',result))))
        expected=expected.get('native') or expected
        if any(bound.get(key)!=binding[key] for key in ('run_id','object_id','coordinator_epoch','authorization_id')):
            return {'new_native_binding_not_owned':True}
        if (native.get('owner_generation')!=expected.get('owner_generation') or native.get('control_epoch')!=expected.get('control_epoch') or
                native.get('active') is not expected.get('active')):return {'new_native_epoch_or_generation_not_owned':True}
        return request('POST','/legacy/explorer/cooperative/stop',{**binding,'control_epoch':expected.get('control_epoch') if expected.get('active') is True else None,
            'expected_native_generation':expected.get('owner_generation')})
    def _attach(self,role,row,spec,pair,actor):
        reference=spec['subskills'][role];policy=row[role+'_input_mode']=='policy';adapter=self._ready_prediction(row,spec,role) if policy else None
        def predict(command):return adapter(deepcopy(reference),dispatch=False,command=deepcopy(command))
        if role=='receiver':
            grant=pair['permission']['grant']
            transport=NativeReceiverTransport(self.service._request(role),{'run_id':row['session_id'],'object_id':row['protocol_object_id'],
                'coordinator_epoch':row['epoch']},grant['binding'],grant,reference,predict if policy else None,clock_domain=self.workflow.domain,clock=self.clock,
                dual_hold_integration_ready=True)
        else:
            binding=self._binding(row,pair);native=pair['source']['native']
            if policy:
                model={key:reference[key] for key in ('model_id','checkpoint_sha256','spec_sha256')}
                transport=NativeGiverPolicyTransport(self.service._request(role),binding,native['control_epoch'],predict,clock_domain=self.workflow.domain,
                    clock=self.clock,receiver_command=self.service._receiver_command,action_constraint=stationary_constraint(spec,row,model))
            else:transport=NativeGiverManualTransport(self.service._request(role),binding,native['owner_generation'],clock_domain=self.workflow.domain,
                clock=self.clock,receiver_command=self.service._receiver_command)
        state=transport.observe_state()
        if not state['current_native_owner_ready']:raise ValueError('Exact already running native USER owner must be current before attachment')
        try:
            with self.workflow.lock:
                self._fresh(row,spec,actor)
                with self.service.lock:
                    bound=self.service.native_bindings.setdefault(row['session_id'],{'epoch':row['epoch'],'actor':actor,'roles':{}})
                    if bound['epoch']!=row['epoch']:raise ValueError('Old native binding epoch cannot be reused')
                    bound.setdefault('roles',{})[role]=deepcopy(state)
                    self.service.transports[(row['session_id'],role)]=transport
                    if adapter is not None:self.service.models[role]=adapter
        except Exception:
            transport.stop(row['session_id']);raise
        return {'attached':True,'current_native_owner':state,'native_control_started_by_attach':False,
            'model_required':policy,'model_callback_instance_pinned':policy}
