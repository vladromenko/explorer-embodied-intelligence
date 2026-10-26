"""Structural SOFTWARE model/cohort plus actual bounded httpx GET packets; no motors."""
from copy import deepcopy
import hashlib
import importlib
from io import BytesIO
import json
from pathlib import Path
import sys

import httpx
import numpy as np
from PIL import Image
import pytest
from fastapi.testclient import TestClient

from explorer import scheduler,so101_shadow_job as shadow,transfer_source,policy_runtime
from explorer_shared.so101_station import NAMES,UNITS
from explorer_shared.source_archive import canonical,sha
from test_transfer_source import fixture


def setup(tmp_path,monkeypatch,*,source_kind='SO101_RECORDED_DEMONSTRATIONS'):
    store,trained,admission,outer,view=fixture(tmp_path,'diffusion',source_kind=source_kind)
    monkeypatch.setattr(transfer_source,'_admission',admission)
    settings=shadow.shadow_request(store,trained['id']);job=store.create_job('so101_skill_shadow','04',settings)
    store.pin_job_policy_binding(job['id'],'a'*64);job=store.job(job['id'])
    monkeypatch.setattr(shadow,'_diffusion_worker_runtime',lambda actual:dict(binding_sha256='a'*64,executable='SOFTWARE_RUNTIME_FIXTURE',prefix='SOFTWARE'))
    return store,trained,job


class Packets:
    def __init__(self,calibration,*,changes=None,indices=None,callbacks=None):
        self.calibration,self.changes=calibration,changes or {};self.indices=indices or list(range(30));self.callbacks=callbacks or {}
        self.calls=[];self.raw={};self.count=0
    def packet(self,index):
        frame=self.indices[index];data=BytesIO();Image.new('RGB',(8,6),(frame*3%255,22,55)).save(data,'JPEG');jpeg=data.getvalue()
        rgb=np.asarray(Image.open(BytesIO(jpeg)).convert('RGB'));ident=f'{index:032x}'
        observation=dict(observation_id=ident,camera_id='SOFTWARE-camera',frame_id='native-frame-'+str(frame),
            frame_sha256=sha(jpeg),decoded_rgb_sha256=sha(rgb.tobytes()),width=8,height=6,
            follower_calibration_sha256=self.calibration,state_basis='measured_state',
            frame_clock_domain='pi:boot',state_clock_domain='pi:boot',source_cutoff_clock_domain='pi:boot',
            frame_boot_id='boot',state_boot_id='boot',source_cutoff_boot_id='boot',frame_monotonic_ns=100+frame*100,
            state_monotonic_ns=110+index*100,source_cutoff_monotonic_ns=120+index*100,source_cutoff_basis='native_observation_packet_created')
        packet=dict(schema='explorer_so101_cached_observation_v1',observation=observation,
            measured_native6=[0.,0.,0.,0.,0.,50.],valid_mask=[True]*6,names=list(NAMES),units=list(UNITS))
        if index in self.changes:self.changes[index](packet)
        self.raw[ident]=(jpeg,deepcopy(packet))
        return packet
    def handler(self,request):
        self.calls.append((request.method,request.url.path))
        assert request.method=='GET' and request.headers['authorization']=='Bearer SOFTWARE_FAKE_NODE_TOKEN'
        if request.url.path=='/station/observation':
            index=self.count;self.count+=1;packet=self.packet(index)
            if index in self.callbacks:self.callbacks[index]()
            return httpx.Response(200,json=packet)
        ident=request.url.path.split('/')[3];jpeg,packet=self.raw[ident];o=packet['observation']
        return httpx.Response(200,content=jpeg,headers={'X-Observation-ID':o['observation_id'],'X-Frame-ID':o['frame_id'],'X-Boot-ID':o['frame_boot_id'],'X-JPEG-SHA256':sha(jpeg)})
    def factory(self,**kwargs):return httpx.Client(transport=httpx.MockTransport(self.handler),**kwargs)


class Actor:
    def __init__(self,callback=None):self.inputs=[];self.callback=callback
    def predict_history(self,images,states,**kwargs):
        self.inputs.append((images.copy(),states.copy(),deepcopy(kwargs)))
        if self.callback:self.callback()
        return np.asarray([[float(i),0,0,0,0,50] for i in range(4)],np.float32)


