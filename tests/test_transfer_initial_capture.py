"""Future hook with explicit fake capture/target; no CAD/MJB allocation or render."""
from copy import deepcopy
import json
import pytest

from explorer_shared import sim_initial_state as initial
from explorer_shared.contracts import canonical
from explorer_shared.skill_transfer import (load_source_trace,record_target_trial,initial_state_capture_config,INITIAL_CAPTURE_HELPER)
from explorer_shared.transfer_mapping import sha
from test_skill_transfer import MockTarget,source_trace


class Target(MockTarget):
    def __init__(self,events,**kwargs):super().__init__(**kwargs);self.events=events
    def reset(self,seed):self.events.append('reset');super().reset(seed)
    def observe(self):self.events.append('observe');return super().observe()
    def advance(self,*args,**kwargs):self.events.append('advance');return super().advance(*args,**kwargs)


def inputs(tmp_path):
    path,document,_=source_trace(tmp_path);bundle=load_source_trace(path,sha(path.read_bytes()))
    command=[90]*5+[30,0,0,0]
    class Mapper:
        lineage={'method':'EXPLICIT_SOFTWARE_FIXTURE'}
        def plan(self,*args):return dict(target_action9=command,arm_command6=command[:6],navigation_required=False)
    spec=dict(skill_id=document['skill_id'],version=2,stages=[dict(id='place_verify')])
    return bundle,Mapper(),spec,path


def fake_capture(monkeypatch,events,*,failure=False):
    captured=[]
    def capture(target,*,scenario,source_bindings):
        events.append('capture');captured.append(deepcopy(dict(scenario=scenario,source_bindings=source_bindings)))
        assert events==['reset','capture']
        if failure:raise initial.InitialStateRejected('EXPLICIT_UNSUPPORTED_SOFTWARE_PROFILE')
        # Source/profile codec is tested independently; this inert binary fixture
        # qualifies hook order and immutable save, never actual native replay.
        raw=b'EXPLICIT_SOFTWARE_INITIAL_BYTES_NOT_A_REAL_MJB'
        doc=dict(schema=initial.SCHEMA,profile=initial.PROFILE,payloads={'native-fixture.bin':dict(bytes=len(raw),sha256=sha(raw))},
            source_bindings={role:dict(sha256=value['sha256']) for role,value in source_bindings.items()},
            physical_io=False,retroactive_capture=False,original_runtime_OS_closure='PARTIAL',software_fixture_only=True)
        return initial.InitialStateBundle(doc,{'native-fixture.bin':raw})
    monkeypatch.setattr(initial,'capture_initial_trial',capture)
    return captured


def test_registered_capture_occurs_after_reset_before_any_observe_or_dispatch_and_retains_bound_files(tmp_path,monkeypatch):
    bundle,mapper,spec,source_path=inputs(tmp_path);events=[];captured=fake_capture(monkeypatch,events)
    protocol=tmp_path/'protocol.json';protocol.write_bytes(canonical(dict(scenario_id='predeclared-software-scene')))
    request=initial_state_capture_config(source_bindings={'trial_protocol':dict(path=str(protocol),sha256=sha(protocol.read_bytes()))},scenario={'scene_id':'development-scene-1'})
    result=record_target_trial(bundle,mapper,Target(events),tmp_path/'trial',target_version=2,target_skill_spec=spec,seed=18421,initial_state_capture=request)
    assert events==['reset','capture','observe','advance'] and result['frames']==1
    snapshot=result['initial_state'];assert snapshot['status']=='RECORDED' and snapshot['helper_id']==INITIAL_CAPTURE_HELPER
    assert snapshot['files']==2 and snapshot['total_payload_bytes']>0 and not snapshot['restore_reexecution_verified']
    manifest=tmp_path/'trial'/'initial-state'/'manifest.json'
    assert sha(manifest.read_bytes())==snapshot['manifest_sha256'] and snapshot['manifest_path']==str(manifest)
    initial.load_initial_bundle(manifest.parent,expected_sha256=snapshot['manifest_sha256'])
    source=captured[0]['source_bindings'];assert source['transfer_source_manifest']==dict(path=str(source_path),sha256=bundle['manifest_sha256'])
    assert {'target_trial_recorder','initial_capture_helper','trial_protocol'} <= source.keys()
    scenario=captured[0]['scenario'];assert scenario['seed']==18421 and scenario['task_type']=='pick_object_to_container'
    assert scenario['target_skill_spec_sha256'] and scenario['declared_trial_scenario']=={'scene_id':'development-scene-1'}
    assert result['source_method']=='SCRIPTED_SIM' and not result['physical_success_validated']


def test_capture_artifacts_and_SHA_survive_later_target_failure(tmp_path,monkeypatch):
    bundle,mapper,spec,_=inputs(tmp_path);events=[];fake_capture(monkeypatch,events)
    result=record_target_trial(bundle,mapper,Target(events,fail=True),tmp_path/'failed',target_version=2,target_skill_spec=spec,seed=18,initial_state_capture=initial_state_capture_config())
    assert result['failure']['code']=='TARGET_PROVIDER_FAILURE' and result['initial_state']['status']=='RECORDED'
    saved=json.loads((tmp_path/'failed'/'result.json').read_bytes())
    assert saved['initial_state']['manifest_sha256']==result['initial_state']['manifest_sha256']
    assert (tmp_path/'failed'/'initial-state'/'native-fixture.bin').exists() and not result['eligible_for_target_dataset']


