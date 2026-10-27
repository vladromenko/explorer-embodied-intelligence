"""Real Root→fixed facade→COMMON→original Station; only fake buses."""
from copy import deepcopy
import json
import threading

import pytest

from test_cooperative_service import fixture as central_fixture,session
from test_station_publication_gate import worker_rig
from test_station_hold_successor import authorize

ACTOR='trusted-owner'


def rig(tmp_path,monkeypatch):
    service,store,calls,active,contexts,client=central_fixture(tmp_path);row,spec=session(service)
    path=tmp_path/'original-worker';path.mkdir();binding=service.observed_setup.receiver_binding(row,spec)
    station,provider,selected,start,bus,samples,tick,StopAfter,leader=worker_rig(path,monkeypatch,binding_override=binding,actor=ACTOR)
    monkeypatch.setattr('explorer_shared.so101_station.time.monotonic_ns',lambda:tick[0])
    old=start();station.stop_event=StopAfter();station._work();wrapper=provider.cooperative_interlock
    bound=wrapper.dispatch('bind',{'grant_id':old['grant_id'],'expected_control_epoch':old['control_epoch'],'expected_revision':0},authenticated_actor=ACTOR)
    wrapper.dispatch('arm',{'grant_id':old['grant_id'],'expected_control_epoch':old['control_epoch'],
        'interlock_id':bound['interlock_id'],'expected_revision':bound['revision']},authenticated_actor=ACTOR)
    provider.command(old['grant_id'],binding,'stop',{'expected_control_epoch':old['control_epoch']})
    tick[0]+=100_000_000;station.stop_event=StopAfter();station._work();fresh=authorize(provider,binding,ACTOR);posts=[]
    with service.workflow.lock:
        actual=service.workflow._session(row['session_id']);actual.update(execution_domain='user_observed_physical_test',
            observed_test_binding={'actor_id':ACTOR,'context':service.observed_setup._context(row,spec),
                'native_grants':{'receiver':{key:deepcopy(old[key]) for key in ('grant_id','provider_process','actor_id','control_epoch')},
                    'giver':{'authorization_id':'unused-fake-giver'}}})
        service.workflow._save(actual);row=deepcopy(actual)
    def request(method,path,body):
        if method=='GET':
            if path=='/station/status':return json.loads(json.dumps(station.status()))
            if path=='/station/observed-test/status':return json.loads(json.dumps(provider.status()))
            if path=='/station/observed-test/cooperative/status':return json.loads(json.dumps(wrapper.status()))
            raise ValueError('No fake giver is present in this receiver-only fixture')
        posts.append((path,deepcopy(body)))
        if path=='/station/observed-test/command':return json.loads(json.dumps(provider.command(body['grant_id'],body['binding'],body['operation'],body['body'])))
        prefix='/station/observed-test/cooperative/'
        assert path.startswith(prefix);fields=deepcopy(body);actor=fields.pop('actor_id')
        return json.loads(json.dumps(wrapper.dispatch(path.removeprefix(prefix),fields,authenticated_actor=actor)))
    service._request=lambda role:request
    return service,row,spec,client,station,provider,wrapper,bus,tick,StopAfter,old,fresh,posts


def adopt(values):
    service,row,spec,client,station,provider,wrapper,bus,tick,StopAfter,old,fresh,posts=values
    helper=service.receiver_setup;choice=helper.refresh(row,spec,actor=ACTOR)['operations']['adopt_hold']['choices'][0]['choice_id']
    return helper.execute('adopt_hold',row,spec,{'receiver_setup_choice_id':choice,'observing':True,'user_observed_test_confirmed':True},actor=ACTOR)


def phase_body(service,row,spec):
    state=service.native_controls.refresh(row,spec,actor=ACTOR);item=state['roles']['receiver']['operations']['native_phase_activate']
    assert item['available'],state['roles']['receiver']
    return {'session_id':row['session_id'],'expected_epoch':row['epoch'],'role':'receiver',
        'native_control_choice_id':item['choice_id'],'observing':True,'user_observed_test_confirmed':True,'neutral':True}


