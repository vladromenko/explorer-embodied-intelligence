"""Sealed04/05 DATA ACK through the original fake-input file writer; no control."""
from copy import deepcopy

import pytest

from explorer_shared.source_capture_ack import validate_capture_finish
from test_so101_capture_service import service
from test_cooperative_guarded_capture import cooperative


def sealed(service,purpose):
    capture,source,clock,request,_=cooperative(service) if purpose=='cooperative05' else service
    ident=capture.start(request,'fixture-owner')['recording_id'];start=clock();source.sample(start+110_000_000);clock.value=start+150_000_000
    manifest=capture.command('record/finish',{'recording_id':ident,'outcome':'unknown'},'fixture-owner')
    identity={'boot_id':source.station.boot,'clock_domain':source.station.clock_domain,
        'calibration_sha256':{role:source.station.profiles[role]['calibration_sha256'] for role in ('leader','follower')}}
    def ack(expected='unknown'):
        return validate_capture_finish('fixture-owner',request['record_start_body'],request['permission'],identity,ident,manifest,expected)
    return ident,source,manifest,ack


@pytest.mark.parametrize('purpose',['source04_demo','cooperative05'])
def test_both_consumers_acknowledge_exact_sealed_data_without_replaying_finish(service,purpose):
    ident,source,manifest,ack=sealed(service,purpose)
    assert source.station.record is None and source.station.mode=='teleoperation'
    assert ack()==ack()==ident
    assert source.station.mode=='teleoperation' and manifest['operator_outcome']=='unknown'
    with pytest.raises(ValueError):ack('success')


@pytest.mark.parametrize('fault',['recording','actor','epoch','source_boot','calibration','context','original_manifest','archive',
    'original_receipt','lifecycle_state','header_state','outcome','physics_claim','auto_import'])
def test_changed_sealed_originals_never_become_finish_ack_or_success(service,fault):
    ident,source,manifest,ack=sealed(service,'cooperative05')
    if fault=='recording':manifest['recording_id']='f'*32
    elif fault=='actor':manifest['lifecycle']['actor_id']='foreign'
    elif fault=='epoch':manifest['lifecycle']['native_control_epoch']='foreign'
    elif fault=='source_boot':manifest['source_boot_id']='foreign'
    elif fault=='calibration':manifest['lifecycle']['calibration_sha256']['leader']='f'*64
    elif fault=='context':manifest['source_context']['scene_id']='foreign'
    elif fault=='original_manifest':manifest['original_manifest']['sha256']='f'*64
    elif fault=='archive':manifest['original_archive']['sha256']='BAD'
    elif fault=='original_receipt':manifest['lifecycle']['original_finish_receipt']['episode_id']='f'*32
    elif fault=='lifecycle_state':manifest['lifecycle']['state']='UNCERTAIN'
    elif fault=='header_state':manifest['state']='RECORDING'
    elif fault=='outcome':manifest['operator_outcome']='success'
    elif fault=='physics_claim':manifest['physical_success_inferred']=True
    else:manifest['automatic_import_or_training']=True
    with pytest.raises(ValueError):ack()
