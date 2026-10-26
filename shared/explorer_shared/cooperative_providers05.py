"""ENGINEERING_GLUE: pinned existing native skill/training/execution providers.

Callbacks bind real Root-owned workflow/transport APIs. Missing capabilities
remain explicit blockers; no provider fabricates dispatch, measured q or hold.
"""
from copy import deepcopy
from dataclasses import dataclass
from typing import Callable

from .cooperative_contract05 import ROLE_CONTRACTS,GIVER_SOURCES,digest,native_role_reference
from .experiments import experiment_id


def contract_projection(value,role):
    value=value if isinstance(value,dict) else {}
    identifier=value.get('id',value.get('schema'));expected=ROLE_CONTRACTS[role]
    return {'id':identifier,'dimension':value.get('dimension'),'names':deepcopy(value.get('names')),
            'units':deepcopy(value.get('units',expected['units'] if identifier==expected['id'] else None))}


def phase_ids(spec):
    if isinstance(spec.get('phase_ids'),list):return list(spec['phase_ids'])
    return [phase['id'] if isinstance(phase,dict) else phase for phase in spec.get('stages',spec.get('phases',[]))]


@dataclass(frozen=True)
class NativeSkillProvider:
    experiment_slug:str
    role:str
    inspect_skill:Callable
    build_aggregate:Callable|None=None
    training_request:Callable|None=None
    predict_shadow:Callable|None=None
    observe_state:Callable|None=None
    record_start:Callable|None=None
    record_stage:Callable|None=None
    record_finish:Callable|None=None
    inspect_episode:Callable|None=None
    submit_observed:Callable|None=None
    stop:Callable|None=None
    native_dispatch_receipts:bool=False
    authoritative_release_deadline:bool=False


class CooperativeProviders:
    def __init__(self):self.providers={}
    def register(self,provider):
        if (not isinstance(provider,NativeSkillProvider) or provider.role not in ROLE_CONTRACTS or
                not callable(provider.inspect_skill)):
            raise ValueError('Typed native provider required')
        allowed=GIVER_SOURCES if provider.role=='giver' else ('so101_source_learning',)
        if provider.experiment_slug not in allowed:raise ValueError('Provider action embodiment mismatch')
        key=(provider.role,provider.experiment_slug)
        if key in self.providers:raise ValueError('Native provider already registered')
        self.providers[key]=provider
    def provider(self,role,reference):
        ref=native_role_reference(role,reference)
        return self.providers.get((role,ref['experiment_slug']))
    def inspect(self,role,reference):
        ref=native_role_reference(role,reference);provider=self.provider(role,ref)
        if provider is None:
            return {'role':role,'bound':False,'blockers':['NATIVE_'+role.upper()+'_PROVIDER_NOT_BOUND'],
                    'action_contract':deepcopy(ROLE_CONTRACTS[role]),'physical_execution_authorized':False}
        try:result=provider.inspect_skill(deepcopy(ref))
        except Exception as error:
            return {'role':role,'bound':True,'blockers':['NATIVE_SKILL_INSPECTION_FAILED:'+type(error).__name__+':'+str(error)],
                    'action_contract':deepcopy(ROLE_CONTRACTS[role]),'physical_execution_authorized':False}
        if not isinstance(result,dict):raise ValueError('Native skill inspection must return an evidence object')
        blockers=[]
        if result.get('skill_id')!=ref['skill_id'] or result.get('skill_version')!=ref['skill_version']:
            blockers.append('PINNED_NATIVE_SKILL_ID_VERSION_MISMATCH')
        if result.get('spec_sha256')!=ref['spec_sha256']:blockers.append('PINNED_NATIVE_SPEC_SHA_MISMATCH')
        if result.get('action_contract')!=ROLE_CONTRACTS[role]:blockers.append('NATIVE_ACTION_CONTRACT_MISMATCH')
        if result.get('confirmed') is not True:blockers.append('NATIVE_SUBSKILL_NOT_CONFIRMED')
        known_phases=set(result.get('phase_ids',[]))
        if any(phase not in known_phases for phase in ref.get('phase_map',{}).values()):
            blockers.append('SOURCE_PHASE_MAP_NOT_IN_PINNED_SPEC')
        model=result.get('model') or {}
        if ref.get('model_id') is not None and (model.get('model_id')!=ref['model_id'] or
                model.get('checkpoint_sha256')!=ref['checkpoint_sha256']):
            blockers.append('PINNED_NATIVE_MODEL_CHANGED')
        return {'role':role,'bound':True,'reference':ref,'source':deepcopy(result),'blockers':blockers,
                'recording_supported':all(callable(value) for value in (provider.record_start,provider.record_finish)),
                'source_phase_annotations_supported':callable(provider.record_stage),
                'shadow_supported':callable(provider.predict_shadow),
                'native_dispatch_receipts':provider.native_dispatch_receipts,
                'authoritative_release_deadline':provider.authoritative_release_deadline,
                'action_contract':deepcopy(ROLE_CONTRACTS[role]),'source_sha256':digest(result),
                'physical_execution_authorized':False}
    def training_requests(self,spec,settings=None):
        requests={};blockers=[]
        for role,ref in spec['subskills'].items():
            state=self.inspect(role,ref);blockers.extend(role+':'+value for value in state['blockers'])
            provider=self.provider(role,ref)
            if provider is None or not callable(provider.training_request):
                blockers.append(role+':EXISTING_NATIVE_TRAINER_NOT_BOUND')
            elif not state['blockers']:
                try:requests[role]=provider.training_request(deepcopy(ref),deepcopy((settings or {}).get(role,{})))
                except Exception as error:blockers.append(role+':'+type(error).__name__+':'+str(error))
        return {'schema':'explorer_cooperative_training_plan_v1','requests':requests,'blockers':blockers,
                'ready':not blockers,'jobs_enqueued':False,'action_spaces_merged':False,
                'coordinator':{'method':'HANDOVER_VERIFIED_FSM','trained_model_claimed':False},
                'training_uses_existing_native_pipelines':True,'native_dimensions':{'giver':9,'receiver':6}}
    def inspect_episode(self,role,reference,episode_reference):
        provider=self.provider(role,reference)
        if provider is None or not callable(provider.inspect_episode):
            return {'admitted_for_training':False,'reasons':['NATIVE_EPISODE_INSPECTOR_NOT_BOUND']}
        try:result=provider.inspect_episode(deepcopy(episode_reference))
        except Exception as error:
            return {'admitted_for_training':False,'reasons':['NATIVE_EPISODE_INSPECTION_FAILED:'+type(error).__name__+':'+str(error)]}
        if not isinstance(result,dict):raise ValueError('Native source admission evidence required')
        if result.get('action_contract')!=ROLE_CONTRACTS[role]:
            return {**result,'admitted_for_training':False,
                    'reasons':list(result.get('reasons',[]))+['NATIVE_EPISODE_ACTION_CONTRACT_MISMATCH']}
        return deepcopy(result)