def test_archived_new_user_hold_lineage_then_explicit_original_phase_control_without_auto_attach_or_prediction(tmp_path,monkeypatch):
    values=rig(tmp_path,monkeypatch);service,row,spec,client,station,provider,wrapper,bus,tick,StopAfter,old,fresh,posts=values
    try:
        before=deepcopy(bus.writes);result=adopt(values)
        current=service.workflow.snapshot(row['session_id']);assert current['observed_test_binding']==row['observed_test_binding']
        assert current['native_grant_successors']['receiver']['permission']['grant_id']==fresh['grant_id']
        assert bus.writes==before and not service.transports and wrapper.status()['parked_observation_only']
        controls=service.native_controls.refresh(row,spec,actor=ACTOR);assert not controls['roles']['receiver']['operations']['native_begin']['available']
        body=phase_body(service,row,spec)
        for extra in ({'phase_id':'spoof'},{'expected_revision':999},{'native6':[0]*6},{'grant_id':'spoof'}):
            with pytest.raises(ValueError,match='fixed native USER'):service.command('native_phase_activate',{**body,**extra},authenticated_actor=ACTOR)
        reply=service.command('native_phase_activate',body,authenticated_actor=ACTOR)['result']
        assert reply['native_receipt']['phase_activation_current'] and reply['native_receipt']['native_command_called']
        assert station.mode=='teleoperation' and wrapper.helper.armed and not wrapper.status()['parked_observation_only']
        assert not service.transports and station.last_command['control_epoch']!=station.control_epoch
        assert posts[-1][0].endswith('/cooperative/phase/activate') and posts[-1][1]['phase_id']==row['protocol_state']
        path,_=service.artifact('native-control',reply['control_receipt']['receipt_id'])
        assert json.loads(path.read_bytes())['source_response']==reply['native_receipt']
        with pytest.raises(ValueError,match='unconsumed|changed'):service.command('native_phase_activate',body,authenticated_actor=ACTOR)
        station.stop_event=StopAfter();station._work();assert station.last_command['control_epoch']==station.control_epoch
        assert wrapper.check_publication(station.control_epoch,station.mode,station.last_command['dispatched'])['allowed']
    finally:service.close();client.close()


def test_changed_or_unarchived_new_grant_never_becomes_root_permission_and_receipt_corruption_revokes_choices(tmp_path,monkeypatch):
    values=rig(tmp_path,monkeypatch);service,row,spec,client,station,provider,wrapper,bus,tick,StopAfter,old,fresh,posts=values
    try:
        state=service.native_controls.refresh(row,spec,actor=ACTOR)
        assert not state['roles']['receiver']['operations']['native_phase_activate']['available']
        assert 'permission' in state['roles']['receiver']['reason'] and not posts
        result=adopt(values);current=service.workflow.snapshot(row['session_id'])
        entry=current['native_grant_successors']['receiver'];path,_=service.artifact('receiver-metadata',result['metadata_receipt']['receipt_id'])
        original=path.read_bytes();path.write_bytes(original+b' ')
        state=service.native_controls.refresh(row,spec,actor=ACTOR)
        assert not any(item['available'] for item in state['roles']['receiver']['operations'].values())
        path.write_bytes(original);current['native_grant_successors']['receiver']['permission']['grant_id']='foreign'
        with pytest.raises(ValueError,match='anchor changed'):service.grant_lineage.expected('receiver',current,spec)
        assert wrapper.status()['parked_observation_only'] and station.mode=='idle'
    finally:service.close();client.close()


def test_global_stop_cancels_exact_new_phase_while_immutable_receipt_write_is_delayed(tmp_path,monkeypatch):
    import explorer.cooperative_native_receipts as module
    values=rig(tmp_path,monkeypatch);service,row,spec,client,station,provider,wrapper,bus,tick,StopAfter,old,fresh,posts=values
    entered,release,stopped=threading.Event(),threading.Event(),threading.Event();errors=[];stop_errors=[]
    adopt(values);body=phase_body(service,row,spec);write=module.write_original
    def blocked(path,raw):
        entered.set();assert release.wait(4);return write(path,raw)
    monkeypatch.setattr(module,'write_original',blocked)
    def activate():
        try:service.command('native_phase_activate',body,authenticated_actor=ACTOR)
        except Exception as error:errors.append(error)
    def stop():
        try:service.command('stop',{'session_id':row['session_id'],'expected_epoch':row['epoch']},authenticated_actor=ACTOR)
        except Exception as error:stop_errors.append(error)
        finally:stopped.set()
    operation=threading.Thread(target=activate);stopper=threading.Thread(target=stop)
    try:
        operation.start();assert entered.wait(2);assert station.mode=='teleoperation';owned_epoch=station.control_epoch
        stopper.start();assert stopped.wait(2);assert not stop_errors and station.mode=='idle'
        stops=[body for path,body in posts if path=='/station/observed-test/command' and body['operation']=='stop']
        assert stops and stops[-1]['body']['expected_control_epoch']==owned_epoch
        assert service.workflow.snapshot(row['session_id'])['status']=='STOPPED' and wrapper.helper.armed
        release.set();operation.join(3);stopper.join(3)
        assert not operation.is_alive() and errors and not service.transports
        assert not service.workflow.snapshot(row['session_id']).get('native_control_ledger')
        originals=list((service.root/'native-control').glob('*/receipt.json'));assert len(originals)==1
        saved=json.loads(originals[0].read_bytes())
        assert saved['source_response']['grant']['control_epoch']==owned_epoch
    finally:release.set();operation.join(3);service.close();client.close()
