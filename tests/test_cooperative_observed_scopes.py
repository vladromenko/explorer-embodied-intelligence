"""Root lineage→authenticated node→original source metadata, fake buses only."""
from copy import deepcopy
from http.server import ThreadingHTTPServer
import json
import threading
from types import SimpleNamespace
from urllib.request import Request,urlopen

import pytest

from explorer_shared.contracts import canonical,digest
from explorer_shared.cooperative_receiver_interlock05 import observed_gripper_direction
from explorer_shared.node_agent import make_handler
from test_cooperative_native_sessions import NativePair,choice,execute
from test_cooperative_observed_service import command,grant
from test_cooperative_service import fixture as central_fixture,session
from test_cooperative_giver_phases import phase,new_permission
from test_cooperative_receiver_interlock05 import endpoint
from test_native_cooperative05 import message
from test_station_publication_gate import worker_rig

ACTOR='trusted-owner'


class Sources:
    def __init__(self,tmp_path,monkeypatch,*,policy=False):
        if policy:
            from test_cooperative_queued_shadows import configured as policy_fixture
            self.service,self.store,_,self.client,self.spec,self.row,_=policy_fixture(tmp_path,input_policy_roles=('giver',),pin_manual_models=False)
            def never_predict(*args,**kwargs):raise AssertionError('No inference belongs to native scope/CONTROL setup')
            self.service.native_controls._ready_prediction=lambda row,spec,role:never_predict
        else:self.service,self.store,_,_,_,self.client=central_fixture(tmp_path);self.row,self.spec=session(self.service)
        self.pair=NativePair(tmp_path,self.service);self.calls=[]
        folder=tmp_path/'actual-pi';folder.mkdir();binding=self.service.observed_setup.receiver_binding(self.row,self.spec)
        self.station,self.provider,_,_,self.bus,_,self.tick,self.StopAfter,_=worker_rig(folder,monkeypatch,binding_override=binding,actor=ACTOR)
        monkeypatch.setattr('explorer_shared.so101_station.time.monotonic_ns',lambda:self.tick[0])
        self.wrapper=self.provider.cooperative_interlock;self.token='PRIVATE-MOCK-NODE-SCOPE-TOKEN'
        agent=SimpleNamespace(mode='pi',token=self.token,station=self.station,station_observed=self.provider,mobile_records=None,
            legacy_bridge=None,lab_vision=None,arm_retarget=SimpleNamespace(status=lambda:{'state':'IDLE'}))
        self.server=ThreadingHTTPServer(('127.0.0.1',0),make_handler(agent));self.server.daemon_threads=True
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.origin='http://127.0.0.1:'+str(self.server.server_port)
        self.service._request=lambda role:self.pair.giver if role=='giver' else self.request
        self.service.observed_setup.requests={'giver':self.pair.giver,'receiver':self.request}
        old_binary=self.pair.binary
        def binary(role,path):
            if role=='giver':return old_binary(role,path)
            with urlopen(Request(self.origin+path,headers={'Authorization':'Bearer '+self.token}),timeout=3) as response:return response.read(),dict(response.headers)
        self.service.observed_setup.binary=binary
        self.pair.gate.clock=self.pair.mux.tap.clock;self.pair.gate.camera.clock=self.pair.mux.tap.clock
        grant(self.service,self.row,self.pair,'giver');grant(self.service,self.row,self.pair,'receiver')
        ready=command(self.service,self.row,'observed_refresh')
        self.row=command(self.service,self.row,'bind_observed_test',observed_choice_id=ready['bind_choice_id'])
        execute(self.service,'native_begin',choice(self.service,self.row,'giver','native_begin'))
        execute(self.service,'native_resume',choice(self.service,self.row,'giver','native_resume'))
        self.pair.arm.pub.publish(message(90));reference={'boot_id':'boot','servo_deg':[90]*6,'measured':False}
        self.pair.mux.tap.reference_header(reference,digest(reference),native_guard_verified=True,source_bytes=canonical(reference))
        self.pair.owner.reference=lambda:deepcopy(reference)
        old_resume=self.pair.teleop.resume
        def resume(source,observing):
            result=old_resume(source,observing);self.pair.teleop.lease=self.pair.native_tick[0]/1e9+.45;return result
        self.pair.teleop.resume=resume
        def invalidate(reason):
            result=self.pair.gate.revoke(reason);self.pair.owner.guard.revoke(reason);return result
        self.pair.mux.on_invalidate=invalidate
        phase(self.service,self.row,'native_phase_park');new_permission(self.service,self.row,self.pair);phase(self.service,self.row,'native_phase_adopt')
        if policy:phase(self.service,self.row,'native_phase_begin')
        started=execute(self.service,'native_begin',choice(self.service,self.row,'receiver','native_begin'))['native_receipt']['grant']
        self.station.stop_event=self.StopAfter();self.station._work()
        profile=observed_gripper_direction(endpoint('empty_open',80.),endpoint('empty_closed',20.))
        self.wrapper.profile_root.mkdir(exist_ok=True);(self.wrapper.profile_root/(profile['profile_sha256']+'.json')).write_bytes(canonical(profile))
        self.wrapper.profiles[profile['profile_sha256']]=profile
        before=self.wrapper.status();self.wrapper.dispatch('bind',{'grant_id':started['grant_id'],'expected_control_epoch':started['control_epoch'],
            'expected_revision':before['revision'],'direction_profile_id':profile['profile_sha256']},authenticated_actor=ACTOR)
        execute(self.service,'native_stop',choice(self.service,self.row,'receiver','native_stop'))
        self.tick[0]+=100_000_000;self.station.stop_event=self.StopAfter();self.station._work()
        grant(self.service,self.row,self.pair,'receiver')
        setup=command(self.service,self.row,'receiver_setup_refresh');selected=setup['operations']['adopt_hold']['choices'][0]['choice_id']
        command(self.service,self.row,'receiver_setup_adopt_hold',receiver_setup_choice_id=selected,observing=True,user_observed_test_confirmed=True)
        self.row=self.service.workflow.snapshot(self.row['session_id'])
    def request(self,method,path,body):
        self.calls.append((method,path,deepcopy(body)))
        with urlopen(Request(self.origin+path,method=method,headers={'Authorization':'Bearer '+self.token},
                data=None if body is None else json.dumps(body).encode()),timeout=3) as response:return json.load(response)
    def close(self):
        self.service.close();self.pair.close();self.client.close();self.server.shutdown();self.server.server_close();self.thread.join(3)