def existing_workflow_providers(store,*,rosmaster_workflow,rosmaster_aggregate,rosmaster_training,
                                lab_workflow,lab_readiness,lab_aggregate,lab_training_request=None,
                                bindings=None):
    """Bind public existing workflows; Root supplies source/transport callbacks.

No queue operation, network request or model initialization occurs here. SO101
and ROSMASTER training stay in their actual native existing job implementations.
"""
    registry=CooperativeProviders();bindings=bindings or {}
    for slug in GIVER_SOURCES:
        def inspect(ref,_slug=slug):
            project=experiment_id(_slug)
            if project not in ('02','03'):
                raise ValueError('TRANSFER_NATIVE_WORKFLOW_PROVIDER_REQUIRES_EXPLICIT_BINDING')
            state=rosmaster_workflow(store,ref['skill_id'],ref['skill_version'],project_id=project)
            spec=state.get('spec') or {}
            return {'skill_id':spec.get('skill_id'),'skill_version':spec.get('version'),
                'spec_sha256':state.get('spec_sha256'),'confirmed':state.get('confirmed') is True,
                'action_contract':contract_projection(spec.get('action_profile') or spec.get('action_contract'),'giver'),
                'contract_units_basis':'provided_or_canonical_registered_contract_ID',
                'phase_ids':phase_ids(spec),
                'train_ready':state.get('train_ready') is True,'training_blockers':state.get('training_blockers',[]),
                'workflow':state,'model':None,'physical_task_qualified':False}
        def train(ref,settings,_slug=slug):
            if experiment_id(_slug) not in ('02','03'):
                raise ValueError('TRANSFER_NATIVE_TRAINING_PROVIDER_REQUIRES_EXPLICIT_BINDING')
            return rosmaster_training(store,ref['skill_id'],ref['skill_version'],settings,
                                      project_id=experiment_id(_slug))
        callbacks=dict(bindings.get(slug,{}));inspect_callback=callbacks.pop('inspect_skill',inspect)
        aggregate=callbacks.pop('build_aggregate',lambda ref:rosmaster_aggregate(store,ref['skill_id'],ref['skill_version']))
        training=callbacks.pop('training_request',train)
        registry.register(NativeSkillProvider(slug,'giver',inspect_callback,
            build_aggregate=aggregate,training_request=training,**callbacks))
    def inspect_lab(ref):
        state=lab_workflow(store,ref['skill_id'],ref['skill_version'])
        readiness=lab_readiness(store,ref['skill_id'],ref['skill_version']);spec=state.get('spec') or {}
        return {'skill_id':spec.get('skill_id'),'skill_version':spec.get('version'),
            'spec_sha256':state.get('spec_sha256'),'confirmed':state.get('confirmed') is True,
            'action_contract':contract_projection(spec.get('action_contract'),'receiver'),
            'contract_units_basis':'provided_or_canonical_registered_contract_ID',
            'phase_ids':phase_ids(spec),
            'train_ready':state.get('train_ready') is True,'model_readiness':readiness,
            'workflow':state,'model':None,'physical_task_qualified':False}
    callbacks=dict(bindings.get('so101_source_learning',{}));inspect_callback=callbacks.pop('inspect_skill',inspect_lab)
    def plan_lab(ref,settings):
        if lab_training_request is not None:return lab_training_request(store,ref['skill_id'],ref['skill_version'],settings)
        state=lab_workflow(store,ref['skill_id'],ref['skill_version'])
        if state.get('data_ready') is not True or state.get('train_ready') is not True:
            raise ValueError('SOURCE04_NATIVE_DATA_AND_TRAIN_READINESS_REQUIRED')
        # The existing source04 owner alone qualifies backend/profile and
        # enqueues. This is an operation descriptor, never a fabricated job.
        return {'operation':'explorer.lab_skill_workflow.enqueue_training','kind':'so101_skill_train',
                'provider':'so101_source_learning','skill_id':ref['skill_id'],'skill_version':ref['skill_version'],
                'spec_sha256':ref['spec_sha256'],'settings':deepcopy(settings),
                'native_owner_must_revalidate_before_enqueue':True,'jobs_enqueued':False}
    aggregate=callbacks.pop('build_aggregate',lambda ref:lab_aggregate(store,ref['skill_id'],ref['skill_version']))
    training=callbacks.pop('training_request',plan_lab)
    registry.register(NativeSkillProvider('so101_source_learning','receiver',inspect_callback,
        build_aggregate=aggregate,training_request=training,**callbacks))
    return registry
