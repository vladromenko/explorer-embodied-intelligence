import numpy as np
import pytest

from explorer_shared.learning.sim_temporal_route import increment_targets,decode_increment9,ElapsedNativeClock,TemporalChunkDispatcher


def test_actual_dispatch_increment_roundtrip_all9_and_rate_continuity_rejection():
    velocity=[60,48,60,60,90,72];period=.05
    prior=np.array([[90,90,90,90,90,30,0,0,0],[91,88,90,89,90,33,0,0,0]],float)
    actual=np.array([[91,88,90,89,90,33,0,0,0],[90,88,91,89,90,36,0,0,0]],float)
    labels=increment_targets(prior,actual,velocity6=velocity,period_s=period)
    np.testing.assert_allclose([decode_increment9(value,state,velocity6=velocity,period_s=period) for value,state in zip(labels,prior)],actual,atol=1e-6)
    assert labels.shape==(2,9) and np.array_equal(labels[:,6:],np.zeros((2,3)))
    discontinuous=prior.copy();discontinuous[1,0]+=.05
    with pytest.raises(ValueError,match='previous dispatch'):increment_targets(discontinuous,actual,velocity6=velocity,period_s=period)
    changed=actual.copy();changed[0,0]+=20
    with pytest.raises(ValueError,match='original rate'):increment_targets(prior,changed,velocity6=velocity,period_s=period)


def test_clock_disambiguates_identical_current_inputs_using_only_observed_past_not_phase_or_future():
    clock=ElapsedNativeClock(40);stamp=lambda t:dict(boot_id='sim',clock_domain='sim',monotonic_ns=t)
    first,metadata=clock.observe(stamp(200000000));later,other=clock.observe(stamp(1050000000))
    assert first==0 and later>first and other['elapsed_s']==.85 and other['mask'] is True
    assert metadata['future_or_phase_input'] is False
    with pytest.raises(ValueError,match='stale/reversed'):clock.observe(stamp(1050000000))
    with pytest.raises(ValueError,match='horizon'):clock.observe(stamp(41000000000))
    with pytest.raises(ValueError,match='stale/reversed'):clock.observe(dict(boot_id='new',clock_domain='new',monotonic_ns=200000000))


@pytest.mark.parametrize('cell',['ZERO_ABS','ELAPSED_ABS','ZERO_INCREMENT','ELAPSED_INCREMENT'])
def test_four_cells_same_state10_boundary_fixed_chunk8_and_true_clock_availability(cell):
    calls=[]
    def predict(image,state):calls.append((image,state.copy()));return np.zeros((16,9))
    rate={'velocity_deg_s':[48,48,48,60,60,72],'period_s':.05}
    dispatcher=TemporalChunkDispatcher(predict,cell=cell,image_size=8,rate_profile=rate,horizon_s=40)
    prior=np.array([90,90,90,90,90,30,0,0,0.]);rgb=np.full((12,16,3),107,np.uint8)
    for index in range(9):
        raw,metadata=dispatcher.next(rgb,prior,dict(boot_id='sim',clock_domain='sim',monotonic_ns=200000000+index*50000000))
        if 'INCREMENT' in cell:np.testing.assert_array_equal(raw,prior)
        assert metadata['clock_sensor_available'] and metadata['clock_availability_mask']
        assert metadata['new_neural_prediction']==(index%8==0)
    assert len(calls)==2 and calls[0][1].shape==(10,) and calls[0][1][-1]==0
    assert (calls[1][1][-1]>0)==cell.startswith('ELAPSED')
    assert metadata['full_normalized_neural_chunk9']==np.zeros((16,9)).tolist()
