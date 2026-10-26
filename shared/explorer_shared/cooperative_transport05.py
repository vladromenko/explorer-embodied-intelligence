"""ENGINEERING_GLUE: authenticated existing-owner policy/native receipt binding.

Instantiation never selects/enables/resumes/renews an operator lease. Root must
bind the user's separately observed native session and an actual native model
predictor. Neither plan/start acceptance nor target echo is a dispatch receipt.
"""
from copy import deepcopy
import time

from .cooperative_contract05 import digest,ROLE_CONTRACTS
from .handover_runtime import DispatchReceipt


class NativeGiverPolicyTransport:
    def __init__(self,request,binding,control_epoch,action_provider,*,clock_domain,
                 receiver_command=None,clock=time.monotonic_ns,sleep=time.sleep,timeout_s=1.):
        self.request,self.binding,self.epoch,self.predict=request,deepcopy(binding),control_epoch,action_provider
        self.domain,self.clock,self.sleep,self.timeout=clock_domain,clock,sleep,timeout_s
        self.receiver_command=receiver_command
        if not callable(request) or not callable(action_provider):raise ValueError('Authenticated request and actual native model callback required')
    def _body(self):return {**deepcopy(self.binding),'control_epoch':self.epoch}
    def _post(self,operation,body):return self.request('POST','/legacy/explorer/cooperative/'+operation,body)
    def observe_state(self):
        state=self.request('GET','/legacy/explorer/cooperative/status',None)
        native=state.get('native') or {};bound=(state.get('release_boundary') or {}).get('binding') or {}
        ready=(native.get('active') is True and native.get('state')=='ACTIVE' and native.get('native_owner')=='keyboard' and
            native.get('control_epoch')==self.epoch and native.get('native_lease_remaining_s',0)>0 and
            (state.get('release_boundary') or {}).get('reference_fault') is None and
            all(bound.get(key)==self.binding.get(key) for key in ('run_id','object_id','coordinator_epoch','authorization_id')))
        return {'current_native_owner_ready':ready,'source_receipt':deepcopy(state),
                'clock_domain':self.domain,'received_ns':self.clock(),'attained_stationarity':None}
    def arm_release_guard(self,session):
        self._session(session)
        return self._post('guard/arm',self._body())
    def _session(self,row):
        if (row.get('session_id')!=self.binding['run_id'] or row.get('protocol_object_id')!=self.binding['object_id'] or
                row.get('epoch')!=self.binding['coordinator_epoch']):raise ValueError('Old cooperative source session cannot operate native owner')
    def prepare_release(self,session,after_receiver_command_id):
        self._session(session);source=None
        if after_receiver_command_id is None:
            if not callable(self.receiver_command):raise ValueError('Actual receiver dispatch identity required before release challenge')
            source=self.receiver_command()
            if not isinstance(source,dict) or not source.get('actual_native_host_dispatch') or not source.get('source_receipt'):
                raise ValueError('Actual source-bound receiver command receipt required')
            after_receiver_command_id=source.get('command_id')
        challenge=self._post('guard/challenge',{**self._body(),'after_receiver_command_id':after_receiver_command_id})
        return {'challenge':challenge,'after_receiver_command_id':after_receiver_command_id,
            'received_coordinator_ns':self.clock(),'received_clock_domain':self.domain,
            'receiver_source_receipt':deepcopy(source),'cross_clock_conversion_assumed':False}
    def _authorize_release(self,authorization):
        if not isinstance(authorization,dict):raise ValueError('Explicit release authorization required')
        wrapper=authorization.get('native_release_challenge') or {};challenge=wrapper.get('challenge') or {}
        proof=(authorization.get('operator_confirmation_receipt') or {}).get('confirmation') or {}
        # All comparisons HERE are NX→NX; the original owner separately checks
        # its challenge's own native clock/boot/deadline at every opening write.
        if (wrapper.get('received_clock_domain')!=self.domain or proof.get('clock_domain')!=self.domain or
                type(wrapper.get('received_coordinator_ns')) is not int or type(proof.get('created_ns')) is not int or
                type(proof.get('received_ns')) is not int or
                not wrapper['received_coordinator_ns']<=proof['created_ns']<=proof['received_ns']<=self.clock() or
                proof.get('state')!='consumed' or proof.get('statement')!='receiver_holds_object' or
                proof.get('role')!='receiver' or proof.get('run_id')!=self.binding['run_id'] or
                proof.get('object_id')!=self.binding['object_id'] or
                proof.get('after_command_id')!=challenge.get('after_receiver_command_id')):
            raise ValueError('Consumed current operator confirmation must be created after this exact native challenge')
        return self._post('guard/authorize',{**self._body(),'challenge_id':challenge.get('challenge_id'),
            'operator_confirmation':deepcopy(proof),'coordinator_verified_confirmation_created_after_challenge':True})
    def submit_observed(self,command,authorization=None):
        if (command.get('run_id')!=self.binding['run_id'] or command.get('object_id')!=self.binding['object_id'] or
                command.get('coordinator_epoch')!=self.binding['coordinator_epoch'] or command.get('role')!='giver'):
            raise ValueError('Current native giver command correlation required')
        if not self.observe_state()['current_native_owner_ready']:raise ValueError('Current native source owner not ready')
        predicted=self.predict(deepcopy(command))
        if (not isinstance(predicted,dict) or predicted.get('action_contract')!=ROLE_CONTRACTS['giver'] or
                predicted.get('model',{}).get('model_id')!=command['reference'].get('model_id') or
                predicted.get('model',{}).get('checkpoint_sha256')!=command['reference'].get('checkpoint_sha256') or
                predicted.get('model',{}).get('spec_sha256')!=command['reference'].get('spec_sha256')):
            raise ValueError('Actual pinned native9 model predictor required')
        release_grant=self._authorize_release(authorization) if command.get('operation')=='release' else None
        page=self.request('GET','/legacy/explorer/retarget/dispatch?after_sequence=0',None)
        cursor=page.get('available_latest_sequence')
        if (page.get('schema')!='explorer_native_dispatch_page_v1' or not page.get('boot_id') or not page.get('clock_domain') or
                type(cursor) is not int or cursor<0):raise ValueError('Original native dispatch cursor not available')
        native_boot,native_domain=page['boot_id'],page['clock_domain']
        body={**self._body(),'command_id':command['command_id'],'source_sequence':predicted['source_sequence'],
            'action9':predicted['action9'],'source_observation':deepcopy(predicted['source_observation']),
            'model':deepcopy(predicted['model'])}
        accepted=self._post('input',body)
        if accepted.get('accepted') is not True:raise ValueError('Native latest-intent API rejected prediction')
        deadline=self.clock()+int(self.timeout*1e9)
        while self.clock()<deadline:
            page=self.request('GET','/legacy/explorer/retarget/dispatch?after_sequence='+str(cursor),None)
            if (page.get('schema')!='explorer_native_dispatch_page_v1' or page.get('boot_id')!=native_boot or
                    page.get('clock_domain')!=native_domain or page.get('gap') is not False):
                raise ValueError('Native dispatch receipt gap/clock/boot changed; no fabricated success')
            for receipt in page.get('receipts',[]):
                source=((receipt.get('source_intent_binding') or {}).get('source') or {})
                if source.get('command_id')==command['command_id']:
                    if (receipt.get('kind')!='arm_host_publish' or source.get('cooperative',{}).get('run_id')!=command['run_id'] or
                            source.get('cooperative',{}).get('object_id')!=command['object_id'] or
                            source.get('cooperative',{}).get('coordinator_epoch')!=command['coordinator_epoch'] or
                            source.get('cooperative',{}).get('native_control_epoch')!=self.epoch or
                            source.get('policy')!=predicted['model'] or source.get('source_target_sha256')!=digest(predicted['action9']) or
                            source.get('state_source')!='policy_prediction_not_measured_leader'):
                        raise ValueError('Native publication source identity mismatch')
                    if release_grant is not None:
                        proof=(receipt.get('original_payload') or {}).get('cooperative_publication_boundary') or {}
                        if not proof.get('opening') or (proof.get('guard_grant') or {}).get('grant_id')!=release_grant.get('grant_id'):
                            raise ValueError('Actual opening publication with this exact receiver-hold grant required')
                    return DispatchReceipt(command['command_id'],command['run_id'],command['object_id'],'giver',True,
                        self.clock(),self.domain,{'basis':'actual_original_native_host_publication','native_receipt':deepcopy(receipt),
                            'api_accepted':deepcopy(accepted),'MCU_dispatch_verified':False,'attainment_verified':False})
            next_sequence=page.get('next_sequence')
            if type(next_sequence) is not int or next_sequence<cursor:raise ValueError('Original native dispatch cursor moved backwards')
            cursor=next_sequence;self.sleep(.01)
        raise TimeoutError('Native API accepted but no correlated host publication was observed')
    def stop(self,run_id):
        if run_id!=self.binding['run_id']:raise ValueError('STOP belongs to another cooperative run')
        return self._post('stop',self._body())