def run(store,trained,job,*,changes=None,indices=None,callbacks=None,actor=None,clock=lambda:100):
    calibration=transfer_source._metadata(store,trained)['source_calibration_sha256']
    packets=Packets(calibration,changes=changes,indices=indices,callbacks=callbacks);actor=actor or Actor();loaded=[]
    def loader(binding,camera):loaded.append((binding,camera,len(packets.calls)));return actor
    result=shadow.run_so101_shadow(store,job,{'nodes':[dict(id='pi',host='fixture',port=8091,token='SOFTWARE_FAKE_NODE_TOKEN')]},
        client_factory=packets.factory,loader=loader,clock=clock)
    return result,packets,actor,loaded


def test_server_derives_DP_history2_from_current_saved_model_and_preserves_all4_outputs_and_originals(tmp_path,monkeypatch):
    store,trained,job=setup(tmp_path,monkeypatch);result,packets,actor,loaded=run(store,trained,job)
    assert loaded[0][2]==1 and loaded[0][0]['backend']=='diffusion' and loaded[0][0]['history_steps']==2
    assert len(packets.calls)==5 and all(method=='GET' for method,path in packets.calls)
    assert len(actor.inputs)==1 and actor.inputs[0][0].shape==(2,6,8,3) and actor.inputs[0][1].shape==(2,6)
    assert result['predicted_native6']==[[float(i),0,0,0,0,50] for i in range(4)]
    assert result['first_native6']==result['predicted_native6'][0] and result['full_chunk_preserved']
    assert result['hardware_control_requests_sent']==result['physical_control_requests_sent']==0
    assert not result['physical_execution_authorized'] and not result['cooperative_warm_qualified'] and not result['qualifies_object_source']
    folder=store.root/'jobs'/job['id'];assert len(result['source_frame_ids'])==len(set(result['source_frame_ids']))==2
    for index in range(2):
        raw=(folder/f'observations/{index:02d}.json').read_bytes();packet=json.loads(raw);jpeg=(folder/f'observations/{index:02d}.jpg').read_bytes()
        assert jpeg==packets.raw[packet['observation']['observation_id']][0]
        assert sha(jpeg)==packet['observation']['frame_sha256']
    assert (folder/'frame.jpg').read_bytes()==(folder/'observations/01.jpg').read_bytes()
    assert json.loads((folder/'prediction.json').read_bytes())['predicted_native6']==result['predicted_native6']


def test_SIM_cohort_is_never_promoted_to_live_source_even_with_valid_technical_saved_profile(tmp_path,monkeypatch):
    store,trained,admission,*_=fixture(tmp_path,'diffusion',source_kind='SIMULATION');monkeypatch.setattr(transfer_source,'_admission',admission)
    with pytest.raises(ValueError,match='SIMULATION is offline only'):shadow.shadow_request(store,trained['id'])


@pytest.mark.parametrize('change',[
    lambda p:p['observation'].update(follower_calibration_sha256='0'*64),
    lambda p:p['observation'].update(frame_monotonic_ns=10**15),
    lambda p:p['observation'].update(state_clock_domain='other-clock'),
    lambda p:p['observation'].update(frame_boot_id='another-boot'),
    lambda p:p.update(valid_mask=[False]*6),
    lambda p:p.update(names=list(reversed(NAMES))),
])
def test_noncausal_cross_clock_calibration_mask_and_action_order_rejected_before_actor(tmp_path,monkeypatch,change):
    store,trained,job=setup(tmp_path,monkeypatch);actor=Actor()
    with pytest.raises(ValueError):run(store,trained,job,changes={2:change},actor=actor)
    assert actor.inputs==[] and (store.root/'jobs'/job['id']/'shadow-error.json').exists()