def test_capture_failure_blocks_first_observation_dispatch_and_retains_actual_failed_attempt(tmp_path,monkeypatch):
    bundle,mapper,spec,_=inputs(tmp_path);events=[];fake_capture(monkeypatch,events,failure=True)
    result=record_target_trial(bundle,mapper,Target(events),tmp_path/'unsupported',target_version=2,target_skill_spec=spec,seed=18,initial_state_capture=initial_state_capture_config())
    assert events==['reset','capture'] and result['frames']==0
    assert result['failure']['code']=='INITIAL_STATE_CAPTURE_FAILED_BEFORE_TARGET_DISPATCH'
    assert result['initial_state']['status']=='CAPTURE_FAILED' and not result['eligible_as_simulation_expert']
    assert json.loads((tmp_path/'unsupported'/'target-trace.json').read_bytes())==[]
    assert not (tmp_path/'unsupported'/'rgb').exists() and not (tmp_path/'unsupported'/'dispatch').exists()


@pytest.mark.parametrize('change',['unregistered','helper_sha','reserved_binding'])
def test_unregistered_or_changed_capture_code_and_source_bindings_fail_before_first_observe(tmp_path,change):
    bundle,mapper,spec,_=inputs(tmp_path);request=initial_state_capture_config();events=[]
    if change=='unregistered':request['helper_id']='arbitrary_python_callback'
    elif change=='helper_sha':request['helper_sha256']='0'*64
    else:request['source_bindings']['target_trial_recorder']=dict(path='some-other.py',sha256='0'*64)
    result=record_target_trial(bundle,mapper,Target(events),tmp_path/'blocked',target_version=2,target_skill_spec=spec,seed=18,initial_state_capture=request)
    assert events==['reset'] and result['frames']==0 and result['initial_state']['status']=='CAPTURE_FAILED'
    assert not result['full_target_simulation_success']


def test_no_capture_option_is_explicitly_NOT_RECORDED_and_does_not_call_helper(tmp_path,monkeypatch):
    bundle,mapper,spec,_=inputs(tmp_path);events=[]
    monkeypatch.setattr(initial,'capture_initial_trial',lambda *args,**kwargs:pytest.fail('Capture must be an explicit opt-in'))
    result=record_target_trial(bundle,mapper,Target(events),tmp_path/'without',target_version=2,target_skill_spec=spec,seed=18)
    assert events==['reset','observe','advance'] and result['initial_state']['status']=='NOT_RECORDED'
    assert result['initial_state']['reason']=='CAPTURE_NOT_REQUESTED' and not result['initial_state']['restore_reexecution_verified']
    assert not (tmp_path/'without'/'initial-state').exists()


def test_real_registered_helper_rejects_unsupported_mock_provider_before_observe_without_fake_success(tmp_path):
    bundle,mapper,spec,_=inputs(tmp_path);events=[]
    result=record_target_trial(bundle,mapper,Target(events),tmp_path/'real-helper',target_version=2,target_skill_spec=spec,seed=18,initial_state_capture=initial_state_capture_config())
    assert events==['reset'] and result['initial_state']['status']=='CAPTURE_FAILED' and result['failure']
    assert result['frames']==0 and not result['eligible_for_target_dataset']


def test_failed_save_retains_manifest_location_but_never_reports_a_complete_capture(tmp_path,monkeypatch):
    bundle,mapper,spec,_=inputs(tmp_path);events=[];fake_capture(monkeypatch,events);save=initial.save_initial_bundle
    def failed_after_save(*args):save(*args);raise OSError('EXPLICIT_SOFTWARE_SAVE_RETURN_FAILURE')
    monkeypatch.setattr(initial,'save_initial_bundle',failed_after_save)
    result=record_target_trial(bundle,mapper,Target(events),tmp_path/'save-failure',target_version=2,target_skill_spec=spec,seed=18,initial_state_capture=initial_state_capture_config())
    assert events==['reset','capture'] and result['initial_state']['status']=='CAPTURE_FAILED'
    assert result['initial_state']['artifact_retained_but_capture_unverified'] is True
    manifest=tmp_path/'save-failure'/'initial-state'/'manifest.json'
    assert result['initial_state']['manifest_sha256']==sha(manifest.read_bytes()) and result['frames']==0


def test_capture_request_is_owned_before_reset_can_change_callers_configuration(tmp_path,monkeypatch):
    bundle,mapper,spec,_=inputs(tmp_path);events=[];captured=fake_capture(monkeypatch,events)
    request=initial_state_capture_config(scenario={'scene_id':'original-declared-scene'})
    class MutatingTarget(Target):
        def reset(self,seed):
            super().reset(seed);request['helper_sha256']='0'*64;request['scenario']['scene_id']='mutated-during-reset'
    result=record_target_trial(bundle,mapper,MutatingTarget(events),tmp_path/'owned',target_version=2,target_skill_spec=spec,seed=18,initial_state_capture=request)
    assert result['initial_state']['status']=='RECORDED'
    assert captured[0]['scenario']['declared_trial_scenario']=={'scene_id':'original-declared-scene'}


def test_declared_scenario_cannot_name_a_different_reset_seed(tmp_path):
    bundle,mapper,spec,_=inputs(tmp_path);events=[]
    result=record_target_trial(bundle,mapper,Target(events),tmp_path/'seed-mismatch',target_version=2,target_skill_spec=spec,seed=18,initial_state_capture=initial_state_capture_config(scenario={'seed':19}))
    assert events==['reset'] and result['failure']['code']=='INITIAL_CAPTURE_DECLARED_SEED_DIFFERS_FROM_RESET'
    assert result['initial_state']['status']=='CAPTURE_FAILED' and result['frames']==0
