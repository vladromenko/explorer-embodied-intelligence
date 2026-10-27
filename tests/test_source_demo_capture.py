"""Metadata-only source04 start/ACK/reconciliation races; no source IO or grants."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy

import pytest

from explorer import source_demo_capture as capture
from explorer import source_demo_workflow as flow
from explorer.store import Store
from explorer_shared.so101_station import _record_context
from test_source_demo_api import prepared


def rig(tmp_path):
    store=Store(tmp_path/'state');row=flow.prepare(store,prepared(store),'SOFTWARE-actor')
    grant=dict(grant_id='a'*32,binding=row['binding'],actor_id=row['actor_id'],native_mode='teleoperation',
        execution_permission=True,control_epoch='SOFTWARE-returned-epoch',boot_id='SOFTWARE-boot',clock_domain='SOFTWARE-domain',
        leader_calibration_sha256='b'*64,follower_calibration_sha256='c'*64)
    flow.record_return(store,row,'control_return',{'grant':grant,'status':{'mode':'teleoperation'}})
    body={'grant_id':grant['grant_id'],'expected_control_epoch':grant['control_epoch']}
    start=deepcopy(row['record_start_body']);start['context']=dict(start['context'],source04_demo_permission={
        'binding':row['binding'],'grant_id':grant['grant_id'],'control_epoch':grant['control_epoch']})
    context=_record_context(start);ident='d'*32
    packet=dict(schema='explorer_so101_source_capture_v1',recording_id=ident,recovery_required=False,closed=False,
        capture=dict(recording_id=ident,state='RECORDING',actor_id=row['actor_id'],source_context=context,
            native_control_epoch=grant['control_epoch'],source_boot_id=grant['boot_id'],source_clock_domain=grant['clock_domain'],
            calibration_sha256={'leader':grant['leader_calibration_sha256'],'follower':grant['follower_calibration_sha256']},
            original_start_receipt={'episode_id':ident}))
    native={'control_epoch':'FOREIGN_CURRENT_EPOCH_NEVER_ADOPT','mode':'idle',
        'roles':{role:{'calibration_sha256':grant[role+'_calibration_sha256']} for role in ('leader','follower')},
        'recording':{'episode_id':ident,'boot_id':grant['boot_id'],'clock_domain':grant['clock_domain'],
            'source_context':deepcopy(context),'demonstration_actor':row['actor_id']}}
    return store,row,body,packet,native,{'boot_id':grant['boot_id'],'clock_domain':grant['clock_domain']}


def test_reads_never_acknowledge_a_pending_source_and_one_intent_never_starts_twice(tmp_path):
    store,row,body,packet,native,stamp=rig(tmp_path)
    assert capture.projection(store,row)==dict(acknowledged_recording_id=None,capture_start_pending=False,control_authority_adopted=False)
    capture.begin(store,row,body)
    for _ in range(2):
        assert capture.projection(store,row)==dict(acknowledged_recording_id=None,capture_start_pending=True,control_authority_adopted=False)
        with pytest.raises(ValueError,match='pending'):capture.begin(store,row,body)
    reply=capture.acknowledge(store,row,packet,native=native,source_stamp=stamp)
    assert reply['recording_id']==packet['recording_id'] and not reply['control_authority_adopted'] and not reply['native_commands_dispatched']
    # Current native epoch may belong to someone else: this acknowledges DATA
    # only, with its historical start grant. No current authority is returned.
    assert native['control_epoch']=='FOREIGN_CURRENT_EPOCH_NEVER_ADOPT'
    assert capture.projection(Store(store.root),row)['acknowledged_recording_id']==packet['recording_id']
    assert capture.acknowledge(store,row,packet,native=native,source_stamp=stamp)==reply
    with store.connect() as db:assert len(capture._rows(db,row,'capture_return'))==1
    with pytest.raises(ValueError,match='already has'):capture.begin(store,row,body)


@pytest.mark.parametrize('fault',['uncertain','recovery','closed','actor','recording','epoch','boot','clock','calibration','context','receipt','binding'])
def test_foreign_or_uncertain_source_cannot_be_acknowledged(tmp_path,fault):
    store,row,body,packet,native,stamp=rig(tmp_path);capture.begin(store,row,body)
    if fault=='uncertain':packet['capture']['state']='UNCERTAIN'
    elif fault=='recovery':packet['recovery_required']=True
    elif fault=='closed':packet['closed']=True
    elif fault=='actor':packet['capture']['actor_id']='FOREIGN'
    elif fault=='recording':packet['recording_id']='e'*32
    elif fault=='epoch':packet['capture']['native_control_epoch']='FOREIGN'
    elif fault=='boot':packet['capture']['source_boot_id']='FOREIGN'
    elif fault=='clock':packet['capture']['source_clock_domain']='FOREIGN'
    elif fault=='calibration':packet['capture']['calibration_sha256']['leader']='e'*64
    elif fault=='context':packet['capture']['source_context']['scene_id']='FOREIGN'
    elif fault=='receipt':packet['capture']['original_start_receipt']['episode_id']='e'*32
    else:row=deepcopy(row);row['binding']['demonstration_id']='e'*32
    with pytest.raises(ValueError):capture.acknowledge(store,row,packet,native=native,source_stamp=stamp)
    assert capture.projection(store,row)['acknowledged_recording_id'] is None


@pytest.mark.parametrize('fault',['original_id','original_boot','original_context','original_actor','calibration','source_boot','source_clock'])
def test_explicit_reconciliation_requires_the_actual_original_writer_and_current_source_identity(tmp_path,fault):
    store,row,body,packet,native,stamp=rig(tmp_path);capture.begin(store,row,body)
    if fault=='original_id':native['recording']['episode_id']='e'*32
    elif fault=='original_boot':native['recording']['boot_id']='FOREIGN'
    elif fault=='original_context':native['recording']['source_context']['scene_id']='FOREIGN'
    elif fault=='original_actor':native['recording']['demonstration_actor']='FOREIGN'
    elif fault=='calibration':native['roles']['follower']['calibration_sha256']='e'*64
    elif fault=='source_boot':stamp['boot_id']='FOREIGN'
    else:stamp['clock_domain']='FOREIGN'
    with pytest.raises(ValueError):capture.acknowledge(store,row,packet,native=native,source_stamp=stamp)
    assert capture.projection(store,row)['capture_start_pending']


def test_saved_acknowledged_epoch_is_required_and_concurrent_start_intents_have_one_winner(tmp_path):
    store,row,body,*_=rig(tmp_path)
    with pytest.raises(ValueError):capture.begin(store,row,dict(body,expected_control_epoch='POLLED_OR_FOREIGN_EPOCH'))
    assert not capture.projection(store,row)['capture_start_pending']
    def begin():
        try:return capture.begin(store,row,body)
        except ValueError:return None
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(lambda _:begin(),range(2)))
    assert sum(result is not None for result in results)==1
