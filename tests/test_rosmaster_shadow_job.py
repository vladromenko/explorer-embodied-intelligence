"""Real sourcepacket/hash/profile gates +isolated dummy CPU predictor, no ROS."""
from copy import deepcopy
import hashlib
import json
import sys
import time

import httpx
import pytest

from explorer.store import Store
from explorer.rosmaster_shadow_job import shadow_request,run_rosmaster_shadow,PROFILE
from explorer_shared.embodied_episode import ACTION_CONTRACT_ID
from explorer_shared.contracts import ACTION_NAMES,canonical,digest
from test_rosmaster_observation import fixture as source_fixture


def fixture(tmp_path):
    store=Store(tmp_path/'state');folder=store.root/'models/original';folder.mkdir(parents=True)
    checkpoint=folder/'student_act';checkpoint.mkdir();(checkpoint/'dummy-weights').write_bytes(b'fixture-not-real-trained-weights')
    files={'dummy-weights':hashlib.sha256((checkpoint/'dummy-weights').read_bytes()).hexdigest()}
    lineage={'skill_id':'fixture-whole-native-skill','skill_version':1,'spec_sha256':'a'*64}
    model={'schema':'explorer_rosmaster_skill_model_v1','action_contract_id':ACTION_CONTRACT_ID,'action_names':list(ACTION_NAMES),
        'ACT9':{'state_dimension':106,'output_dimension':9},'source_kind':['physical'],
        'skill_id':lineage['skill_id'],'skill_version':1,'skill_spec_sha256':lineage['spec_sha256'],'weights_sha256':files}
    path=folder/'model_manifest.json';path.write_bytes(canonical(model))
    trained=store.create_job('rosmaster_skill_train','02',{'skill_workflow':lineage})
    store.complete_job(trained['id'],{'trained':True,'weights_changed':True,'save_reload_passed':True,'optimizer_steps':8,'device':'cpu',
        'model_manifest':str(path),'model_manifest_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
        'checkpoint_dir':str(checkpoint),'checkpoint_sha256':files})
    settings=shadow_request(store,trained['id']);job=store.create_job('rosmaster_skill_shadow','02',settings)
    return store,store.job(trained['id']),job,path,model


def test_request_metadata_no_torch_import_and_legacy_state9_is_explicitly_rejected(tmp_path):
    before=set(sys.modules);store,trained,job,path,model=fixture(tmp_path)
    assert 'torch' not in set(sys.modules)-before and job['config']['state_profile']==PROFILE and job['config']['device']=='cpu'
    model['ACT9']['state_dimension']=9;path.write_bytes(canonical(model))
    with store.connect() as db:
        result=trained['result'];result['model_manifest_sha256']=hashlib.sha256(path.read_bytes()).hexdigest()
        db.execute('UPDATE jobs SET result=? WHERE id=?',(json.dumps(result),trained['id']))
    with pytest.raises(ValueError,match='LEGACY_STATE9_NOT_INTERCHANGEABLE'):shadow_request(store,trained['id'])


def test_model_warmup_precedes_new_source_and_real_frozen_source_admission_no_control_requests(tmp_path):
    store,trained,job,path,model=fixture(tmp_path);source,native,sensors,jpeg,tick=source_fixture();events=[];packets={}
    class Model:
        def predict(self,observation,read,verify_observation):
            events.append('predict');admission=verify_observation(observation,model)
            assert admission['camera_rgb']['admitted'] and admission['preceding_command9']['admitted']
            assert not admission['joint_feedback']['admitted']
            assert read(observation['channels']['camera_rgb']['value']['source_file'])==jpeg
            return {'action9':[91]*6+[0]*3,'hardware_io':False,'simultaneous_9d':True,'phase_progress':{'state':'WAITING_FOR_INDEPENDENT_EVIDENCE'}}
    def loader(path):events.append('load');return Model()
    def handler(request):
        assert request.method=='GET' and request.headers['Authorization']=='Bearer private-fake-node-token'
        events.append(request.url.path)
        if request.url.path=='/native-policy/observation':
            packet=source.create();packets[packet['observation_id']]=packet;return httpx.Response(200,json=packet)
        identity=request.url.path.split('/')[3];raw,metadata=source.frame(identity)
        return httpx.Response(200,content=raw,headers={'X-Observation-ID':identity,'X-Frame-ID':metadata['frame_id'],
            'X-Boot-ID':metadata['boot_id'],'X-JPEG-SHA256':metadata['jpeg_sha256'],'X-Clock-Domain':metadata['clock_domain']})
    def factory(**kwargs):return httpx.Client(**kwargs,transport=httpx.MockTransport(handler))
    result=run_rosmaster_shadow(store,job,{'nodes':[{'id':'nano','host':'fake-node','port':8092,'token':'private-fake-node-token'}]},
        loader=loader,client_factory=factory)
    assert events[0]=='load' and events[-1]=='predict'
    assert result['model_loaded_before_fresh_source'] and result['state_dimension']==106 and len(result['action9'])==9
    assert not result['dispatch_performed'] and result['physical_control_requests_sent']==0 and not result['independent_full_task_success']
    assert (store.root/'jobs'/job['id']/'observation.json').is_file()


def test_cancel_before_worker_load_never_opens_client_or_loads_model(tmp_path):
    store,trained,job,path,model=fixture(tmp_path);store.update_job(job['id'],status='cancel_requested')
    def forbidden(*args,**kwargs):raise AssertionError('No model/source request aftercancel')
    with pytest.raises(RuntimeError,match='cancelled'):
        run_rosmaster_shadow(store,job,{'nodes':[{'id':'nano','host':'fake','port':1,'token':'unused'}]},client_factory=forbidden,loader=forbidden)


def test_actual_reference_blocker_survives_source_http_in_job_error_without_inference_or_control(tmp_path):
    from explorer_shared.rosmaster_observation import NativeObservationUnavailable
    from explorer.rosmaster_shadow_job import NativeShadowSourceBlocked
    store,trained,job,path,model=fixture(tmp_path);calls=[]
    record=NativeObservationUnavailable(409,b'{"detail":"Original reference invalid after EX_STALE; observed reference required"}','/explorer/native-policy/observation').record()
    def handler(request):
        calls.append((request.method,request.url.path));assert request.method=='GET'
        return httpx.Response(409,json=record)
    class Model:
        def predict(self,*args,**kwargs):raise AssertionError('Invalid reference must not become an inferred command state')
    with pytest.raises(NativeShadowSourceBlocked,match='NATIVE_REFERENCE_UNAVAILABLE.*EX_STALE') as error:
        run_rosmaster_shadow(store,job,{'nodes':[{'id':'nano','host':'fake','port':1,'token':'unused'}]},
            loader=lambda path:Model(),client_factory=lambda **kwargs:httpx.Client(**kwargs,transport=httpx.MockTransport(handler)))
    assert error.value.record==record and error.value.record['joint_feedback_required'] is False
    assert calls==[('GET','/native-policy/observation')] and not (store.root/'jobs'/job['id']/'prediction.json').exists()
