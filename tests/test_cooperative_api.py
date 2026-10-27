"""Thin authenticated API over explicit service fixtures, no device/network."""
import hashlib
import importlib

from fastapi.testclient import TestClient

from explorer.store import Store


def test_cooperative_routes_pin_session_scope_actor_and_managed_artifact(tmp_path,monkeypatch):
    module=importlib.import_module('explorer.app');store=Store(tmp_path/'state');calls=[]
    source=tmp_path/'managed.jpg';raw=b'\xff\xd8original-software-fixture\xff\xd9';source.write_bytes(raw)
    class Service:
        def snapshot(self,session=None):calls.append(('GET',session));return {'schema':'fixture-only','selected_session':session}
        def command(self,operation,body,*,authenticated_actor):
            calls.append(('POST',operation,body,authenticated_actor));return {'result':{'operation':operation},'snapshot':self.snapshot(body.get('session_id'))}
        def artifact(self,kind,ident,role):calls.append(('artifact',kind,ident,role));return source,'image/jpeg'
        def observed_frame(self,choice_id,*,authenticated_actor):
            calls.append(('displayed_frame',choice_id,authenticated_actor))
            return raw,{'X-JPEG-SHA256':hashlib.sha256(raw).hexdigest(),'X-Frame-ID':'fake-frame','X-Boot-ID':'fake-boot'}
    service=Service();monkeypatch.setattr(module,'cooperative_service',lambda:service)
    monkeypatch.setattr(module,'STORE',store)
    monkeypatch.setattr(module,'CONFIG',dict(module.CONFIG,nodes=[],password_salt='fixture',password_hash=hashlib.pbkdf2_hmac('sha256',b'fixture',b'fixture',310000).hex()))
    with TestClient(module.app) as client:
        assert client.get('/api/cooperative').status_code==401 and calls==[]
        assert client.post('/api/login',json={'password':'fixture'}).status_code==200
        headers={'X-Explorer-CSRF':client.cookies['explorer_csrf']};ident='a'*32
        assert client.get('/api/cooperative?session_id='+ident).json()['selected_session']==ident
        before=len(calls)
        assert client.get('/api/cooperative?session_id=../bad').status_code==400
        assert client.get('/api/cooperative?session_id='+ident+'&session_id='+ident).status_code==400
        assert client.get('/api/cooperative?path=/etc/passwd').status_code==400
        assert len(calls)==before
        assert client.post('/api/cooperative/record_start',json={}).status_code==403
        assert client.post('/api/cooperative/authorize',json={},headers=headers).status_code==404
        body={'session_id':ident,'actor_id':'spoofed','expected_epoch':'current-fixture'}
        assert client.post('/api/cooperative/step',json=body,headers=headers).status_code==200
        assert client.post('/api/cooperative/warm_models',json={'session_id':ident,'expected_epoch':'current-fixture'},headers=headers).status_code==200
        assert client.post('/api/cooperative/observed_challenge',json={'session_id':ident,'expected_epoch':'current-fixture','source_setup_choice_id':'server-only'},headers=headers).status_code==200
        for operation in ('native_refresh','native_begin','native_resume','native_recenter','native_attach','native_lease','native_stop','native_reset_safe'):
            assert client.post('/api/cooperative/'+operation,json={'session_id':ident,'expected_epoch':'current-fixture','role':'giver','actor_id':'spoofed'},headers=headers).status_code==200
        native_calls=[row for row in calls if row[0]=='POST' and row[1].startswith('native_')]
        assert len(native_calls)==8 and all(row[-1].startswith('authenticated-session:') and row[-1]!='spoofed' for row in native_calls)
        for operation in ('receiver_setup_refresh','receiver_setup_bind_guard','receiver_setup_capture_empty_open',
                          'receiver_setup_capture_empty_closed','receiver_setup_reset_safe','receiver_setup_adopt_hold'):
            assert client.post('/api/cooperative/'+operation,json={'session_id':ident,'expected_epoch':'current-fixture'},headers=headers).status_code==200
        setup_calls=[row for row in calls if row[0]=='POST' and row[1].startswith('receiver_setup_')]
        assert len(setup_calls)==6 and all(row[-1].startswith('authenticated-session:') for row in setup_calls)
        assert client.post('/api/cooperative/receiver_setup_action',json={},headers=headers).status_code==404
        frame=client.get('/api/cooperative/observed-frame/'+ident)
        assert frame.status_code==200 and frame.content==raw and frame.headers['x-jpeg-sha256']==hashlib.sha256(raw).hexdigest()
        assert next(row for row in calls if row[0]=='displayed_frame')[-1].startswith('authenticated-session:')
        assert client.get('/api/cooperative/observed-frame/'+ident+'?path=/etc/passwd').status_code==400
        command=next(row for row in calls if row[0]=='POST')
        assert command[-1].startswith('authenticated-session:') and command[-1]!='spoofed'
        media=client.get('/api/cooperative/artifacts/zone/'+ident+'?role=giver')
        assert media.status_code==200 and media.content==raw and media.headers['cache-control']=='no-store'
        assert client.get('/api/cooperative/artifacts/zone/'+ident+'?file=/etc/passwd').status_code==400
        assert client.get('/api/cooperative/artifacts/receiver-metadata/'+ident).status_code==200
        assert client.get('/api/cooperative/artifacts/native-control/'+ident).status_code==200
        assert client.get('/api/cooperative/artifacts/native-control/'+ident+'?path=/etc/passwd').status_code==400
        assert client.get('/api/cooperative/artifacts/receiver-metadata/'+ident+'?path=/etc/passwd').status_code==400
        assert client.post('/api/cooperative/step',json=[],headers=headers).status_code==409