def test_repeated_camera_frame_is_retained_excluded_and_actual_new_frame_forms_history(tmp_path,monkeypatch):
    store,trained,job=setup(tmp_path,monkeypatch);result,packets,actor,_=run(store,trained,job,indices=[0,1,1,2]+list(range(4,30)))
    assert result['history_indices']==[0,2] and len(packets.calls)==7
    selection=json.loads((store.root/'jobs'/job['id']/'history-selection.json').read_bytes())
    assert selection['polls'][1]['accepted_for_history'] is False
    assert (store.root/'jobs'/job['id']/'observations/01.jpg').exists() and len(actor.inputs)==1


def test_backward_measured_state_time_is_rejected_even_with_newer_RGB_and_cutoff(tmp_path,monkeypatch):
    store,trained,job=setup(tmp_path,monkeypatch);actor=Actor()
    changes={1:lambda p:p['observation'].update(frame_monotonic_ns=200,state_monotonic_ns=1000,source_cutoff_monotonic_ns=1100),
        2:lambda p:p['observation'].update(frame_monotonic_ns=300,state_monotonic_ns=500,source_cutoff_monotonic_ns=1200)}
    with pytest.raises(ValueError,match='measured-state history moved backwards'):run(store,trained,job,changes=changes,actor=actor)
    assert not actor.inputs


@pytest.mark.parametrize('changed_value',[False,True])
def test_equal_state_tick_can_reuse_same_real_measurement_but_not_different_values(tmp_path,monkeypatch,changed_value):
    store,trained,job=setup(tmp_path,monkeypatch);actor=Actor()
    def second(packet):
        packet['observation'].update(state_monotonic_ns=310,source_cutoff_monotonic_ns=420)
        if changed_value:packet['measured_native6'][0]=1.
    changes={1:lambda p:p['observation'].update(state_monotonic_ns=310,source_cutoff_monotonic_ns=400),2:second}
    if changed_value:
        with pytest.raises(ValueError,match='timestamp cannot identify different'):run(store,trained,job,changes=changes,actor=actor)
        assert not actor.inputs
    else:
        result,_,_,_=run(store,trained,job,changes=changes,actor=actor)
        assert result['history_steps']==2 and len(actor.inputs)==1


def test_cancel_after_model_load_prevents_history_HTTP_and_actor(tmp_path,monkeypatch):
    store,trained,job=setup(tmp_path,monkeypatch);calibration=transfer_source._metadata(store,trained)['source_calibration_sha256'];packets=Packets(calibration);actor=Actor()
    def loader(*args):store.request_job_operation(job['id'],'cancel');return actor
    with pytest.raises(RuntimeError,match='cancelled'):
        shadow.run_so101_shadow(store,job,{'nodes':[dict(id='pi',host='fixture',port=8091,token='SOFTWARE_FAKE_NODE_TOKEN')]},client_factory=packets.factory,loader=loader)
    assert len(packets.calls)==1 and not actor.inputs


def test_cancel_during_inference_retains_both_originals_but_never_publishes_prediction(tmp_path,monkeypatch):
    store,trained,job=setup(tmp_path,monkeypatch);actor=Actor(callback=lambda:store.request_job_operation(job['id'],'cancel'))
    with pytest.raises(RuntimeError,match='cancelled'):run(store,trained,job,actor=actor)
    folder=store.root/'jobs'/job['id'];assert len(actor.inputs)==1 and (folder/'observations/01.jpg').exists() and not (folder/'prediction.json').exists()
    assert len(json.loads((folder/'raw-source-prediction.json').read_bytes())['predicted_native6'])==4


def test_expired_first_history_frame_cannot_be_revalidated_by_fresh_second_packet(tmp_path,monkeypatch):
    store,trained,job=setup(tmp_path,monkeypatch);tick=[100]
    def age():tick[0]+=2_000_000_001
    with pytest.raises(ValueError,match='expired'):run(store,trained,job,callbacks={2:age},clock=lambda:tick[0])


def test_DP_scheduler_uses_same_qualified_interpreter_and_pins_or_rejects_binding(tmp_path,monkeypatch):
    store,trained,job=setup(tmp_path,monkeypatch);release=tmp_path/'immutable-source'
    monkeypatch.setattr(policy_runtime,'qualified_diffusion_runtime',lambda actual:dict(executable='/qualified/dp/bin/python',binding_sha256='a'*64))
    assert scheduler.executable_for(job,release,store)=='/qualified/dp/bin/python'
    wrong=store.create_job('so101_skill_shadow','04',dict(backend='diffusion',_policy_runtime_binding_sha256='b'*64))
    with pytest.raises(ValueError,match='Pinned policy runtime differs'):scheduler.executable_for(wrong,release,store)


