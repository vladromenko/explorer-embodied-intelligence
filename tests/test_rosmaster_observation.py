"""Actual source packet/checksums and typed current-clock admission; no devices."""
from copy import deepcopy
from io import BytesIO
import hashlib
import json

import numpy as np
from PIL import Image
import pytest

from explorer_shared.contracts import canonical
from explorer_shared.rosmaster_observation import RosmasterCachedObservation
from explorer.rosmaster_shadow_job import validate_packet


def fixture():
    tick=[10_000_000_000];stream=BytesIO();Image.new('RGB',(8,6),(12,34,56)).save(stream,format='JPEG');jpeg=stream.getvalue()
    native={'observation_id':'a'*32,'frame_id':'native-frame','jpeg_sha256':hashlib.sha256(jpeg).hexdigest(),
        'boot_id':'boot','clock_domain':'nano:boot','state_source':'command_estimate','monotonic_ns':tick[0]-20,
        'reference_read_native_ns':tick[0]-10,'reference_servo_deg':[90]*6,'command_reference9':[90]*6+[0]*3,
        'command_reference9_basis':'actual_original_publisher_and_base_request_as_of_snapshot','journal_pose_used_for_command9':False,
        'issued_context_receipts':{'arm':{'native_host_publication_returned':True,'pose_deg':[90]*6,
            'stamp':{'boot_id':'boot','clock_domain':'nano:boot','monotonic_ns':tick[0]-30}}},
        'base_reference_receipt':{'velocity':[0]*3,'stamp_ns':tick[0]-30,'receipt_id':'actual-base-zero'},
        'original_reference_header':{'native_guard_verified':True,'source_sha256':'f'*64},
        'timestamp_basis':'original_callback_receive_not_exposure'}
    class Reader:
        def observation(self):return deepcopy(native),canonical(native),jpeg
    class Sensors:
        def __init__(self):self.receipts=[]
        def observation_provider(self,receipt,frame):
            self.receipts.append(deepcopy(receipt))
            return {'channels':{'base_velocity':{'available':True,'present':True,'mask':True,
                'value':{'values':[.2,0,0],'units':['m/s','m/s','rad/s'],'state_source':'measured'},
                'stamp':{'boot_id':'boot','clock_domain':'nano:boot','monotonic_ns':tick[0]-25}}},
                'source_payloads':{},'diagnostics':{'localization_verified':False}}
    sensors=Sensors();source=RosmasterCachedObservation(Reader(),sensors,None,clock_domain='nano:boot',boot_id='boot',clock=lambda:tick[0])
    return source,native,sensors,jpeg,tick


def test_exact_prior_command9_real_zero_base_is_not_measured_odometry_and_all_missing_channels_explicit():
    source,native,sensors,jpeg,tick=fixture();packet=source.create()
    observation,payloads,admission=validate_packet(packet)
    assert observation['channels']['preceding_command9']['value']['values']==[90]*6+[0]*3
    assert observation['channels']['base_velocity']['value']['values']==[.2,0,0]  # never used to invent command9
    assert not admission['joint_feedback']['admitted'] and observation['channels']['joint_feedback']['value'] is None
    assert not admission['goal']['admitted'] and 'gripper' in admission and 'power_context' in admission
    assert source.frame(packet['observation_id'])[0]==jpeg
    assert sensors.receipts[0]['kind']=='observation_packet_created_not_action_dispatch'
    assert packet['hardware_commands_dispatched'] is False


@pytest.mark.parametrize('mutation',[lambda n:n.update(command_reference9=None),lambda n:n.update(boot_id='other'),
    lambda n:n.update(reference_read_native_ns=99_000_000_000),lambda n:n['base_reference_receipt'].update(receipt_id=None)])
def test_missing_unknown_future_or_other_boot_reference_is_never_reconstructed(mutation):
    source,native,sensors,jpeg,tick=fixture();mutation(native)
    with pytest.raises(ValueError):source.create()
    assert sensors.receipts==[]


def test_runtime_channel_mutation_or_asset_hash_change_fails_and_exact_pin_expires():
    source,native,sensors,jpeg,tick=fixture();packet=source.create()
    modified=deepcopy(packet);modified['observation']['channels']['preceding_command9']['value']['values'][6]=.9
    with pytest.raises(ValueError,match='source channel record'):validate_packet(modified)
    modified=deepcopy(packet);name=next(iter(modified['source_files_sha256']));modified['source_files_sha256'][name]='0'*64
    with pytest.raises(ValueError,match='SHA'):validate_packet(modified)
    tick[0]+=2_000_000_001
    with pytest.raises(ValueError,match='expired'):source.frame(packet['observation_id'])


def test_outer_native_receipt_cannot_be_spliced_onto_valid_channel_provenance():
    source,_,_,_,_=fixture();packet=source.create()
    packet['observation']['native_observation']['command_reference9'][0]=177
    with pytest.raises(ValueError,match='Whole original runtime context'):validate_packet(packet)