def test_constructor_cached_snapshots_and_render_are_inert_explicit_refresh_is_get_only(tmp_path,monkeypatch):
    sources=Sources(tmp_path,monkeypatch);service=sources.service
    try:
        before=(len(sources.calls),len(sources.pair.calls));writes=deepcopy(sources.bus.writes);drives=deepcopy(sources.pair.teleop.native_drive)
        assert not service.observed_scopes.snapshot(sources.row,sources.spec)['roles']['giver']['current']
        for _ in range(2):service.snapshot(sources.row['session_id'])
        assert before==(len(sources.calls),len(sources.pair.calls))
        result=service.observed_scopes.require_current(sources.row,sources.spec,actor=ACTOR)
        assert all(result['roles'][role]['current'] for role in ('giver','receiver')),result
        assert all(method=='GET' for method,_,_ in sources.calls[before[0]:])
        assert all(method=='GET' for _,method,_,_ in sources.pair.calls[before[1]:])
        assert not service._authority(sources.row)['explicit_user_observed_test_current']
        assert service._authority(sources.row)['dispatch_authority_by_role']=={'giver':False,'receiver':False}
        assert sources.bus.writes==writes and sources.pair.teleop.native_drive==drives and not service.transports
    finally:sources.close()


def test_both_parked_metadata_arm_uses_current_lineage_arms_receiver_first_and_never_promotes_motion(tmp_path,monkeypatch):
    sources=Sources(tmp_path,monkeypatch);service=sources.service
    try:
        initial=deepcopy(sources.row['observed_test_binding']);writes=deepcopy(sources.bus.writes);drives=deepcopy(sources.pair.teleop.native_drive)
        before=sources.wrapper.helper.snapshot_state();order=[];request=service._request
        def routed(role):
            callback=request(role)
            def invoke(method,path,body):
                if method=='POST' and path.endswith(('/held/arm','/phase/arm')):
                    order.append(role)
                    if role=='giver':sources.pair.native_tick[0]+=1  # distinct actual source cutoff, same physical context
                return callback(method,path,body)
            return invoke
        service._request=routed
        result=service._arm_guard(sources.row)
        assert result['both_original_publication_boundaries_armed'] and order==['receiver','giver']
        assert sources.wrapper.helper.armed and sources.pair.owner.guard.armed and sources.wrapper.helper.frozen==before['frozen']
        current=service.workflow.snapshot(sources.row['session_id']);assert current['observed_test_binding']==initial
        assert [item['operation'] for item in current['native_user_control_receipts']][-2:]==['parked_scope_arm']*2
        pointer=result['giver']['control_receipt'];path,_=service.artifact('native-control',pointer['receipt_id']);document=json.loads(path.read_bytes())
        assert document['intent_evidence']['source_scope']['scope_sha256']!=document['source_response']['source_scope']['scope_sha256']
        assert document['intent_evidence']['source_scope']['source_origin']==document['source_response']['source_scope']['source_origin']
        assert not service._authority(current)['explicit_user_observed_test_current'] and not service.transports
        assert service._authority(current)['dispatch_authority_by_role']=={'giver':False,'receiver':False}
        assert sources.bus.writes==writes and sources.pair.teleop.native_drive==drives
    finally:sources.close()


