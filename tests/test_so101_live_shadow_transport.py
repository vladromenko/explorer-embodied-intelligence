"""Software cached camera/model fixtures only; no real camera, buses or action."""
from copy import deepcopy
import hashlib
import importlib
import io
import json
from types import SimpleNamespace

import httpx
from fastapi.testclient import TestClient
from PIL import Image
import pytest

from explorer.store import Store
from explorer.so101_shadow_job import shadow_request,run_so101_shadow
from explorer_shared.node_agent import CameraSource
from explorer_shared.so101_observation import SO101CachedObservation
from explorer_shared.so101_station import LOW,HIGH
from explorer_shared.learning.act_policy import validate_observation


def rig():
    clock=SimpleNamespace(value=2000)
    data=io.BytesIO();Image.new('RGB',(16,12),'blue').save(data,'JPEG')
    source=CameraSource('pi-overview','software cached source');source.publish(data.getvalue(),16,12,monotonic_ns=1000)
    status=dict(available=True,roles={'follower':dict(calibration_sha256='a'*64,verified_on_connected_hardware=True)},
        measurements=dict(follower=[0,0,0,0,0,50],monotonic_ns=1500,clock_domain='pi:boot'))
    station=SimpleNamespace(boot='boot',clock_domain='pi:boot',status=lambda:deepcopy(status))
    packets=SO101CachedObservation(source,station,clock_domain='pi:boot',boot_id='boot',clock=lambda:clock.value)
    return source,status,packets,clock


def test_source_packet_uses_only_existing_cache_and_pins_original_rgb_before_measurement():
    camera,status,packets,clock=rig();packet=packets.create();observation=packet['observation']
    raw,metadata=packets.frame(observation['observation_id'])
    assert raw==camera.frame.jpeg and metadata['frame_monotonic_ns']==1000
    assert observation['state_monotonic_ns']==1500 and observation['source_cutoff_monotonic_ns']==2000
    assert packet['valid_mask']==[True]*6 and not packet['hardware_commands_dispatched']
    camera.publish(b'\xff\xd8later-but-unrelated\xff\xd9',16,12,monotonic_ns=1750)
    assert packets.frame(observation['observation_id'])[0]==raw
    clock.value=2_000_002_001
    with pytest.raises(ValueError,match='expired'):packets.frame(observation['observation_id'])


@pytest.mark.parametrize('field,value',[('follower',[0,0,0,0,0,None]),('follower',[0,0,0,0,0,True]),('monotonic_ns',2500),('clock_domain','other-clock')])
def test_unavailable_noncausal_and_non_numeric_states_are_blocked_without_zero_fill(field,value):
    _,status,packets,_=rig();status['measurements'][field]=value
    with pytest.raises(ValueError):packets.create()
    assert not packets.pins


def model_store(tmp_path):
    store=Store(tmp_path/'state');training=store.create_job('so101_skill_train','04',{'backend':'act'})
    store.update_job(training['id'],status='completed',result=dict(backend='act',source_kind='SO101_RECORDED_DEMONSTRATIONS',
        trained=True,weights_changed=True,save_reload_passed=True,checkpoint_sha256={'software-fixture':'b'*64}))
    config=shadow_request(store,training['id']);job=store.create_job('so101_skill_shadow','04',config)
    return store,training,job


def test_isolated_worker_fetches_new_packet_after_loading_and_only_GETs_exact_frame(tmp_path):
    store,training,job=model_store(tmp_path);camera,_,packets,_=rig();calls=[];loaded=[]
    def transport(request):
        calls.append((request.method,request.url.path))
        assert request.headers['Authorization']=='Bearer software-fixture-node-token'
        if request.url.path=='/station/observation':return httpx.Response(200,json=packets.create())
        raw,meta=packets.frame(request.url.path.split('/')[3])
        return httpx.Response(200,content=raw,headers={'X-Observation-ID':meta['observation_id'],'X-Frame-ID':meta['frame_id'],'X-Boot-ID':meta['frame_boot_id'],'X-JPEG-SHA256':meta['frame_sha256']})
    def loader(store,model,**kwargs):loaded.append((model,kwargs,len(calls)));return object()
    def predict(policy,rgb,state,*,valid_mask,observation):
        validate_observation(rgb,state,valid_mask,observation,camera_id='pi-overview',calibration_sha256='a'*64,low=LOW,high=HIGH)
        return dict(hardware_commands_dispatched=False,physical_execution_authorized=False,predicted_native6=[0]*6,software_model_fixture=True)
    result=run_so101_shadow(store,job,{'nodes':[dict(id='pi',host='fixture',port=8091,token='software-fixture-node-token')]},
        client_factory=lambda **kwargs:httpx.Client(transport=httpx.MockTransport(transport),**kwargs),loader=loader,predictor=predict)
    assert loaded[0][2]==1 and len(calls)==3 and all(method=='GET' for method,path in calls)
    assert result['transport_freshness_verified_by_worker'] and not result['independent_full_task_success']
    assert set(result['artifacts'])=={'frame.jpg','observation.json','prediction.json'}
    assert (store.root/'jobs'/job['id']/'frame.jpg').read_bytes()==camera.frame.jpeg


def test_corrupt_pinned_frame_is_rejected_before_prediction(tmp_path):
    store,_,job=model_store(tmp_path);_,_,packets,_=rig()
    def transport(request):
        if request.url.path=='/station/observation':return httpx.Response(200,json=packets.create())
        return httpx.Response(200,content=b'not-the-pinned-jpeg',headers={'X-JPEG-SHA256':'a'*64})
    with pytest.raises(ValueError,match='SHA differs'):
        run_so101_shadow(store,job,{'nodes':[dict(id='pi',host='fixture',port=8091,token='software-fixture-node-token')]},
            client_factory=lambda **kwargs:httpx.Client(transport=httpx.MockTransport(transport),**kwargs),loader=lambda *a,**k:object(),
            predictor=lambda *a,**k:pytest.fail('Corrupt frame must not reach prediction'))


def test_api_has_no_model_import_and_queues_only_pinned_real_source_act_jobs(tmp_path,monkeypatch):
    module=importlib.import_module('explorer.app');store,training,_=model_store(tmp_path)
    monkeypatch.setattr(module,'STORE',store)
    monkeypatch.setattr(module,'CONFIG',dict(module.CONFIG,nodes=[],password_salt='fixture',password_hash=hashlib.pbkdf2_hmac('sha256',b'fixture',b'fixture',310000).hex()))
    with TestClient(module.app) as client:
        assert client.post('/api/lab/source-shadow',json={'model_job_id':training['id']}).status_code==401
        assert client.post('/api/login',json={'password':'fixture'}).status_code==200
        headers={'X-Explorer-CSRF':client.cookies['explorer_csrf']}
        assert client.post('/api/lab/source-shadow',json={'model_job_id':training['id']}).status_code==403
        response=client.post('/api/lab/source-shadow',json={'model_job_id':training['id']},headers=headers)
        assert response.status_code==200 and response.json()['kind']=='so101_skill_shadow'
        assert client.post('/api/lab/source-shadow',json={'model_job_id':training['id'],'dispatch':True},headers=headers).status_code==400
        old=store.job(training['id']);simulation=store.create_job('so101_skill_train','04',{'backend':'act'})
        store.update_job(simulation['id'],status='completed',result=dict(old['result'],source_kind='SIMULATION'))
        assert client.post('/api/lab/source-shadow',json={'model_job_id':simulation['id']},headers=headers).status_code==400
        assert store.job(training['id'])['result']==old['result']