@pytest.mark.parametrize('invalid',['binding','executable','prefix'])
def test_worker_itself_rejects_wrong_DP_interpreter_before_any_HTTP_or_actor(tmp_path,monkeypatch,invalid):
    store,trained,job=setup(tmp_path,monkeypatch);monkeypatch.undo()
    # Admission restored explicitly, production interpreter guard remains active.
    store,trained,admission,*_=fixture(tmp_path/'strict','diffusion',source_kind='SO101_RECORDED_DEMONSTRATIONS');monkeypatch.setattr(transfer_source,'_admission',admission)
    settings=shadow.shadow_request(store,trained['id']);job=store.create_job('so101_skill_shadow','04',settings);store.pin_job_policy_binding(job['id'],'a'*64);job=store.job(job['id'])
    selected=dict(executable=sys.executable,binding_sha256='a'*64)
    if invalid=='binding':selected['binding_sha256']='b'*64
    elif invalid=='executable':selected['executable']='/another/runtime/bin/python'
    else:monkeypatch.setattr(shadow.sys,'prefix','/another/runtime')
    monkeypatch.setattr(policy_runtime,'qualified_diffusion_runtime',lambda:selected)
    with pytest.raises(ValueError,match='qualified Diffusion interpreter'):
        shadow.run_so101_shadow(store,job,{'nodes':[]},client_factory=lambda **kwargs:pytest.fail('HTTP before interpreter admission'),loader=lambda *args:pytest.fail('Actor before interpreter admission'))


def test_slow_drip_GET_checks_deadline_between_chunks_before_buffering_whole_response(tmp_path,monkeypatch):
    store,trained,job=setup(tmp_path,monkeypatch);tick=[0];yielded=[]
    class Drip(httpx.SyncByteStream):
        def __iter__(self):
            for index in range(10):
                tick[0]+=1_000_000_000;yielded.append(index);yield b' '
    def handler(request):assert request.method=='GET';return httpx.Response(200,stream=Drip())
    with pytest.raises(ValueError,match='streaming transport expired'):
        shadow.run_so101_shadow(store,job,{'nodes':[dict(id='pi',host='fixture',port=8091,token='SOFTWARE_FAKE_NODE_TOKEN')]},
            client_factory=lambda **kwargs:httpx.Client(transport=httpx.MockTransport(handler),**kwargs),clock=lambda:tick[0],loader=lambda *args:pytest.fail('No model load after expired source'))
    assert len(yielded)==3


def test_public_API_still_accepts_only_authenticated_model_ID_and_derives_DP_without_loading_actor(tmp_path,monkeypatch):
    store,trained,job=setup(tmp_path,monkeypatch);module=importlib.import_module('explorer.app')
    monkeypatch.setattr(module,'STORE',store)
    monkeypatch.setattr(module,'CONFIG',dict(module.CONFIG,nodes=[],password_salt='fixture',
        password_hash=hashlib.pbkdf2_hmac('sha256',b'fixture',b'fixture',310000).hex()))
    with TestClient(module.app) as client:
        body=dict(model_job_id=trained['id'])
        assert client.post('/api/lab/source-shadow',json=body).status_code==401
        assert client.post('/api/login',json={'password':'fixture'}).status_code==200
        assert client.post('/api/lab/source-shadow',json=body).status_code==403
        headers={'X-Explorer-CSRF':client.cookies['explorer_csrf']}
        response=client.post('/api/lab/source-shadow',json=body,headers=headers)
        assert response.status_code==200
        queued=store.job(response.json()['id']);assert queued['config']['backend']=='diffusion' and queued['config']['history_steps']==2
        for key,value in [('backend','act'),('actor_id','forged-user'),('physical_execution_authorized',True)]:
            assert client.post('/api/lab/source-shadow',json=dict(body,**{key:value}),headers=headers).status_code==400
