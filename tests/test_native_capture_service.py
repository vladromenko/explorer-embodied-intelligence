import json
import threading
from http.server import ThreadingHTTPServer
from types import SimpleNamespace
from urllib.request import Request,urlopen
from urllib.error import HTTPError

import pytest

from explorer_shared.native_capture import NativeCaptureWriter
from explorer_shared.native_capture_service import NativeCaptureService
from explorer_shared.native_stream03_client import OriginalNativeStreamReader
from explorer_shared.node_agent import make_handler
from test_native_capture import setup,context,publication,outcomes,encoded


def test_source_service_is_get_only_and_finish_drains_inflight_without_including_later_commands(tmp_path):
    old,tap,camera,clock,_,initial=setup(tmp_path/'initial');old.close()
    writer=NativeCaptureWriter(tmp_path/'live',camera,clock_domain='nano',boot_id='boot',clock=clock)
    entered,release=threading.Event(),threading.Event();calls=[]
    class Reader:
        def status(self):
            calls.append('GET-status');return {'schema':'explorer_native_retarget_mux_v1'}
        def reference(self):
            calls.append('GET-reference');return {'measured':False}
        def dispatch_source_packet(self,cursor):
            calls.append('GET-dispatch-'+str(cursor))
            if cursor==0:
                return {'payload':json.loads(initial),'source_bytes':initial}
            entered.set();assert release.wait(2.)
            raw=encoded(tap.since(cursor));return {'payload':json.loads(raw),'source_bytes':raw}
    service=NativeCaptureService(writer,Reader(),clock=clock,poll_s=.01)
    try:
        source=context('02','keyboard');source['version']=source.pop('skill_version')
        source['spec_sha256']=source.pop('skill_spec_sha256')
        started=service.start(source);ident=started['recording_id']
        assert entered.wait(1)
        publication(tap,camera,clock,stamp=2250,mode='keyboard')
        clock.value=2500
        service.finish(ident,json.loads(outcomes(ident)))
        assert not writer.done.is_set() and service.status()['finish_pending']
        publication(tap,camera,clock,stamp=3250,mode='keyboard')
        release.set();assert writer.done.wait(3),service.status()
        result=json.loads((writer.folder/'manifest.json').read_bytes())
        assert result['admission']['admitted_for_dataset'],result['admission']['missing_facts']
        assert len(result['embodied_episode']['samples'])==1
        assert result['embodied_episode']['recording']['post_finish_native_receipts_retained_not_training_samples']==1
        assert result['embodied_episode']['recording']['finish_received_monotonic_ns']==2500
        assert all(item.startswith('GET-') for item in calls)
        ready=service.status()['ready_recordings']
        assert len(ready)==1 and ready[0]['project_id']=='02'
        assert not service.status()['legacy_ready_recording_ids_modified']
    finally:
        release.set();service.close()


def test_missing_new_native_mount_never_falls_back_to_legacy_capture_or_commands(tmp_path):
    old,tap,camera,clock,_,initial=setup(tmp_path/'initial');old.close()
    writer=NativeCaptureWriter(tmp_path/'live',camera,clock_domain='nano',boot_id='boot',clock=clock)
    class Missing:
        def status(self):
            raise RuntimeError('HTTP404 original native tap not mounted')
        def reference(self):
            pytest.fail('No source-reference or command fallback allowed')
    service=NativeCaptureService(writer,Missing(),clock=clock)
    try:
        assert not service.capabilities()['native_owner_ready']
        with pytest.raises(RuntimeError,match='NOT_MOUNTED'):
            service.start(context())
        assert writer.status()['state']=='IDLE'
    finally:
        service.close()


