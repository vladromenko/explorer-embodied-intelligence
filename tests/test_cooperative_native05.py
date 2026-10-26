"""Exact original metadata HTTP ABI, with no control/action endpoint callable."""
from copy import deepcopy
import json
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import threading
from urllib.request import Request,urlopen

import pytest

from explorer_shared.cooperative_native05 import NativeMetadataRecorder,public_capture_preparers


@pytest.fixture
def metadata_node():
    state={'giver':None,'receiver':None,'receiver_mode':'teleoperation','identity_fence':True};calls=[]
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def send(self,body):
            raw=json.dumps(body).encode();self.send_response(200);self.send_header('Content-Length',str(len(raw)))
            self.end_headers();self.wfile.write(raw)
        def do_GET(self):
            assert self.headers.get('Authorization')=='Bearer local-test-token-only';calls.append(('GET',self.path,None))
            if self.path=='/native-capture/capabilities':self.send({'metadata_skill_context_supported':True,'native_owner_ready':True,'action_dimension':9})
            elif self.path=='/native-capture/status':self.send({'active_recording_id':state['giver']})
            elif self.path=='/station/status':self.send({'mode':state['receiver_mode'],'recording_identity_fence':state['identity_fence'],
                'recording':{'episode_id':state['receiver']} if state['receiver'] else None})
            else:raise AssertionError('Unexpected source API')
        def do_POST(self):
            assert self.headers.get('Authorization')=='Bearer local-test-token-only'
            body=json.loads(self.rfile.read(int(self.headers['Content-Length'])));calls.append(('POST',self.path,body))
            assert self.path in ('/native-capture/record/start','/native-capture/record/stage','/native-capture/record/finish',
                '/station/record/start','/station/record/stage','/station/record/finish')
            role='giver' if self.path.startswith('/native-capture/') else 'receiver'
            if self.path.endswith('/start'):
                assert state[role] is None;state[role]=role+'-native-original'
                self.send({'recording_id' if role=='giver' else 'episode_id':state[role]})
            else:
                assert body.get('recording_id',body.get('episode_id'))==state[role]
                if self.path.endswith('/finish'):state[role]=None
                self.send({'accepted':True})
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler);threading.Thread(target=server.serve_forever,daemon=True).start()
    def request(method,path,body):
        req=Request('http://127.0.0.1:'+str(server.server_port)+path,method=method,
            headers={'Authorization':'Bearer local-test-token-only','Content-Type':'application/json'},
            data=json.dumps(body).encode() if body is not None else None)
        with urlopen(req,timeout=2) as response:return json.load(response)
    try:yield request,state,calls
    finally:server.shutdown();server.server_close()


@pytest.mark.parametrize('role',['giver','receiver'])
def test_authenticated_exact_native_metadata_start_stage_finish_no_control_calls(metadata_node,role):
    request,state,calls=metadata_node
    producer=NativeMetadataRecorder(role,request,lambda context:{'body':{'skill_context':context} if role=='giver' else context})
    reference=producer.record_start({'actual_pinned_source_context':'retained'})
    assert reference['metadata_only_no_control_started']
    producer.record_stage(reference,'native-phase',{'phase_id':'RECEIVER_GRASP_CHECK','cooperative_session_id':'session'})
    producer.record_finish(reference,{'status':'unknown'})
    posts=[call for call in calls if call[0]=='POST']
    assert len(posts)==3 and all('/record/' in path for _,path,_ in posts)
    assert posts[-1][2].get('recording_id',posts[-1][2].get('episode_id'))==role+'-native-original'
    if role=='receiver':
        assert posts[1][2]['phase_scope']=='coordinator_phase'
        assert posts[1][2]['coordinator_context']['phase_id']=='RECEIVER_GRASP_CHECK'


def test_old_receiver_recorder_and_new_record_id_block_stale_mutation(metadata_node):
    request,state,calls=metadata_node;producer=NativeMetadataRecorder('receiver',request,lambda context:{'body':context})
    state['identity_fence']=False
    with pytest.raises(ValueError,match='identity-fenced'):producer.record_start({})
    assert all(method=='GET' for method,_,_ in calls)
    state['identity_fence']=True;reference=producer.record_start({});state['receiver']='other-new-episode'
    with pytest.raises(ValueError,match='changed'):producer.record_finish(reference,{'status':'unknown'})
    assert calls[-1][0]=='GET'


def test_role_success_not_inferred_from_whole_protocol_and_native_control_never_started(metadata_node):
    request,state,calls=metadata_node;producer=NativeMetadataRecorder('receiver',request,lambda context:{'body':context})
    state['receiver_mode']='idle'
    with pytest.raises(ValueError,match='already user-observed'):producer.record_start({})
    state['receiver_mode']='teleoperation';reference=producer.record_start({})
    with pytest.raises(ValueError,match='operator outcome'):producer.record_finish(reference,{'status':'success','method':'coordinator_completed'})
    assert state['receiver']==reference['episode_id']


def test_public_capture_preparers_use_native_validators_and_original_source04_parent_context():
    calls=[]
    def session(store,skill,version,body):calls.append(('session',deepcopy(body)))
    def ros_prepare(store,context,**kwargs):calls.append(('prepare',deepcopy(context),kwargs));return {'capture_id':'actual'}, {'spec_sha256':'a'*64}
    def lab_prepare(store,skill,version,context):return {'record_start_body':{'project_id':'obsolete06',
        'skill_spec_sha256':context['skill_spec_sha256'],'context':{'variant_id':context['variant_id']}}}
    callbacks=public_capture_preparers(None,rosmaster_create_session=session,rosmaster_prepare_capture=ros_prepare,
        lab_create_session=session,lab_capture_context=lab_prepare)
    context={'project_id':'03','skill_id':'existing','skill_version':1,'skill_spec_sha256':'a'*64,'variant_id':'actual-existing-variant',
        'session_id':'explicit-native-session','scene_id':'actual-scene','split':'train','object_instance_id':'block',
        'input_mode':'so101_leader','cooperative_parent':{'session_id':'original05'},'cooperative_parent_session_id':'original05',
        'context':{'cooperative_parent_session_id':'original05'}}
    prepared=callbacks['giver'](context)
    assert calls[1][1]['input_mode']=='so101_leader_retarget' and calls[1][2]['project_id']=='03'
    assert prepared['body']['skill_context']['input_mode']=='so101_leader'
    prepared=callbacks['receiver'](context)
    assert prepared['body']['project_id']=='04' and prepared['body']['context']['cooperative_parent_session_id']=='original05'
    with pytest.raises(ValueError,match='existing native demonstration variant'):callbacks['giver']({**context,'variant_id':None})
