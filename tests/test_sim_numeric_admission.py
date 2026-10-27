"""No models/physics/devices: exact retained C-v1 arithmetic and parity guards."""
from copy import deepcopy
from fractions import Fraction

import numpy as np
import pytest

from explorer_shared.contracts import digest
from explorer_shared.learning.sim_numeric_admission import (SCHEMA,FORMULA,NumericAdmissionRejected,
    NumericAdmissionDispatcher,admit_prior9,ieee_interpolation_bound,normalized_state9,command_context)
from explorer_shared.learning.sim_temporal_route import CELLS,TemporalChunkDispatcher,decode_increment9
from explorer_shared.learning.sim_object_route import LOW,HIGH,ObjectRouteRejected


def actual_roundoff_fixture():
    # Literal values retained by C-v1 frame162, raw trace SHA
    # ce21e74aa09a17991fa36af341fdd38b1488c3ace76aa225c004bfcb8278dae5.
    # Only source identity/clock UUID are explicit unit-fixture stand-ins.
    start=[100.4548542085027,.800938875900105,62.8568027727256,29.200963139533997,80.022618010167,30.556178338260324,0.,0.,0.]
    request=[100.72408452630043,0.,62.82925486564636,30.93873918056488,80.06241023540497,30.90053379535675,0.,0.,0.]
    actual=[100.72408452630043,-1.1102230246251565e-16,62.82925486564636,30.93873918056488,80.06241023540497,30.90053379535675,0.,0.,0.]
    identity=dict(implementation_sha256='a'*64,environment_implementation_sha256='b'*64,target_model_sha256='c'*64,engine_version='EXPLICIT_MOCK')
    stamp=dict(boot_id='mock-boot',clock_domain='mock-boot',monotonic_ns=8350000000)
    profile=dict(source_identity=identity,velocity_deg_s=[48.,48.,48.,60.,60.,72.],period_s=.05,timestep_s=.002,substeps=25)
    context=dict(schema=SCHEMA,formula=FORMULA,source_kind='simulation',source_identity=identity,
        physical_io=False,finite_dynamics_step_completed=True,controller_settings_modified=False,receipt_id='mock-dispatch-163',
        pre_action_stamp=dict(stamp,monotonic_ns=8300000000),dispatch_stamp=stamp,
        actual_start9=start,native_command_before6=start[:6],actual_requested9=request,native_requested6=request[:6],
        intended_endpoint6=request[:6],actual_dispatched9=actual,velocity_deg_s=profile['velocity_deg_s'],period_s=.05,timestep_s=.002,substeps=25)
    return actual,stamp,context,profile


def test_exact_retained_negative_ulp_reproduces_and_is_admitted_only_with_bitwise_tensor_identity():
    raw,stamp,context,profile=actual_roundoff_fixture();network,receipt=admit_prior9(raw,stamp,context,profile)
    assert raw[1]==-1.1102230246251565e-16 and network[1]==0 and receipt['kind']=='PROVEN_NATIVE_INTERPOLATION_ROUNDOFF'
    assert receipt['native_reproduction_bitwise'] and receipt['normalization_bytes_unchanged'] and receipt['context_sha256']==digest(context)
    assert normalized_state9(raw).tobytes()==normalized_state9(network).tobytes()
    bound=ieee_interpolation_bound(context['actual_start9'][1],0.)
    assert Fraction.from_float(abs(raw[1]))<=bound<Fraction(1,10**12)


@pytest.mark.parametrize('cell',list(CELLS))
def test_original_normal_path_output_and_float32_input_parity_for_all_four_cells(cell):
    _,_,_,expectation=actual_roundoff_fixture();rate={key:expectation[key] for key in ('velocity_deg_s','period_s')};old_inputs=[];new_inputs=[]
    def predictor(inputs):
        def run(image,state):inputs.append((image.copy(),state.copy()));return np.tile(np.linspace(-.2,.2,16)[:,None],(1,9))
        return run
    original=TemporalChunkDispatcher(predictor(old_inputs),cell=cell,image_size=8,rate_profile=rate,horizon_s=40)
    guarded=NumericAdmissionDispatcher(predictor(new_inputs),cell=cell,image_size=8,rate_profile=rate,horizon_s=40,admission_expectation=expectation)
    rgb=np.full((12,16,3),107,np.uint8)
    for index in range(10):
        prior=np.array([90,90,90,90,90,30,0,0,0.])
        if index==3:prior[0]=np.nextafter(180.,np.inf)  # Already normal in the frozen float32 precheck.
        stamp=dict(boot_id='mock',clock_domain='mock',monotonic_ns=200000000+index*50000000)
        before,old_meta=original.next(rgb,prior,stamp);after,new_meta=guarded.next(rgb,prior,stamp)
        assert before.tobytes()==after.tobytes() and new_meta['numeric_admission']['kind']=='NORMAL'
        assert {key:value for key,value in new_meta.items() if key not in ('numeric_admission','raw_literal_prior9_preserved')}==old_meta
    assert len(old_inputs)==len(new_inputs)==2
    assert all(a[0].tobytes()==b[0].tobytes() and a[1].tobytes()==b[1].tobytes() for a,b in zip(old_inputs,new_inputs))


