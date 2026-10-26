"""Causal measured native6 features; no policy/device allocation in these tests."""
import hashlib

import numpy as np
import pytest

from explorer_shared.learning.act_policy import validate_observation
from explorer_shared.so101_station import LOW,HIGH


def observation(rgb):
    return dict(camera_id='fixture-camera',follower_calibration_sha256='b'*64,state_basis='measured_state',frame_id='fixture:1',frame_sha256='a'*64,
        decoded_rgb_sha256=hashlib.sha256(rgb.tobytes()).hexdigest(),frame_clock_domain='pi:boot',state_clock_domain='pi:boot',source_cutoff_clock_domain='pi:boot',
        frame_boot_id='boot',state_boot_id='boot',source_cutoff_boot_id='boot',frame_monotonic_ns=100,state_monotonic_ns=101,source_cutoff_monotonic_ns=102,
        source_cutoff_basis='native_observation_packet_created')


def validate(rgb,state,mask,metadata):
    return validate_observation(rgb,state,mask,metadata,camera_id='fixture-camera',calibration_sha256='b'*64,low=np.asarray(LOW),high=np.asarray(HIGH))


def test_actual_neutral_numeric_state_is_retained_but_missing_or_invalid_channels_never_zero_filled():
    rgb=np.zeros((24,32,3),np.uint8);state=np.zeros(6,np.float32);mask=np.ones(6,bool)
    _,actual=validate(rgb,state,mask,observation(rgb));np.testing.assert_array_equal(actual,state)
    with pytest.raises(ValueError,match='Unavailable'):validate(rgb,state,np.zeros(6,bool),observation(rgb))
    with pytest.raises(ValueError,match='numeric'):validate(rgb,['0']*6,mask,observation(rgb))
    with pytest.raises(ValueError,match='numeric'):validate(rgb,[False]*6,mask,observation(rgb))
    with pytest.raises(ValueError,match='numeric'):validate(rgb,[None]*6,mask,observation(rgb))


@pytest.mark.parametrize('field,value,message',[
    ('state_basis','command_estimate','measured-state'),('camera_id','other-camera','identity'),
    ('follower_calibration_sha256','c'*64,'identity'),('state_clock_domain','nx:otherclock','clocks/boot'),
    ('frame_boot_id','previous-boot','clocks/boot'),('frame_monotonic_ns',103,'noncausal'),
    ('source_cutoff_monotonic_ns',3_000_000_000,'noncausal'),('source_cutoff_monotonic_ns',True,'noncausal'),
    ('decoded_rgb_sha256','c'*64,'checksum'),('source_cutoff_basis','guessed_NX_wall_time','cutoff')])
def test_untrusted_clock_camera_calibration_masks_and_frame_mismatch_rejected(field,value,message):
    rgb=np.full((24,32,3),32,np.uint8);metadata=observation(rgb);metadata[field]=value
    with pytest.raises(ValueError,match=message):validate(rgb,np.zeros(6,np.float32),np.ones(6,bool),metadata)


def test_unbound_policy_is_rejected_before_any_prediction_callback():
    from explorer.so101_shadow import infer_shadow
    class ForbiddenPolicy:
        def predict(self,*args,**kwargs):raise AssertionError('untrusted object must never be invoked')
    with pytest.raises(ValueError,match='trusted server'):infer_shadow(ForbiddenPolicy(),None,None,valid_mask=None,observation={})