def test_actual_authenticated_local_node_serves_only_exact_pinned_cache_and_rejects_queries():
    import threading
    from http.server import ThreadingHTTPServer
    from types import SimpleNamespace
    from urllib.request import Request,urlopen
    from urllib.error import HTTPError
    from explorer_shared.node_agent import make_handler
    source,_,sensors,jpeg,_=fixture();token='local-fixture-token-not-hardware'
    agent=SimpleNamespace(token=token,rosmaster_observations=source)
    server=ThreadingHTTPServer(('127.0.0.1',0),make_handler(agent));server.daemon_threads=True
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    base='http://127.0.0.1:'+str(server.server_port)
    def get(path):return urlopen(Request(base+path,headers={'Authorization':'Bearer '+token}),timeout=2)
    try:
        with pytest.raises(HTTPError) as denied:urlopen(base+'/native-policy/observation',timeout=2)
        assert denied.value.code==401 and not sensors.receipts
        with get('/native-policy/observation') as response:packet=json.load(response)
        with get('/native-policy/observations/'+packet['observation_id']+'/frame') as response:
            assert response.read()==jpeg and response.headers['X-JPEG-SHA256']==hashlib.sha256(jpeg).hexdigest()
        with pytest.raises(HTTPError):get('/native-policy/observation?latest=true')
        assert len(sensors.receipts)==1
    finally:
        server.shutdown();server.server_close();thread.join(timeout=2)


@pytest.mark.parametrize('status,detail,code',[
    (409,'New observed factory reference required after native telemetry fault','NATIVE_REFERENCE_UNAVAILABLE'),
    (409,'ACTUAL_ISSUED_ARM_AND_BASE_CONTEXT_NOT_AVAILABLE_OR_PUBLICATION_INFLIGHT','NATIVE_COMMAND_CONTEXT_UNAVAILABLE'),
    (401,'Authentication required','NATIVE_SOURCE_AUTHENTICATION_REJECTED'),
])
def test_original_http_error_retains_bounded_actual_reason_instead_of_collapsing_to_transport(tmp_path,status,detail,code):
    from types import SimpleNamespace
    from urllib.error import HTTPError
    from explorer_shared.rosmaster_observation import OriginalObservationReader,NativeObservationUnavailable
    (tmp_path/'config').mkdir();(tmp_path/'config/access_token').write_text('private-fake-token')
    reader=OriginalObservationReader(SimpleNamespace(root=tmp_path));raw=canonical({'detail':detail});calls=[]
    def reject(request,timeout):
        calls.append(request)
        assert request.get_method()=='GET' and request.headers['Authorization']=='Bearer private-fake-token'
        raise HTTPError(request.full_url,status,'Conflict',{},BytesIO(raw))
    reader.opener=reject
    with pytest.raises(NativeObservationUnavailable) as caught:reader.observation()
    result=caught.value.record()
    assert result['code']==code and result['reason']==detail and result['source_response_sha256']==hashlib.sha256(raw).hexdigest()
    assert result['source_http_response_received'] and not result['joint_feedback_required'] and result['physical_commands_sent']==0
    assert len(calls)==1


def test_status_checks_only_original_metadata_reference_and_preserves_fault_without_new_pinned_packet(tmp_path):
    from types import SimpleNamespace
    from urllib.error import HTTPError,URLError
    from explorer_shared.rosmaster_observation import OriginalObservationReader
    (tmp_path/'config').mkdir();(tmp_path/'config/access_token').write_text('private-fake-token')
    reader=OriginalObservationReader(SimpleNamespace(root=tmp_path));calls=[]
    def response(request,timeout):
        calls.append(request.full_url)
        if request.full_url.endswith('/cooperative/status'):return BytesIO(canonical({'schema':'actual-owner-status'}))
        assert request.full_url.endswith('/retarget/reference')
        raise HTTPError(request.full_url,409,'Conflict',{},BytesIO(canonical({'detail':'New observed factory reference required after fault'})))
    reader.opener=response;result=reader.status()
    assert result['state']=='BLOCKED_REFERENCE' and len(calls)==2
    assert not any('/observation' in path for path in calls)
    def no_transport(*args,**kwargs):raise URLError('connection refused')
    reader.opener=no_transport;result=reader.status()
    assert result['state']=='TRANSPORT_UNAVAILABLE' and not result['source_http_response_received']


def test_actual_node_http_propagates_source_reference_fault_as_typed409_and_status_is_readonly(tmp_path):
    import threading
    from types import SimpleNamespace
    from http.server import ThreadingHTTPServer
    from urllib.request import Request,urlopen
    from urllib.error import HTTPError
    from explorer_shared.node_agent import make_handler
    from explorer_shared.rosmaster_observation import OriginalObservationReader
    (tmp_path/'config').mkdir();(tmp_path/'config/access_token').write_text('private-original-fixture-token')
    reader=OriginalObservationReader(SimpleNamespace(root=tmp_path));seen=[]
    def original(request,timeout):
        seen.append(request.full_url);assert request.get_method()=='GET'
        if request.full_url.endswith('/cooperative/status'):return BytesIO(canonical({'schema':'actual-native-owner-fixture'}))
        raise HTTPError(request.full_url,409,'Conflict',{},BytesIO(canonical({'detail':'New observed factory reference required after fault'})))
    reader.opener=original;source,_,sensors,_,_=fixture();source.reader=reader;token='private-node-fixture-token'
    server=ThreadingHTTPServer(('127.0.0.1',0),make_handler(SimpleNamespace(token=token,rosmaster_observations=source)))
    server.daemon_threads=True;thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    def get(path):return urlopen(Request('http://127.0.0.1:'+str(server.server_port)+path,headers={'Authorization':'Bearer '+token}),timeout=2)
    try:
        with get('/native-policy/status') as response:assert json.load(response)['state']=='BLOCKED_REFERENCE'
        with pytest.raises(HTTPError) as rejected:get('/native-policy/observation')
        assert rejected.value.code==409 and json.load(rejected.value)['code']=='NATIVE_REFERENCE_UNAVAILABLE'
        assert not sensors.receipts and len(seen)==3 and not source.pins
    finally:
        server.shutdown();server.server_close();thread.join(timeout=2)
