"""ENGINEERING_GLUE: consume an already-installed source release grant.

Reuse the same native manual/policy transports and Publisher receipt checks.
Only the explicit parked release CONTROL binder creates these adapters. Grant
validation uses fresh fixed GETs and the source clock, never re-authorization,
native lease renewal, Source challenge replay or model/driver construction.
"""
from copy import deepcopy

from .contracts import digest
from .cooperative_transport05 import NativeGiverPolicyTransport,NativeGiverManualTransport


class _InstalledGrant:
    def _bind_installed(self,grant,descriptor):
        if (not isinstance(grant,dict) or not isinstance(descriptor,dict) or
                grant.get('challenge')!=descriptor.get('challenge') or
                descriptor.get('source_challenge_sha256')!=digest(grant.get('challenge')) or
                grant.get('expires_native_ns')!=(grant.get('challenge') or {}).get('expires_native_ns') or
                not isinstance(grant.get('grant_id'),str) or not grant['grant_id']):
            raise ValueError('LITERAL_VALIDATED_PARKED_SOURCE_GRANT_AND_ROOT_DESCRIPTOR_REQUIRED')
        self.installed_grant=deepcopy(grant);self.release_descriptor=deepcopy(descriptor)
    def _authorize_release(self,authorization):
        installed=self.installed_grant;descriptor=self.release_descriptor;challenge=installed['challenge']
        if (not isinstance(authorization,dict) or authorization.get('preinstalled_parked_release_grant')!=installed or
                authorization.get('native_release_challenge')!=descriptor or
                (authorization.get('operator_confirmation_receipt') or {}).get('confirmation')!=installed.get('operator_confirmation') or
                descriptor.get('received_clock_domain')!=self.domain or type(descriptor.get('deadline_nx_ns')) is not int or
                not self.clock()<descriptor['deadline_nx_ns']):
            raise ValueError('EXACT_POST_CHALLENGE_USER_RELEASE_AUTHORIZATION_REQUIRED')
        state=self.observe_state()
        source=state.get('source_receipt') or {}
        if source.get('schema')!='explorer_native_cooperative_owner_v1':source=source.get('native') or {}
        boundary=source.get('release_boundary') or {};native=source.get('native') or {};stamp=boundary.get('source_stamp') or {}
        persistence=boundary.get('persistence') or {};actual=boundary.get('grant') or {};bound=boundary.get('binding') or {}
        if (state.get('current_native_owner_ready') is not True or actual!=installed or boundary.get('armed') is not True or
                boundary.get('schema')!='explorer_native_receiver_hold_boundary_v1' or
                not {'reference_fault','parked','pending_challenge','grant'}<=boundary.keys() or
                boundary.get('parked') is not None or boundary.get('pending_challenge') is not None or boundary.get('reference_fault') is not None or
                boundary.get('revision')!=installed.get('revision') or
                any(bound.get(key)!=self.binding.get(key) or challenge.get(key)!=self.binding.get(key) for key in
                    ('run_id','object_id','coordinator_epoch','authorization_id')) or
                (bound.get('observed_source_authorization') or {}).get('actor_id')!=descriptor.get('actor_id') or
                any(stamp.get(key)!=challenge.get(source_key) or native.get(key)!=challenge.get(source_key) for key,source_key in
                    (('boot_id','native_boot_id'),('clock_domain','native_clock_domain'))) or
                type(stamp.get('monotonic_ns')) is not int or
                not challenge['issued_native_ns']<=stamp['monotonic_ns']<installed['expires_native_ns'] or
                installed['expires_native_ns']!=challenge['expires_native_ns'] or
                native.get('owner_generation')!=challenge.get('source_native_generation') or
                bound.get('native_control_epoch')!=challenge.get('source_control_epoch') or
                not all(persistence.get(key) is True for key in ('installed','healthy','initialized','fail_closed_publication_gate')) or
                persistence.get('pending') is not False or persistence.get('recovery_required') is not False or persistence.get('fault') is not None):
            raise ValueError('CURRENT_UNCHANGED_INSTALLED_SOURCE_GRANT_NATIVE_EXPIRY_AND_OWNER_REQUIRED')
        proof=installed.get('operator_confirmation') or {};stationary=installed.get('stationary_confirmations') or {}
        for statement,value in [('receiver_holds_object',proof)]+list(stationary.items()):
            if (value.get('statement')!=statement or value.get('state')!='consumed' or value.get('affirmed') is not True or
                    value.get('observing') is not True or value.get('authenticated_actor')!=descriptor.get('actor_id') or
                    value.get('run_id')!=self.binding['run_id'] or value.get('object_id')!=self.binding['object_id']):
                raise ValueError('LITERAL_CONSUMED_SAME_ACTOR_RELEASE_AND_STATIONARY_FACTS_REQUIRED')
        if set(stationary)!={'giver_stationary','receiver_stationary'}:raise ValueError('BOTH_INDEPENDENT_STATIONARY_CONFIRMATIONS_REQUIRED')
        return deepcopy(installed)  # Deliberately no POSTguard/authorize or lease.


class InstalledGrantGiverPolicyTransport(_InstalledGrant,NativeGiverPolicyTransport):
    def __init__(self,*args,installed_grant,release_descriptor,**kwargs):
        super().__init__(*args,**kwargs);self._bind_installed(installed_grant,release_descriptor)


class InstalledGrantGiverManualTransport(_InstalledGrant,NativeGiverManualTransport):
    def __init__(self,*args,installed_grant,release_descriptor,**kwargs):
        super().__init__(*args,**kwargs);self._bind_installed(installed_grant,release_descriptor)
