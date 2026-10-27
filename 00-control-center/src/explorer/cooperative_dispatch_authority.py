"""ENGINEERING_GLUE: fresh per-role admission before an original native RPC.

No constructor, snapshot or preview grants motion. The moving role must retain
its exact current Root-bound native transport. The other role may be active or
source-verified parked; neither cached readiness nor parked state enables its
own motion. Original source validation, watchdogs and publication gates remain
the final boundary. This module never starts, resumes, renews or sends a target.
"""
from copy import deepcopy

from explorer_shared.contracts import digest

OPERATIONS={'giver':('acquire','offer','release'),'receiver':('approach','close','finish')}


class CooperativeDispatchAuthority:
    def __init__(self,service):self.service=service;self.clock=service.clock
    def _pin(self,row,spec):
        return {'context':self.service.observed_setup._context(row,spec),
            'original_binding_sha256':digest(row.get('observed_test_binding')),
            'lineage_sha256':digest(row.get('native_grant_successors')),
            'ledger_sha256':digest(row.get('native_control_ledger')),
            'phase':self.service.native_controls._phase_pin(row)}
    def _live(self,row,spec,role,transport):
        if transport is None:raise ValueError('Current own native transport required: '+role)
        began=self.clock();native=self.service.native_controls
        pair=native._source(role);native._check_permission(role,row,spec,pair)
        binding={'run_id':row['session_id'],'object_id':row['protocol_object_id'],'coordinator_epoch':row['epoch']}
        if any((getattr(transport,'binding',None) or {}).get(key)!=value for key,value in binding.items()):
            raise ValueError('Native transport belongs to another Root intent/epoch: '+role)
        if role=='giver':
            permission=pair['permission'];source=pair['source'];current=source.get('native') or {};boundary=source.get('release_boundary') or {}
            if (transport.binding.get('authorization_id')!=permission.get('authorization_id') or
                    not native._same_binding(source,native._binding(row,pair)) or
                    not self.service._durable_giver(boundary) or boundary.get('parked') is not None or boundary.get('reference_fault') is not None):
                raise ValueError('Exact current unparked durable giver scope required')
            if row['giver_input_mode']=='policy':
                model={key:spec['subskills']['giver'][key] for key in ('model_id','checkpoint_sha256','spec_sha256')}
                if source.get('policy')!=model or getattr(transport,'epoch',None)!=current.get('control_epoch'):
                    raise ValueError('Exact giver model and returned native epoch required')
            elif getattr(transport,'generation',None)!=current.get('owner_generation'):
                raise ValueError('Exact returned manual giver generation required')
        else:
            grant=pair['permission'].get('grant') or {};saved=getattr(transport,'grant',None) or {}
            if (any(saved.get(key)!=grant.get(key) for key in ('grant_id','provider_process','control_epoch','actor_id','boot_id',
                    'clock_domain','follower_calibration_sha256','binding')) or
                    (getattr(getattr(transport,'permission',None),'binding',None))!=self.service.observed_setup.receiver_binding(row,spec) or
                    getattr(transport,'reference',None)!=spec['subskills']['receiver']):
                raise ValueError('Exact current receiver model/grant/provider/native epoch required')
        state=transport.observe_state();now=self.clock()
        if (not isinstance(state,dict) or state.get('current_native_owner_ready') is not True or not state.get('source_receipt') or
                state.get('clock_domain')!=self.service.workflow.domain or type(state.get('received_ns')) is not int or
                not began<=state['received_ns']<=now or not 0<=now-began<=2_000_000_000):
            raise ValueError('Fresh actual source owner receive proof required: '+role)
        receipt=state['source_receipt']
        latest={'source':self.service._giver_source(state),
            'permission':state.get('observed_activation_receipt') or receipt.get('activation') or {}} if role=='giver' else {
                'source':receipt.get('native') or {},'permission':receipt.get('permission') or {}}
        native._check_permission(role,row,spec,latest)
        first=native._identity(role,pair);last=native._identity(role,latest)
        if role=='giver':
            for key in ('grant','pending_challenge'):
                first[key]=deepcopy((pair['source'].get('release_boundary') or {}).get(key))
                last[key]=deepcopy((latest['source'].get('release_boundary') or {}).get(key))
        # Original stream quiescence changes during active policy execution. It
        # is a phase-park barrier, not an ordinary latest-intent admission gate.
        first.pop('quiescence',None);last.pop('quiescence',None)
        if first!=last or role=='giver' and pair['source'].get('policy')!=latest['source'].get('policy'):
            raise ValueError('Native scope/model changed during fresh transport inspection: '+role)
        if role=='receiver' and state.get('cooperative_dual_hold_publication_interlock_current') is not True:
            raise ValueError('Current original receiver publication interlock required')
        return {'kind':'active_native_transport','role':role,'source_pair':deepcopy(latest),'observed_state':deepcopy(state),
            'received_nx_ns':now,'clock_domain':self.service.workflow.domain}
    @staticmethod
    def _armed(role,proof):
        if proof['kind']=='parked_observation':return proof['scope']['facts'].get('armed') is True
        state=proof['observed_state']
        if role=='giver':return (proof['source_pair']['source'].get('release_boundary') or {}).get('armed') is True
        return (state.get('receiver_interlock') or {}).get('source_armed') is True
    def require(self,row,spec,role,operation,*,expected_transport=None):
        """GET-only check; callers still need actual original dispatch receipts."""
        if role not in OPERATIONS or operation not in OPERATIONS[role]:raise ValueError('Fixed native role/operation required')
        native=self.service.native_controls;actor=(row.get('observed_test_binding') or {}).get('actor_id')
        row=native._fresh(row,spec,actor);pin=self._pin(row,spec);began=self.clock()
        if not actor:raise ValueError('Current authenticated Root-bound actor required')
        other='receiver' if role=='giver' else 'giver'
        with self.service.lock:
            moving=self.service.transports.get((row['session_id'],role));partner=self.service.transports.get((row['session_id'],other))
            attached=deepcopy(self.service.native_bindings.get(row['session_id']) or {})
        if (moving is None or expected_transport is not None and moving is not expected_transport or
                attached.get('epoch')!=row['epoch'] or attached.get('actor')!=actor):
            raise ValueError('Explicit current Root transport attachment for this actor/epoch required')
        # Never fall back to a parked proof for the moving role.
        self._live(row,spec,role,moving)
        try:partner_proof=self._live(row,spec,other,partner)
        except Exception:
            scope=self.service.observed_scopes.require_current(row,spec,(other,),actor=actor)['roles'][other]
            if (scope['facts'].get('original_publication_guard_current') is not True or
                    scope['facts'].get('durable_guard_current') is not True or
                    other=='receiver' and scope['facts'].get('direction_current') is not True):
                raise ValueError('Exact parked partner publication guard/direction required')
            partner_proof={'kind':'parked_observation','role':other,'scope':deepcopy(scope)}
        # Partner I/O may consume the original moving controller lease. Read the
        # moving source again; no permission, source timestamp or lease is reset.
        moving_proof=self._live(row,spec,role,moving)
        current=native._fresh(row,spec,actor)
        if self._pin(current,spec)!=pin or not 0<=self.clock()-began<=2_000_000_000:
            raise ValueError('Root context/source receive window changed during dispatch check')
        with self.service.lock:
            if (self.service.transports.get((row['session_id'],role)) is not moving or
                    self.service.transports.get((row['session_id'],other)) is not partner or
                    self.service.native_bindings.get(row['session_id'])!=attached):
                raise ValueError('Root native transport attachment changed during source checks')
        proofs={role:moving_proof,other:partner_proof}
        if operation in ('close','release') and not all(self._armed(key,value) for key,value in proofs.items()):
            raise ValueError('Both original dual-hold publication latches must already be armed')
        return {'schema':'explorer_cooperative_fresh_dispatch_scope_v1','run_id':row['session_id'],
            'object_id':row['protocol_object_id'],'epoch':row['epoch'],'role':role,'operation':operation,
            'actor_id':actor,'root_pin':pin,'source_proofs':proofs,
            'dispatch_authority_by_role':{key:key==role for key in OPERATIONS},'per_role_authority_is_metadata_preview':False,
            'source_revalidation_required_before_native_dispatch':False,
            'received_nx_ns':self.clock(),'received_clock_domain':self.service.workflow.domain,
            'original_native_boundary_validation_still_required':True,'native_commands_sent':0,
            'native_lease_renewed':False,'physical_attainment_claimed':False}