def test_increment_decoder_preserves_literal_negative_raw_prior_even_when_network_input_is_proven_equivalent():
    raw,stamp,context,profile=actual_roundoff_fixture();inputs=[];rate={key:profile[key] for key in ('velocity_deg_s','period_s')}
    def predict(image,state):inputs.append(state.copy());return np.zeros((16,9))
    original=TemporalChunkDispatcher(predict,cell='ZERO_INCREMENT',image_size=8,rate_profile=rate,horizon_s=40)
    with pytest.raises(ObjectRouteRejected):original.next(np.zeros((8,8,3),np.uint8),raw,stamp)
    guarded=NumericAdmissionDispatcher(predict,cell='ZERO_INCREMENT',image_size=8,rate_profile=rate,horizon_s=40,admission_expectation=profile)
    result,metadata=guarded.next(np.zeros((8,8,3),np.uint8),raw,stamp,previous_command_context=context)
    assert result.tobytes()==np.asarray(raw,float).tobytes() and result[1]<0 and len(inputs)==1 and inputs[0].shape==(10,)
    assert inputs[0][:9].tobytes()==normalized_state9(raw).tobytes()
    assert metadata['raw_literal_prior9_preserved'] and metadata['numeric_admission']['raw_prior9']==raw


@pytest.mark.parametrize('change',['absent','identity','stamp','request','actual','substeps','significant','nan','base'])
def test_unknown_significant_or_unbound_prior_never_reaches_neural_predictor(change):
    raw,stamp,context,profile=actual_roundoff_fixture();raw=deepcopy(raw);context=deepcopy(context);calls=[]
    if change=='absent':context=None
    elif change=='identity':context['source_identity']=dict(context['source_identity'],target_model_sha256='d'*64)
    elif change=='stamp':context['dispatch_stamp']=dict(stamp,monotonic_ns=stamp['monotonic_ns']-1)
    elif change=='request':context['native_requested6']=[0.]*6
    elif change=='actual':context['actual_dispatched9'][1]=0.
    elif change=='substeps':context['substeps']=24
    elif change=='significant':raw[1]=-.001;context['actual_dispatched9']=raw
    elif change=='nan':raw[1]=float('nan')
    else:raw[6]=1.;context['actual_dispatched9']=raw
    guarded=NumericAdmissionDispatcher(lambda *args:calls.append(args),cell='ZERO_ABS',image_size=8,rate_profile=profile,horizon_s=40,admission_expectation=profile)
    with pytest.raises(NumericAdmissionRejected):guarded.next(np.zeros((8,8,3),np.uint8),raw,stamp,previous_command_context=context)
    assert not calls


def test_tensor_difference_is_explicitly_rejected_even_if_a_future_normalizer_changes(monkeypatch):
    import explorer_shared.learning.sim_numeric_admission as module
    raw,stamp,context,profile=actual_roundoff_fixture();original=module.normalized_state9
    def changed(value):
        result=original(value)
        if value[1]<0:result[1]=np.nextafter(result[1],np.float32(-np.inf))
        return result
    monkeypatch.setattr(module,'normalized_state9',changed)
    with pytest.raises(NumericAdmissionRejected,match='tensor differs'):admit_prior9(raw,stamp,context,profile)


def test_context_snapshot_is_stable_when_callback_mutates_original_caller_objects():
    raw,stamp,context,profile=actual_roundoff_fixture();expected_digest=digest(context);raw_original=raw.copy();inputs=[]
    def predict(image,state):
        inputs.append(state.copy());context['actual_start9'][1]=999.;raw[1]=999.;return np.zeros((16,9))
    guarded=NumericAdmissionDispatcher(predict,cell='ZERO_INCREMENT',image_size=8,rate_profile=profile,horizon_s=40,admission_expectation=profile)
    result,metadata=guarded.next(np.zeros((8,8,3),np.uint8),raw,stamp,previous_command_context=context)
    assert result.tobytes()==np.asarray(raw_original,float).tobytes() and metadata['numeric_admission']['context_sha256']==expected_digest


def test_context_builder_reads_only_past_native_command_fields_and_copies_arrays():
    from types import SimpleNamespace
    raw,stamp,expected,profile=actual_roundoff_fixture()
    class CommandsOnly:
        def __init__(self):
            self.command_before=np.array(expected['native_command_before6']);self.requested=np.array(expected['native_requested6']);self.command=np.array(raw[:6]);self.velocity=np.array(profile['velocity_deg_s']);self.period=.05;self.model=SimpleNamespace(opt=SimpleNamespace(timestep=.002))
        def __getattr__(self,name):raise AssertionError('Forbidden truth/goal/phase field requested: '+name)
    env=CommandsOnly();target=SimpleNamespace(env=env,provider_identity=profile['source_identity'])
    observation={'preceding_command9':expected['actual_start9']};dispatch=dict(receipt_id=expected['receipt_id'],dispatched_action9=raw,pre_action_stamp=expected['pre_action_stamp'],dispatch_stamp=stamp,physical_io=False,finite_dynamics_step_completed=True)
    preparation=dict(original_velocity_deg_s=profile['velocity_deg_s'],original_period_s=.05,controller_settings_modified=False)
    result=command_context(target,observation,expected['actual_requested9'],dispatch,preparation)
    env.command_before[1]=999.
    assert result['native_command_before6'][1]==.800938875900105 and not {'phase','goal','object','evaluator','measured_qpos'}.intersection(result)
    network,receipt=admit_prior9(raw,stamp,result,profile);assert network[1]==0 and receipt['native_reproduction_bitwise']