@pytest.mark.parametrize('role',['giver','receiver'])
def test_per_role_preview_requires_own_current_running_transport_and_other_current_parked_source(tmp_path,monkeypatch,role):
    sources=Sources(tmp_path,monkeypatch);service=sources.service
    try:
        service._arm_guard(sources.row)
        if role=='giver':phase(service,sources.row,'native_phase_resume')
        else:execute(service,'native_phase_activate',{**choice(service,sources.row,'receiver','native_phase_activate'),'neutral':True})
        execute(service,'native_attach',choice(service,sources.row,role,'native_attach'))
        current=service.workflow.snapshot(sources.row['session_id']);authority=service._authority(current);other='receiver' if role=='giver' else 'giver'
        assert authority['dispatch_authority_by_role'][role] and not authority['dispatch_authority_by_role'][other],authority
        assert authority['observation_scopes_current'][other] and not authority['observation_scopes_current'][role]
        assert authority['source_revalidation_required_before_native_dispatch'] and not authority['explicit_user_observed_test_current']
        assert service.transports.keys()=={(sources.row['session_id'],role)}
    finally:sources.close()


@pytest.mark.parametrize('changed',['giver_object','giver_origin','giver_model','giver_mask','receiver_actor','receiver_measurement','receiver_provider','attainment'])
def test_semantically_forged_source_scope_or_current_pair_never_becomes_current_even_with_rehashed_packet(tmp_path,monkeypatch,changed):
    sources=Sources(tmp_path,monkeypatch);service=sources.service;role='giver' if changed.startswith('giver') else 'receiver'
    old=service._request
    def request(selected):
        callback=old(selected)
        def invoke(method,path,body):
            value=callback(method,path,body)
            if selected==role:
                if path.endswith(('held-status','held/status')):
                    if changed=='giver_object':value['binding']['object_id']='foreign-object'
                    elif changed=='giver_origin':value['source_origin']['actual_issued']['command9'][0]=91.
                    elif changed=='giver_mask':value['measured_feedback_mask']=[0]*6
                    elif changed=='receiver_actor':value['actor_id']='foreign-actor'
                    elif changed=='receiver_measurement':value['measurement_stamp']['monotonic_ns']-=600_000_000
                    elif changed=='receiver_provider':value['grant']['provider_process']='foreign-provider'
                    elif changed=='attainment':value['stationarity_verified']=True
                    value['scope_sha256']=digest({key:item for key,item in value.items() if key!='scope_sha256'})
                if changed=='giver_model' and path.endswith('/cooperative/status'):value['policy']={'model_id':'foreign-model'}
            return value
        return invoke
    service._request=request
    try:
        result=service.observed_scopes.refresh(sources.row,sources.spec,actor=ACTOR)
        assert not result['roles'][role]['current'] and result['roles'][role]['reason']
        with pytest.raises(ValueError):service.observed_scopes.require_current(sources.row,sources.spec,(role,),actor=ACTOR)
        assert not service.transports and not service._authority(sources.row)['explicit_user_observed_test_current']
    finally:sources.close()


def test_immutable_original_artifact_and_initial_grant_anchor_cannot_be_replaced_by_rehashed_session_projection(tmp_path,monkeypatch):
    sources=Sources(tmp_path,monkeypatch);service=sources.service
    try:
        row=service.workflow.snapshot(sources.row['session_id']);entry=row['native_grant_successors']['giver'];original=deepcopy(row)
        entry['previous_entry_sha256']='f'*64;entry['entry_sha256']=digest({key:value for key,value in entry.items() if key!='entry_sha256'})
        service.workflow._save(row);before=len(sources.pair.calls)
        result=service.observed_scopes.refresh(row,sources.spec,actor=ACTOR)
        assert not result['roles']['giver']['current'] and len(sources.pair.calls)==before
        service.workflow._save(original);entry=original['native_grant_successors']['giver'];path,_=service.artifact('native-control',entry['receipt']['receipt_id'])
        raw=path.read_bytes();path.write_bytes(raw+b' ')
        result=service.observed_scopes.refresh(original,sources.spec,actor=ACTOR)
        assert not result['roles']['giver']['current'];path.write_bytes(raw)
        assert service.observed_scopes.require_current(original,sources.spec,actor=ACTOR)['roles']['giver']['current']
    finally:sources.close()


