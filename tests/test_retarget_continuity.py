"""Independent clocks/source SHA and finite gateway qualification, fixtures only."""
from copy import deepcopy
import hashlib
import json

import pytest

from explorer_shared.retarget_continuity import inspect_recorded_trace,qualify_recorded_trace,sequence_metrics
from test_pi_native_retarget import Geometry


def encoded(value):return json.dumps(value,separators=(',',':'),allow_nan=False).encode()


def trace_fixture(*,changing=True):
    samples=[];sources={};first=2**54+123
    for index in range(10):
        value=index/10 if changing else 0.
        original={'available':True,'mode':'idle','control_epoch':'software-station-epoch',
            'roles':{'leader':{'calibration_sha256':'a'*64}},
            'measurements':{'leader':[value]*6,'monotonic_ns':first+index*33_000_001,'clock_domain':'pi:software-boot'}}
        name=f'sources/{index:04}.json';raw=encoded(original);sources[name]=raw
        samples.append({'index':index,'raw_source_file':name,'raw_source_sha256':hashlib.sha256(raw).hexdigest(),
            'source_monotonic_ns':original['measurements']['monotonic_ns'],'source_clock_domain':'pi:software-boot',
            'leader':[value]*6,'source_calibration_sha256':'a'*64,'station_control_epoch':'software-station-epoch',
            'available':True,'station_mode':'idle','request_started_homebrain_ns':123+index*5_000_000,
            'response_received_homebrain_ns':900+index*5_000_000})
    return {'schema':'explorer_leader_measured_cache_trace_v1','scope':'SOFTWARE_GENERATED_TEST_INPUTS','samples':samples},sources


def test_real_shape_trace_uses_integer_differences_and_never_equates_two_clocks(tmp_path):
    trace,sources=trace_fixture();value=inspect_recorded_trace(encoded(trace),sources)
    assert value['source_timestamp_metrics']['dt_ns']['min']==33_000_001
    assert value['homebrain_GET_RTT_ns']['min']==777
    assert value['changing_input_observed'] and value['source_identity_count']==1
    assert not value['cross_device_clock_subtraction_used']
    result=qualify_recorded_trace(encoded(trace),sources,state_dir=tmp_path)
    assert result['software_gateway_comparison']['state']=='BLOCKED'
    assert result['full_continuity_qualification']=='OPEN' and result['physical_commands']==0


def test_finite_memory_sink_compares_exact_original_geometry_outputs_and_reports_synthetic_scope(tmp_path):
    trace,sources=trace_fixture()
    result=qualify_recorded_trace(encoded(trace),sources,state_dir=tmp_path,geometry_factory=Geometry,
        target_reference={'servo_deg':[90]*6,'measured':False,'boot_id':'software-native-boot'},original_native_period_s=.04)
    comparison=result['software_gateway_comparison']
    assert comparison['state']=='PASS' and comparison['accepted_inputs']==9
    assert comparison['exact_unchanged_geometry_velocity_values'] and comparison['injected_zero_or_substituted_vector_count']==0
    assert comparison['resume_calls']==comparison['explicit_neutral_lease_calls']==1
    assert comparison['registration_provenance']['provenance']=='synthetic'
    assert result['source_declared_scope']=='SOFTWARE_GENERATED_TEST_INPUTS'
    assert result['full_continuity_qualification']=='OPEN' and not result['native_Ruckig_executed']


def test_static_real_shape_trace_cannot_satisfy_changing_continuity_even_when_memory_comparison_passes(tmp_path):
    trace,sources=trace_fixture(changing=False)
    result=qualify_recorded_trace(encoded(trace),sources,state_dir=tmp_path,geometry_factory=Geometry,
        target_reference={'servo_deg':[90]*6,'measured':False,'boot_id':'software-native-boot'},original_native_period_s=.04)
    assert result['software_gateway_comparison']['state']=='PASS'
    assert not result['changing_input_observed'] and 'CHANGING_REAL_LEADER_TRACE_REQUIRED' in result['open_facts']
    assert result['source_position_ranges_normalized']==[0.]*6 and result['full_continuity_qualification']=='OPEN'
    assert result['software_gateway_comparison']['naturally_zero_baseline_vectors']==9


@pytest.mark.parametrize('change',['source_bytes','projection','path','source_clock','bool_timestamp','identity','reference'])
def test_rehashed_malformed_or_unrelated_inputs_fail_closed(tmp_path,change):
    trace,sources=trace_fixture();kwargs={}
    if change=='source_bytes':sources['sources/0001.json']+=b' '
    elif change=='projection':trace['samples'][1]['leader'][0]=42
    elif change=='path':trace['samples'][1]['raw_source_file']='../private.json'
    elif change in ('source_clock','bool_timestamp','identity'):
        name='sources/0001.json';original=json.loads(sources[name])
        if change=='source_clock':original['measurements']['clock_domain']=None;trace['samples'][1]['source_clock_domain']=None
        elif change=='bool_timestamp':original['measurements']['monotonic_ns']=True;trace['samples'][1]['source_monotonic_ns']=True
        else:original['control_epoch']='foreign';trace['samples'][1]['station_control_epoch']='foreign'
        sources[name]=encoded(original);trace['samples'][1]['raw_source_sha256']=hashlib.sha256(sources[name]).hexdigest()
    if change in ('identity','reference'):
        kwargs={'geometry_factory':Geometry,'original_native_period_s':.04,
            'target_reference':{'servo_deg':[90]*6,'boot_id':'software-native','measured':change=='reference'}}
    with pytest.raises((ValueError,KeyError)):
        qualify_recorded_trace(encoded(trace),sources,state_dir=tmp_path,**kwargs)


def test_metrics_report_intent_discontinuity_without_attainment_or_sample_loss_inference():
    result=sequence_metrics([100,1000000100,2000000100,3000000100],[[0]*6,[1]*6,[4]*6,[9]*6])
    assert result['max_step_delta']==5 and result['max_velocity']==5
    assert result['max_acceleration']==2 and result['max_jerk']==0
    assert result['out_of_order_or_duplicate']==0
    assert 'not_attained_motion' in result['derivative_basis']
