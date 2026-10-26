"""Durable temp job + real AF_UNIX + authenticated fake sensor HTTP; no ML/hardware."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import threading
import time

import httpx
import numpy as np
import pytest

from explorer.cooperative_policy_job import warm_request,run_cooperative_policy_session,current_source
from explorer_shared.cooperative_policy_actor import NativeActorClient,read_lease,lease_path
from explorer_shared.learning.checkpoint_control import worker_identity
from explorer_shared.learning.act_policy import validate_observation
from explorer_shared.so101_station import LOW,HIGH
from test_cooperative_queued_shadows import configured
from test_rosmaster_observation import fixture as nano_source
from test_so101_live_shadow_transport import rig as pi_source


def queued(tmp_path,*,roles=('giver','receiver')):
    service,store,calls,client,spec,session,models=configured(tmp_path,input_policy_roles=roles,pin_manual_models=False)
    settings=warm_request(store,session,spec)
    job=store.create_job('cooperative_policy_warm','05',settings);store.update_job(job['id'],status='running')
    store.update_schedule_runtime(job['id'],worker=worker_identity(),launch_nonce='c'*32,bootstrap_admitted=True)
    return service,store,calls,client,spec,session,store.job(job['id']),settings


def test_actual_same_worker_two_warmed_heads_get_only_inference_and_durable_cancel_revokes_ipc(tmp_path,monkeypatch):
    service,store,calls,client,spec,session,job,settings=queued(tmp_path);ready=threading.Event();result=[];errors=[];events=[]
    nano,_,_,_,_=nano_source();_,_,pi,_=pi_source();requests=[];temporary=tempfile.TemporaryDirectory(prefix='ex-warm-')
    source_ref=spec['subskills']['receiver']
    class Nine:
        def predict(self,observation,read,verify_observation):
            assert verify_observation(observation,None)['preceding_command9']['admitted']
            events.append('predict9');return {'action9':[90]*6+[0]*3,'hardware_io':False,'software_model_fixture':True}
    class Six:
        camera_id='pi-overview';calibration_sha256='a'*64;last_receipt={'software_model_fixture':True}
        shadow_lineage={'checkpoint_sha256':source_ref['checkpoint_sha256'],'skill_id':source_ref['skill_id'],
            'skill_version':source_ref['skill_version'],'spec_sha256':source_ref['spec_sha256'],'software_model_fixture':True}
        def predict(self,rgb,state,*,valid_mask,observation):
            validate_observation(rgb,state,valid_mask,observation,camera_id=self.camera_id,
                calibration_sha256=self.calibration_sha256,low=LOW,high=HIGH)
            events.append('predict6');return np.array([0]*5+[50],np.float32)
    def loader(store,settings,config,**kwargs):
        events.append('load-both')
        return {'giver':Nine(),'receiver':Six()},{'action_dimensions':{'giver':9,'receiver':6},'software_model_fixture':True}
    def handler(request):
        assert request.method=='GET';requests.append(request.url.path)
        assert request.headers['authorization'].startswith('Bearer ')
        if request.url.path=='/native-policy/observation':return httpx.Response(200,json=nano.create())
        if request.url.path.startswith('/native-policy/observations/'):
            raw,meta=nano.frame(request.url.path.split('/')[3]);headers={'X-Observation-ID':meta['observation_id'],
                'X-Frame-ID':meta['frame_id'],'X-JPEG-SHA256':meta['jpeg_sha256'],'X-Boot-ID':meta['boot_id'],'X-Clock-Domain':meta['clock_domain']}
        elif request.url.path=='/station/observation':return httpx.Response(200,json=pi.create())
        else:
            raw,meta=pi.frame(request.url.path.split('/')[3]);headers={'X-Observation-ID':meta['observation_id'],
                'X-Frame-ID':meta['frame_id'],'X-JPEG-SHA256':meta['frame_sha256'],'X-Boot-ID':meta['frame_boot_id']}
        return httpx.Response(200,content=raw,headers=headers)
    original=store.update_schedule_runtime
    def update(identity,**values):
        value=original(identity,**values)
        if values.get('phase')=='cooperative_actor_ready':events.append('READY');ready.set()
        return value
    monkeypatch.setattr(store,'update_schedule_runtime',update)
    def run():
        try:result.append(run_cooperative_policy_session(store,job,service.config,model_loader=loader,
            client_factory=lambda **kwargs:httpx.Client(**kwargs,transport=httpx.MockTransport(handler)),socket_root=temporary.name))
        except Exception as error:errors.append(error);ready.set()
    thread=threading.Thread(target=run);thread.start();assert ready.wait(3)
    try:
        assert not errors and events[:2]==['load-both','READY'] and requests==[]
        private=read_lease(store.root/'jobs'/job['id'])
        actor=NativeActorClient(job['id'],store.root/'jobs'/job['id'],settings,actor_instance_id=private['actor_instance_id'],
            valid=lambda lease:store.job(job['id'])['status']=='running',socket_root=temporary.name)
        giver=actor.predict('giver');receiver=actor.predict('receiver')
        assert len(giver['action9'])==9 and len(receiver['predicted_native6'])==6
        assert giver['source_sequence']<receiver['source_sequence'] and giver['source_observation']['command_reference9']==[90]*6+[0]*3
        assert not giver['dispatch_performed'] and not receiver['dispatch_performed'] and len(requests)==5
        for item in (giver,receiver):
            folder=store.root/'jobs'/item['source_evidence']['artifact_id']
            assert (folder/'observation.json').is_file() and (folder/'frame.jpg').is_file() and (folder/'result.json').is_file()
        store.update_job(job['id'],status='cancel_requested');thread.join(3)
        assert not thread.is_alive() and not errors and read_lease(store.root/'jobs'/job['id'])['phase']=='REVOKED'
        with pytest.raises(ValueError):actor.predict('giver')
        assert not result[0]['hardware_commands_dispatched'] and not calls
        public=store.schedule_status(job['id'])['runtime']['warm_actor'];assert 'token' not in public
    finally:
        store.update_job(job['id'],status='cancel_requested');thread.join(3);temporary.cleanup();service.close();client.close()


def test_cancel_during_model_load_never_publishes_ready_or_opens_source(tmp_path):
    service,store,calls,client,spec,session,job,settings=queued(tmp_path);entered,release=threading.Event(),threading.Event();errors=[]
    def loader(*args,**kwargs):entered.set();assert release.wait(2);return {'giver':object(),'receiver':object()},{'action_dimensions':{'giver':9,'receiver':6}}
    def run():
        try:run_cooperative_policy_session(store,job,service.config,model_loader=loader)
        except ValueError as error:errors.append(error)
    thread=threading.Thread(target=run);thread.start();assert entered.wait(1)
    try:
        store.update_job(job['id'],status='cancel_requested');release.set();thread.join(3)
        assert errors and 'cancelled/revoked' in str(errors[0])
        assert not lease_path(store.root/'jobs'/job['id']).exists() and not calls
    finally:release.set();thread.join(3);service.close();client.close()


def test_worker_requires_original_process_epoch_and_durable_bootstrap_before_model_load(tmp_path):
    service,store,calls,client,spec,session,job,settings=queued(tmp_path)
    try:
        store.update_schedule_runtime(job['id'],bootstrap_admitted=False)
        def forbidden(*args,**kwargs):raise AssertionError('No model loader before durable admission')
        with pytest.raises(ValueError,match='admission'):run_cooperative_policy_session(store,job,service.config,model_loader=forbidden)
        service.command('stop',{'session_id':session['session_id'],'expected_epoch':session['epoch']},authenticated_actor='owner')
        with pytest.raises(ValueError,match='boot/process/session/epoch'):current_source(store,settings)
        assert not calls
    finally:service.close();client.close()


@pytest.mark.parametrize('role',['giver','receiver'])
def test_real_single_head_ipc_never_loads_predicts_or_reads_manual_role(tmp_path,monkeypatch,role):
    service,store,calls,client,spec,session,job,settings=queued(tmp_path,roles=(role,))
    ready=threading.Event();errors=[];requests=[];loaded=[];manual='receiver' if role=='giver' else 'giver'
    nano,_,_,_,_=nano_source();_,_,pi,_=pi_source();temporary=tempfile.TemporaryDirectory(prefix='ex-one-')
    ref=spec['subskills']['receiver']
    class Nine:
        def predict(self,observation,read,verify_observation):
            assert verify_observation(observation,None)['preceding_command9']['admitted']
            return {'action9':[90]*6+[0]*3,'hardware_io':False,'software_model_fixture':True}
    class Six:
        camera_id='pi-overview';calibration_sha256='a'*64;last_receipt={'software_model_fixture':True}
        shadow_lineage={'checkpoint_sha256':ref.get('checkpoint_sha256'),'skill_id':ref['skill_id'],
            'skill_version':ref['skill_version'],'spec_sha256':ref['spec_sha256'],'software_model_fixture':True}
        def predict(self,rgb,state,*,valid_mask,observation):
            validate_observation(rgb,state,valid_mask,observation,camera_id=self.camera_id,
                calibration_sha256=self.calibration_sha256,low=LOW,high=HIGH)
            return np.array([0]*5+[50],np.float32)
    def loader(_store,actual,_config,**kwargs):
        assert actual['required_policy_roles']==[role] and set(actual['native_plans'])=={role}
        loaded.append(role)
        return {role:Nine() if role=='giver' else Six()},{'action_dimensions':{role:9 if role=='giver' else 6},'software_model_fixture':True}
    def handler(request):
        assert request.method=='GET';requests.append(request.url.path)
        if role=='giver':
            assert request.url.path.startswith('/native-policy/')
            if request.url.path=='/native-policy/observation':return httpx.Response(200,json=nano.create())
            raw,meta=nano.frame(request.url.path.split('/')[3]);headers={'X-Observation-ID':meta['observation_id'],
                'X-Frame-ID':meta['frame_id'],'X-JPEG-SHA256':meta['jpeg_sha256'],'X-Boot-ID':meta['boot_id'],'X-Clock-Domain':meta['clock_domain']}
        else:
            assert request.url.path.startswith('/station/')
            if request.url.path=='/station/observation':return httpx.Response(200,json=pi.create())
            raw,meta=pi.frame(request.url.path.split('/')[3]);headers={'X-Observation-ID':meta['observation_id'],
                'X-Frame-ID':meta['frame_id'],'X-JPEG-SHA256':meta['frame_sha256'],'X-Boot-ID':meta['frame_boot_id']}
        return httpx.Response(200,content=raw,headers=headers)
    original=store.update_schedule_runtime
    def update(identity,**values):
        result=original(identity,**values)
        if values.get('phase')=='cooperative_actor_ready':ready.set()
        return result
    monkeypatch.setattr(store,'update_schedule_runtime',update)
    def run():
        try:run_cooperative_policy_session(store,job,service.config,model_loader=loader,
            client_factory=lambda **kwargs:httpx.Client(**kwargs,transport=httpx.MockTransport(handler)),socket_root=temporary.name)
        except Exception as error:errors.append(error);ready.set()
    thread=threading.Thread(target=run);thread.start();assert ready.wait(3)
    try:
        assert loaded==[role] and not errors and not requests
        lease=read_lease(store.root/'jobs'/job['id'])
        actor=NativeActorClient(job['id'],store.root/'jobs'/job['id'],settings,actor_instance_id=lease['actor_instance_id'],
            valid=lambda lease:store.job(job['id'])['status']=='running',socket_root=temporary.name)
        with pytest.raises(ValueError,match='Manual role'):actor.predict(manual)
        assert not requests
        result=actor.predict(role);assert result['native_role']==role and len(requests)==(2 if role=='giver' else 3)
        assert not result['dispatch_performed'] and not calls
    finally:
        store.update_job(job['id'],status='cancel_requested');thread.join(3);temporary.cleanup();service.close();client.close()
    assert not thread.is_alive() and not errors