def test_cache_expiry_phase_change_and_actor_rejection_require_new_source_get_without_auto_refresh(tmp_path,monkeypatch):
    sources=Sources(tmp_path,monkeypatch);service=sources.service
    try:
        result=service.observed_scopes.require_current(sources.row,sources.spec,actor=ACTOR);before=(len(sources.calls),len(sources.pair.calls))
        with pytest.raises(ValueError):service.observed_scopes.refresh(sources.row,sources.spec,actor='foreign-operator')
        assert before==(len(sources.calls),len(sources.pair.calls))
        clock=service.observed_scopes.clock;now=clock();service.observed_scopes.clock=lambda:now+2_000_000_001
        assert not any(item['current'] for item in service.observed_scopes.snapshot(sources.row,sources.spec)['roles'].values())
        assert before==(len(sources.calls),len(sources.pair.calls));service.observed_scopes.clock=clock
        with service.workflow.lock:
            row=service.workflow.snapshot(sources.row['session_id']);row['protocol_state']='ROSMASTER_HOLDING';service.workflow._save(row)
        assert not any(item['current'] for item in service.observed_scopes.snapshot(row,sources.spec)['roles'].values())
        assert before==(len(sources.calls),len(sources.pair.calls))
    finally:sources.close()


def test_late_scope_get_after_stop_cannot_restore_cache_and_does_not_hold_workflow_stop_lock(tmp_path,monkeypatch):
    sources=Sources(tmp_path,monkeypatch);service=sources.service;entered,release,stopped=threading.Event(),threading.Event(),threading.Event();errors=[]
    old=service._request
    def request(role):
        callback=old(role)
        def invoke(method,path,body):
            result=callback(method,path,body)
            if role=='giver' and path.endswith('/phase/held-status'):entered.set();assert release.wait(5)
            return result
        return invoke
    service._request=request
    def refresh():
        try:service.observed_scopes.refresh(sources.row,sources.spec,actor=ACTOR)
        except Exception as error:errors.append(error)
    reader=threading.Thread(target=refresh)
    try:
        reader.start();assert entered.wait(2)
        stopper=threading.Thread(target=lambda:(command(service,sources.row,'stop'),stopped.set()));stopper.start();assert stopped.wait(2)
        release.set();reader.join(3);stopper.join(3)
        assert not reader.is_alive() and errors and service.workflow.snapshot(sources.row['session_id'])['status']=='STOPPED'
        assert not any(item['current'] for item in service.observed_scopes.snapshot(sources.row,sources.spec)['roles'].values())
    finally:release.set();reader.join(3);sources.close()


def test_stop_during_metadata_arm_artifact_io_leaves_latch_without_advancing_second_arm_or_authority(tmp_path,monkeypatch):
    import explorer.cooperative_native_receipts as module
    sources=Sources(tmp_path,monkeypatch);service=sources.service;entered,release,stopped=threading.Event(),threading.Event(),threading.Event();errors=[];write=module.write_original
    def delayed(path,raw):
        if json.loads(raw).get('operation')=='parked_scope_arm':entered.set();assert release.wait(5)
        return write(path,raw)
    monkeypatch.setattr(module,'write_original',delayed)
    def arm():
        try:service._arm_guard(sources.row)
        except Exception as error:errors.append(error)
    worker=threading.Thread(target=arm)
    try:
        worker.start();assert entered.wait(2);assert sources.wrapper.helper.armed and not sources.pair.owner.guard.armed
        stopper=threading.Thread(target=lambda:(command(service,sources.row,'stop'),stopped.set()));stopper.start();assert stopped.wait(2)
        release.set();worker.join(3);stopper.join(3)
        assert not worker.is_alive() and errors and sources.wrapper.helper.armed and not sources.pair.owner.guard.armed
        assert not service.transports and not service._authority(service.workflow.snapshot(sources.row['session_id']))['explicit_user_observed_test_current']
        originals=[json.loads(path.read_bytes()) for path in (service.root/'native-control').glob('*/receipt.json')]
        assert len([doc for doc in originals if doc['operation']=='parked_scope_arm'])==1
    finally:release.set();worker.join(3);sources.close()