def test_original_reader_cannot_mutate_even_through_inherited_methods(tmp_path):
    (tmp_path/'config').mkdir();(tmp_path/'config/access_token').write_text('mock-original-secret-token-012345')
    reader=OriginalNativeStreamReader(tmp_path,opener=lambda *args,**kwargs:pytest.fail('No HTTP request allowed'))
    for call in (lambda:reader.begin(observing=True,neutral=True),lambda:reader.resume('old',observing=True),
                 lambda:reader.stop('old'),lambda:reader.keyboard_base('old',{},observing=True)):
        with pytest.raises(PermissionError,match='GET-only'):
            call()


def test_authenticated_node_capture_routes_forward_metadata_only_and_keep03_out_of_legacy_ready_ids(tmp_path):
    old,tap,camera,clock,_,initial=setup(tmp_path/'initial');old.close()
    writer=NativeCaptureWriter(tmp_path/'live',camera,clock_domain='nano',boot_id='boot',clock=clock)
    calls=[]
    class Reader:
        def status(self):
            calls.append('GET-status');return {'schema':'explorer_native_retarget_mux_v1'}
        def reference(self):
            calls.append('GET-reference');return {'measured':False}
        def dispatch_source_packet(self,cursor):
            calls.append('GET-dispatch')
            raw=initial if cursor==0 else encoded(tap.since(cursor))
            return {'source_bytes':raw,'payload':json.loads(raw)}
    service=NativeCaptureService(writer,Reader(),clock=clock,poll_s=.01)
    token='mock-native-node-private-token-012345'
    legacy_ready=['old-observation-only-id']
    agent=SimpleNamespace(token=token,mode='nano',native_capture=service,legacy_bridge=None)
    server=ThreadingHTTPServer(('127.0.0.1',0),make_handler(agent));server.daemon_threads=True
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    origin='http://127.0.0.1:'+str(server.server_port)
    def request(path,body=None,auth=True):
        data=encoded(body) if body is not None else None
        headers={'Authorization':'Bearer '+token} if auth else {}
        with urlopen(Request(origin+path,data=data,headers=headers),timeout=3) as response:
            return json.load(response)
    try:
        with pytest.raises(HTTPError) as failure:
            request('/native-capture/record/start',{'skill_context':context()},False)
        assert failure.value.code==401 and calls==[]
        assert request('/native-capture/capabilities')['native_owner_ready']
        result=request('/native-capture/record/start',{'skill_context':context()});ident=result['recording_id']
        request('/native-capture/record/stage',{'recording_id':ident,'phase_id':'grasp_confirm'})
        request('/native-capture/record/finish',{'recording_id':ident})
        assert writer.done.wait(3),service.status()
        catalogue=request('/native-capture/recordings')['ready_recordings']
        assert len(catalogue)==1 and catalogue[0]['project_id']=='03' and catalogue[0]['input_mode']=='so101_leader'
        assert catalogue[0]['input_mode_basis'].endswith('not_native_proof')
        assert catalogue[0]['analysis_only']
        assert legacy_ready==['old-observation-only-id'] and all(item.startswith('GET-') for item in calls)
    finally:
        server.shutdown();server.server_close();thread.join(2);service.close()


