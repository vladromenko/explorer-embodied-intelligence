"""Real private AF_UNIX IPC and lifecycle fences; no models/devices/processes."""
from copy import deepcopy
import json
import os
from pathlib import Path
import socket
import tempfile
import threading
import time
import uuid

import pytest

from explorer_shared.contracts import canonical,digest
from explorer_shared.cooperative_policy_actor import (ScheduledNativeActor,NativeActorClient,
    read_lease,lease_path,socket_path,_receive,REPLY_LIMIT)


class Rig:
    def __init__(self,tmp_path,*,predict=None):
        self.temporary=tempfile.TemporaryDirectory(prefix='expa-');self.socket_root=Path(self.temporary.name)
        self.root=tmp_path/'job';self.root.mkdir();self.job_id=uuid.uuid4().hex
        self.binding={'session_id':'actual-server-session','epoch':'current-epoch','models':{'giver':'a'*64,'receiver':'b'*64}}
        self.tick=[time.monotonic_ns()];self.active=True;self.calls=[];self.public=[];self.ready=threading.Event();self.errors=[]
        def prediction(role):self.calls.append(role);return {'native_role':role,'action_dimension':9 if role=='giver' else 6,'hardware_io':False}
        self.prediction=predict or prediction
    def start(self):
        def update(value):
            self.public.append(value)
            if value['phase']=='READY':self.ready.set()
        self.actor=ScheduledNativeActor(self.job_id,self.root,self.binding,launch_nonce='a'*32,compute_ttl_s=30,
            predict=self.prediction,valid=lambda:self.active,runtime_update=update,model_receipts={'fixture':True},
            clock=lambda:self.tick[0],socket_root=self.socket_root)
        def run():
            try:self.result=self.actor.serve()
            except Exception as error:self.errors.append(error);self.ready.set()
        self.thread=threading.Thread(target=run);self.thread.start();assert self.ready.wait(2)
        if self.errors:raise self.errors[0]
        return NativeActorClient(self.job_id,self.root,self.binding,actor_instance_id=self.actor.instance,
            valid=lambda lease:self.active,clock=lambda:self.tick[0],socket_root=self.socket_root)
    def close(self):
        self.actor.closed=True;self.thread.join(2);self.temporary.cleanup()
        assert not self.thread.is_alive()


def test_real_private_ipc_separate_native_roles_token_not_public_and_expiry_closes_listener(tmp_path):
    rig=Rig(tmp_path);client=rig.start()
    try:
        assert client.predict('giver')['action_dimension']==9
        assert client.predict('receiver')['action_dimension']==6 and rig.calls==['giver','receiver']
        assert all('token' not in row for row in rig.public) and read_lease(rig.root)['token']
        assert lease_path(rig.root).stat().st_mode&0o077==0
        rig.tick[0]+=31_000_000_000
        with pytest.raises(ValueError,match='lease unavailable'):client.predict('giver')
        rig.thread.join(2);assert not rig.thread.is_alive()
        assert read_lease(rig.root)['phase']=='REVOKED' and read_lease(rig.root)['token'] is None
        assert rig.result['reason']=='COMPUTE_TTL_EXPIRED' and not rig.result['hardware_commands_dispatched']
    finally:rig.close()


def test_cancel_during_prediction_rejects_late_reply_and_never_reactivates_ready(tmp_path):
    entered,release=threading.Event(),threading.Event();errors=[]
    def predict(role):entered.set();assert release.wait(2);return {'old_prediction':True}
    rig=Rig(tmp_path,predict=predict);client=rig.start()
    def request():
        try:client.predict('giver')
        except ValueError as error:errors.append(error)
    reader=threading.Thread(target=request);reader.start();assert entered.wait(1)
    rig.active=False;release.set();reader.join(2);rig.thread.join(2)
    try:
        assert errors and read_lease(rig.root)['phase']=='REVOKED' and read_lease(rig.root)['token'] is None
        assert rig.actor.requests==0 and not any(row['phase']=='READY' for row in rig.public[2:])
    finally:rig.close()


def test_actor_request_nonce_is_one_use_and_wrong_token_never_calls_predict(tmp_path):
    rig=Rig(tmp_path);client=rig.start();lease=read_lease(rig.root)
    body={'schema':'explorer_cooperative_cpu_actor_request_v1','token':lease['token'],'actor_instance_id':rig.actor.instance,
        'binding_sha256':digest(rig.binding),'request_id':uuid.uuid4().hex,'request_monotonic_ns':rig.tick[0],
        'operation':'predict','role':'giver'}
    def request(value):
        with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as peer:
            peer.settimeout(2);peer.connect(str(socket_path(rig.job_id,socket_root=rig.socket_root)))
            peer.sendall(canonical(value)+b'\n');return _receive(peer,REPLY_LIMIT)
    try:
        assert request({**body,'token':'wrong'})['schema'].endswith('_error_v1') and rig.calls==[]
        assert request(body)['schema'].endswith('_reply_v1') and rig.calls==['giver']
        assert 'one-use' in request(body)['error'] and rig.calls==['giver']
        assert 'one-use' in request({**body,'request_id':uuid.uuid4().hex,'request_monotonic_ns':rig.tick[0]+1})['error']
    finally:rig.close()


def test_recovered_compute_session_keeps_first_deadline_and_old_client_cannot_follow_new_instance(tmp_path):
    rig=Rig(tmp_path);old=rig.start();initial=read_lease(rig.root)
    rig.actor.closed=True;rig.thread.join(2);rig.ready.clear();rig.tick[0]+=10_000_000_000
    new=rig.start()
    try:
        current=read_lease(rig.root)
        assert current['actor_instance_id']!=initial['actor_instance_id'] and current['token']!=initial['token']
        assert current['expires_monotonic_ns']==initial['expires_monotonic_ns']
        with pytest.raises(ValueError,match='lease unavailable'):old.predict('giver')
        assert new.predict('receiver')['action_dimension']==6
    finally:rig.close()


def test_private_lease_permissions_and_binding_cannot_be_substituted(tmp_path):
    rig=Rig(tmp_path);client=rig.start()
    try:
        lease_path(rig.root).chmod(0o644)
        with pytest.raises(ValueError,match='Private bounded'):client.predict('giver')
        lease_path(rig.root).chmod(0o600)
        wrong=NativeActorClient(rig.job_id,rig.root,dict(rig.binding,epoch='different'),actor_instance_id=rig.actor.instance,
            valid=lambda lease:True,clock=lambda:rig.tick[0],socket_root=rig.socket_root)
        with pytest.raises(ValueError,match='lease unavailable'):wrong.predict('giver')
        assert not rig.calls
    finally:rig.close()
