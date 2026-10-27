"""ENGINEERING_GLUE: explicit metadata-only receiver setup with opaque choices.

No native begin/action/STOP/lease, camera owner, model, numeric browser endpoint
or implicit observation renewal. Cached snapshot makes no RPC. Source mutations
use the existing COMMON authenticated metadata facade and current USER grant.
"""
from collections import OrderedDict
from copy import deepcopy
import re
import threading
import uuid

from explorer_shared.contracts import digest
from explorer_shared.station_observed_http import StationObservedHttp

OPERATIONS=('bind_guard','capture_empty_open','capture_empty_closed','reset_safe')
SCHEMA='explorer_cooperative_receiver_setup_v1'
TTL_NS=2_000_000_000  # Existing server USER-choice lifetime, not a motor lease.


class CooperativeReceiverSetup:
    def __init__(self,service):
        self.service,self.workflow,self.observed=service,service.workflow,service.observed_setup
        self.clock=service.clock;self.lock=threading.RLock();self.choices=OrderedDict();self.cache={};self.pending=set();self.generations={}
    def _fresh(self,row,spec,actor):
        current=self.observed._fresh(row,spec,intent=not row.get('observed_test_binding'))
        if (self.service.closed or current.get('skill_id')!=spec.get('skill_id') or current.get('skill_version')!=spec.get('version') or
                digest({key:value for key,value in spec.items() if key!='spec_sha256'})!=spec.get('spec_sha256') or
                not isinstance(actor,str) or not actor or current.get('pending_operation') or
                current.get('observed_test_binding') and current['observed_test_binding'].get('actor_id')!=actor):
            raise ValueError('Exact current operator/cooperative Spec/epoch with no pending step required')
        return current
    def _source(self,row,spec,actor):
        self._fresh(row,spec,actor);request=self.service._request('receiver')
        facade=StationObservedHttp(request,self.observed.receiver_binding(row,spec));began=self.clock()
        permission=facade.status();native=request('GET','/station/status',None);source=facade.cooperative('status')
        grant=permission.get('grant') or {};follower=(native.get('roles') or {}).get('follower') or {}
        if (not 0<=self.clock()-began<=TTL_NS or permission.get('execution_permission') is not True or
                permission.get('native_publication_permission_gate_installed') is not True or grant.get('execution_permission') is not True or
                grant.get('binding')!=facade.binding or grant.get('actor_id')!=actor or not grant.get('grant_id') or
                not grant.get('provider_process') or not grant.get('boot_id') or not grant.get('clock_domain') or
                native.get('available') is not True or native.get('configuration_writes')!=0 or
                native.get('control_epoch')!=grant.get('control_epoch') or native.get('mode')!=grant.get('native_mode') or
                follower.get('verified_on_connected_hardware') is not True or follower.get('calibration_sha256')!=grant.get('follower_calibration_sha256') or
                source.get('schema')!='explorer_cooperative_receiver_interlock_v1'):
            raise ValueError('Exact current source-bound USER Pi grant/native epoch/calibration required')
        self._fresh(row,spec,actor)
        return facade,grant,native,source
    @staticmethod
    def _identity(grant,native,source):
        return {'grant':{key:deepcopy(grant.get(key)) for key in ('grant_id','binding','provider_process','actor_id','control_epoch',
                    'native_mode','boot_id','clock_domain','follower_calibration_sha256')},
            'native_mode':native.get('mode'),'interlock':{key:deepcopy(source.get(key)) for key in
                ('interlock_id','revision','armed','persistence_fault','all_target_writes_blocked')}}
    def _mint(self,operation,row,spec,actor,identity,profile=None):
        context=self.observed._context(row,spec);choice=digest({'context':context,'operation':operation,'actor':actor,'nonce':uuid.uuid4().hex})
        self.choices[choice]={'context':context,'operation':operation,'actor':actor,'identity':deepcopy(identity),'profile':deepcopy(profile),
            'created_ns':self.clock(),'used':False,'generation':self.generations.get(row['session_id'],0)}
        while len(self.choices)>128:self.choices.popitem(last=False)
        return {'choice_id':choice,'label':'Подтвердить зарегистрированное направление' if profile else operation,'valid_for_s':2}
    def refresh(self,row,spec,*,actor):
        self._fresh(row,spec,actor);operations={name:{'implemented':True,'available':False,'choices':[]} for name in OPERATIONS}
        with self.lock:generation=self.generations.get(row['session_id'],0)
        source={};reason=None
        try:
            _,grant,native,source=self._source(row,spec,actor);identity=self._identity(grant,native,source)
            profiles=source.get('direction_profile_choices') or []
            if not isinstance(profiles,list) or len(profiles)>64:raise ValueError('Bounded actual source profile inventory required')
            profiles=[profile for profile in profiles if isinstance(profile,dict) and
                re.fullmatch('[0-9a-f]{64}',str(profile.get('profile_id'))) and profile.get('follower_calibration_sha256')==grant['follower_calibration_sha256']]
            if len({profile['profile_id'] for profile in profiles})!=len(profiles):raise ValueError('Duplicate source profile identity')
            signs=[profile.get('closing_sign') for profile in profiles]
            ambiguity=len(profiles)>1 and (any(type(sign) is not int or sign not in (-1,1) for sign in signs) or len(set(signs))!=1)
            healthy=source.get('persistence_fault') is None and source.get('all_target_writes_blocked') is False
            with self.lock:
                if self.generations.get(row['session_id'],0)!=generation:raise ValueError('Receiver setup was locally invalidated during refresh')
                if healthy and source.get('armed') is False:
                    for name in ('capture_empty_open','capture_empty_closed'):
                        operations[name].update(available=True,choices=[self._mint(name,row,spec,actor,identity)])
                    if profiles and not ambiguity:
                        operations['bind_guard'].update(available=True,choices=[self._mint('bind_guard',row,spec,actor,identity,profile) for profile in profiles])
                    else:operations['bind_guard']['reason']='MULTIPLE_DIRECTION_PROFILES_REQUIRE_SOURCE_REVIEW' if ambiguity else 'OBSERVED_EMPTY_ENDPOINTS_REQUIRED'
                if native.get('mode')=='idle' and isinstance(source.get('interlock_id'),str):
                    operations['reset_safe'].update(available=True,choices=[self._mint('reset_safe',row,spec,actor,identity)])
        except Exception as error:reason=type(error).__name__+':'+str(error)
        summary={key:deepcopy(source.get(key)) for key in ('schema','phase','armed','binding_current','capability_ready','direction_known',
            'persistence_fault','all_target_writes_blocked')}
        summary.update({key:len(source[name]) if isinstance(source.get(name),list) else None for key,name in
            (('direction_capture_count','direction_capture_choices'),('direction_profile_count','direction_profile_choices'))})
        result={'schema':SCHEMA,'operations':operations,'source_status':summary,'reason':reason,'received_ns':self.clock(),
            'cached_snapshot_started_native_control':False,'metadata_is_not_execution_authority':True,
            'native_lease_or_user_observation_renewed':False}
        self._fresh(row,spec,actor)
        with self.lock:
            if self.generations.get(row['session_id'],0)!=generation:raise ValueError('Late receiver setup refresh after invalidation')
            self.cache[row['session_id']]={'context':self.observed._context(row,spec),'actor':actor,'value':deepcopy(result)}
            while len(self.cache)>64:self.cache.pop(next(iter(self.cache)))
        return result
    def snapshot(self,row):
        with self.lock:cached=deepcopy(self.cache.get(row['session_id']))
        if (not cached or cached['context']['epoch']!=row.get('epoch') or cached['context']['process_session']!=row.get('process_session') or
                not 0<=self.clock()-cached['value']['received_ns']<=TTL_NS or self.service.closed or self.workflow.closed or
                row.get('status') in ('STOPPED','INTERRUPTED','COMPLETED')):
            return {'schema':SCHEMA,'operations':{},'reason':'EXPLICIT_RECEIVER_SETUP_REFRESH_REQUIRED',
                'cached_snapshot_started_native_control':False,'metadata_is_not_execution_authority':True}
        return cached['value']
    def _choose(self,operation,row,spec,body,actor):
        self._fresh(row,spec,actor);identity=body.get('receiver_setup_choice_id')
        if not isinstance(identity,str) or not re.fullmatch('[0-9a-f]{64}',identity):raise ValueError('Current server-issued receiver setup choice required')
        with self.lock:
            record=self.choices.get(identity)
            if (not record or record['used'] or record['operation']!=operation or record['actor']!=actor or
                    record['context']!=self.observed._context(row,spec) or not 0<=self.clock()-record['created_ns']<=TTL_NS or
                    record['generation']!=self.generations.get(row['session_id'],0) or
                    row['session_id'] in self.pending):raise ValueError('One-use current operator/session/operation receiver choice required')
            record['used']=True;self.pending.add(row['session_id']);return deepcopy(record)
    def _current_choice(self,selected,row):
        with self.lock:
            if selected['generation']!=self.generations.get(row['session_id'],0):raise ValueError('Receiver metadata operation was locally invalidated')
    def _displayed(self,identity,row,spec,body,actor,grant):
        record,current,_,selected=self.observed._observer(identity,actor);frame=record['frame'];checksum=body.get('displayed_jpeg_sha256')
        if (record['context']!=self.observed._context(row,spec) or current['session_id']!=row['session_id'] or selected.get('role')!='receiver' or
                selected.get('binding')!=grant['binding'] or selected['grants'].get('grant_id')!=grant['grant_id'] or
                selected['grants'].get('control_epoch')!=grant['control_epoch'] or checksum!=frame.get('frame_sha256') or
                record.get('frame_served_sha256')!=checksum or frame.get('follower_calibration_sha256')!=grant['follower_calibration_sha256'] or
                frame.get('frame_boot_id')!=grant['boot_id'] or frame.get('frame_clock_domain')!=grant['clock_domain']):
            raise ValueError('Exact served existing observed-setup JPEG/current actor/grant/calibration required')
        return frame
    def execute(self,operation,row,spec,body,*,actor):
        common={'receiver_setup_choice_id','observing','user_observed_test_confirmed','session_id','expected_epoch'}
        extra={'bind_guard':set(),'capture_empty_open':{'observation_choice_id','displayed_jpeg_sha256','empty_gripper_confirmed'},
            'capture_empty_closed':{'observation_choice_id','displayed_jpeg_sha256','empty_gripper_confirmed'},'reset_safe':{'object_safe_no_dual_hold'}}
        if (operation not in extra or not isinstance(body,dict) or set(body)-common-extra[operation] or
                body.get('observing') is not True or body.get('user_observed_test_confirmed') is not True or
                operation.startswith('capture_') and body.get('empty_gripper_confirmed') is not True or
                operation=='reset_safe' and body.get('object_safe_no_dual_hold') is not True):
            raise ValueError('Separate explicit USER metadata confirmation/known ID-only fields required')
        if ('session_id' in body and body['session_id']!=row['session_id'] or 'expected_epoch' in body and body['expected_epoch']!=row['epoch']):
            raise ValueError('Exact current receiver metadata session/epoch required')
        if operation.startswith('capture_') and (not isinstance(body.get('observation_choice_id'),str) or
                not re.fullmatch('[0-9a-f]{32}',body['observation_choice_id']) or
                not re.fullmatch('[0-9a-f]{64}',str(body.get('displayed_jpeg_sha256')))):
            raise ValueError('Exact server-issued observed image choice/SHA required')
        selected=self._choose(operation,row,spec,body,actor)
        try:
            facade,grant,native,source=self._source(row,spec,actor)
            if self._identity(grant,native,source)!=selected['identity']:raise ValueError('Receiver native grant/interlock changed after choice was displayed')
            payload={'grant_id':grant['grant_id'],'expected_control_epoch':grant['control_epoch'],
                'interlock_id':source.get('interlock_id'),'expected_revision':source.get('revision')}
            if operation=='bind_guard':
                profile=selected['profile']
                if profile not in source.get('direction_profile_choices',[]):raise ValueError('Actual source direction profile changed')
                payload['direction_profile_id']=profile['profile_id'];native_op='bind'
            elif operation=='reset_safe':payload['object_safe_no_dual_hold']=True;native_op='reset_safe'
            else:
                frame=self._displayed(body.get('observation_choice_id'),row,spec,body,actor,grant)
                payload={key:value for key,value in payload.items() if key in ('grant_id','expected_control_epoch')}
                payload.update(label='empty_open' if operation=='capture_empty_open' else 'empty_closed',frame_id=frame['frame_id'],
                    displayed_jpeg_sha256=frame['frame_sha256'],observing=True,empty_gripper_confirmed=True,user_observed_test_confirmed=True)
                native_op='direction/observe'
            self._fresh(row,spec,actor);self._current_choice(selected,row)
            result=facade.cooperative(native_op,payload,authenticated_actor=actor)
            self._fresh(row,spec,actor);self._current_choice(selected,row)
            if operation.startswith('capture_'):self._displayed(body.get('observation_choice_id'),row,spec,body,actor,grant)
            return {'operation':operation,'source_receipt':deepcopy(result),'native_command_sent':False,
                'native_lease_or_user_observation_renewed':False,'physical_execution_authorized':False}
        finally:
            with self.lock:self.pending.discard(row['session_id']);self.cache.pop(row['session_id'],None)
    def invalidate_session(self,row):
        """Local invalidation only; no source revoke/STOP/unfreeze implied."""
        with self.lock:
            self.generations[row['session_id']]=self.generations.get(row['session_id'],0)+1
            for record in self.choices.values():
                if record['context']['session_id']==row['session_id']:record['used']=True
            self.cache.pop(row['session_id'],None)