@pytest.mark.parametrize('native_failure',[False,True])
def test_original_keyboard_wire_and_native_reply_survive_passive_input_journal(tmp_path,native_failure):
    old,tap,camera,clock,_,initial=setup(tmp_path/'initial');old.close()
    writer=NativeCaptureWriter(tmp_path/'live',camera,clock_domain='nano',boot_id='boot',clock=clock)
    class Reader:
        def status(self):return {'schema':'explorer_native_retarget_mux_v1'}
        def reference(self):return {'measured':False}
        def dispatch_source_packet(self,cursor):return {'source_bytes':initial,'payload':json.loads(initial)}
    service=NativeCaptureService(writer,Reader(),clock=clock,poll_s=.1)
    token='only-local-fixture-token';calls=[]
    def native(path,body):
        calls.append((path,body));clock.value=2400
        if native_failure:raise ValueError('fixture: original native refused input')
        return 200,{'state':'ACTIVE','actual_fixture_reply':True}
    agent=SimpleNamespace(token=token,mode='nano',native_capture=service,legacy_bridge=object(),mobile_capture=None,legacy_request=native)
    server=ThreadingHTTPServer(('127.0.0.1',0),make_handler(agent));server.daemon_threads=True
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
        started=service.start(context('02','keyboard'));ident=started['recording_id']
        payload={'source':'keyboard','keys':['KeyI'],'observing':True};clock.value=2300
        request=Request('http://127.0.0.1:'+str(server.server_port)+'/legacy/teleop/input',data=encoded(payload),headers={
            'Authorization':'Bearer '+token,'X-Explorer-Operator-Input':json.dumps({'input_origin':'explicit browser fixture','raw_axes':[.5,0,0,0]})})
        if native_failure:
            with pytest.raises(HTTPError) as failure:urlopen(request,timeout=3)
            assert failure.value.code==400
        else:
            with urlopen(request,timeout=3) as response:assert json.load(response)=={'state':'ACTIVE','actual_fixture_reply':True}
        service.finish(ident,{'recording_id':ident});assert writer.done.wait(3)
        assert calls==[('teleop/input',payload)]
        manifest=json.loads((writer.folder/'manifest.json').read_bytes());trace=manifest['embodied_episode']['operator_input_trace']
        event=json.loads((writer.folder/trace[0]['source_file']).read_bytes())
        assert event['operator_input']==payload and event['request_started_monotonic_ns']==2300 and event['response_returned_monotonic_ns']==2400
        assert event['accepted_action'] is None and not event['physical_attainment_inferred']
        if native_failure:assert event['native_response']['error_type']=='ValueError'
        else:assert event['native_response']=={'state':'ACTIVE','actual_fixture_reply':True}
    finally:server.shutdown();server.server_close();thread.join(2);service.close()


def test_idle_recording_keeps_real_cached_frames_and_sensor_payloads_without_inventing_actions(tmp_path):
    import time
    old,tap,camera,clock,_,initial=setup(tmp_path/'initial');old.close()
    camera.publish(b'\xff\xd8idle-original-fixture\xff\xd9',4,3,monotonic_ns=2100)
    frame=camera.before(2200);assert frame is not None;camera.latest=lambda:frame;clock.value=2300
    writer=NativeCaptureWriter(tmp_path/'live',camera,clock_domain='nano',boot_id='boot',clock=clock)
    calls=[]
    class Reader:
        def status(self):calls.append('GET-status');return {'schema':'explorer_native_retarget_mux_v1'}
        def reference(self):calls.append('GET-reference');return {'measured':False}
        def dispatch_source_packet(self,cursor):
            calls.append('GET-source');raw=initial if cursor==0 else encoded(tap.since(cursor))
            return {'source_bytes':raw,'payload':json.loads(raw)}
    service=NativeCaptureService(writer,Reader(),clock=clock,poll_s=.01,
        observation_reader=lambda cutoff,original:{'channels':{},'source_payloads':{'sources/raw-idle-ros.json':b'{"fixture_sensor":true}'}})
    try:
        ident=service.start(context('02','keyboard'))['recording_id'];deadline=time.monotonic()+2
        while writer.status()['frames_recorded']==0 and time.monotonic()<deadline:time.sleep(.005)
        assert writer.status()['frames_recorded']==1
        service.finish(ident,{'recording_id':ident,'operator_outcome':'unknown'});assert writer.done.wait(3)
        manifest=json.loads((writer.folder/'manifest.json').read_bytes());episode=manifest['embodied_episode']
        assert episode['samples']==[] and manifest['analysis_only'] and len(episode['observation_stream'])==1
        observation=episode['observation_stream'][0]
        assert observation['action'] is None and observation['no_native_action_inferred']
        assert (writer.folder/observation['camera_rgb']['source_file']).read_bytes()==frame.jpeg
        assert (writer.folder/'sources/raw-idle-ros.json').read_bytes()==b'{"fixture_sensor":true}'
        assert all(call.startswith('GET-') for call in calls)
    finally:service.close()
