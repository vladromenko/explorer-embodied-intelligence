"""Actual ASGI route/service integration; upstream recording is memory-only."""
import hashlib
import importlib
import json

import httpx
from fastapi.testclient import TestClient

from explorer.store import Store


def test_skill_capture_context_is_durable_and_never_changes_native_payload(monkeypatch,tmp_path):
    module=importlib.import_module('explorer.app')
    from explorer.skill_workflow import bind_import_from_capture,workflow
    store=Store(tmp_path/'state')
    native={'active':None,'last':None};calls=[];identity='a'*32
    def transport(request):
        path=request.url.path
        if request.method=='GET':
            return httpx.Response(200,json=native if path=='/legacy/teaching/mobile' else {})
        body=json.loads(request.content);calls.append((path,body))
        if path=='/legacy/teaching/mobile/start':native['active']={'id':identity,'name':body['name']}
        elif path=='/legacy/teaching/mobile/finish':native.update(active=None,last={'id':identity,'outcome':body['outcome']})
        return httpx.Response(200,json=native)
    config=dict(module.CONFIG,root=str(module.ROOT),data_dir=str(store.root),password_salt='test-salt',
        password_hash=hashlib.pbkdf2_hmac('sha256',b'test-code',b'test-salt',310000).hex(),nodes=[dict(id='nano',host='in-memory.test',port=8092,token='not-production')])
    monkeypatch.setattr(module,'STORE',store);monkeypatch.setattr(module,'CONFIG',config)
    monkeypatch.setattr(module,'CLIENT',httpx.AsyncClient(transport=httpx.MockTransport(transport),trust_env=False))
    with TestClient(module.app) as c:
        assert c.post('/api/login',json={'password':'test-code'}).status_code==200
        headers={'X-Explorer-CSRF':c.cookies['explorer_csrf']}
        def post(path,body):return c.post(path,json=body,headers=headers)
        selected=post('/api/skill-workflow',{'preset':'sock_to_basket','request_id':'software-fixture-proposal'}).json()
        skill=selected['spec']['skill_id'];version=selected['spec']['version'];base='/api/skill-workflow/'+skill
        assert post(base+'/confirm',{'version':version,'spec_sha256':selected['spec_sha256']}).status_code==200
        state=c.get(base).json();variant=state['variants'][0]
        session=post(base+'/session',{'version':version,'split':variant['split'],'scene':'Explicit mocked scene; not a physical trial'}).json()
        context=dict(skill_id=skill,version=version,spec_sha256=selected['spec_sha256'],variant_id=variant['variant_id'],session_id=session['session_id'],stationary_chassis_confirmed=True)
        payload=dict(name='No hardware: mocked recording',observing=True,object_label='sock',object_class='unknown',size_class='medium',destination='basket')
        denied=post('/api/robot/teaching/mobile/start',dict(payload,skill_context=dict(context,stationary_chassis_confirmed=False)))
        assert denied.status_code==409 and calls==[]
        started=post('/api/robot/teaching/mobile/start',dict(payload,skill_context=context))
        assert started.status_code==200,started.text
        capture=started.json()['skill_capture']['capture_id']
        assert calls==[('/legacy/teaching/mobile/start',payload)]
        recovered=c.get('/api/skill-workflow/capture-context',params={'episode_id':identity}).json()['capture']
        assert recovered['capture_id']==capture and recovered['original_episode_id']==identity
        assert recovered['skill_id']==skill and recovered['native_still_active_must_be_verified_separately'] is True
        before=len(calls)
        wrong=post('/api/robot/teaching/mobile/stage',dict(stage='unknown-phase',expected_id=identity,skill_capture_id=capture))
        assert wrong.status_code==400 and len(calls)==before
        phase=state['spec']['stages'][0]['id']
        marked=post('/api/robot/teaching/mobile/stage',dict(stage=phase,expected_id=identity,skill_capture_id=capture))
        assert marked.status_code==200 and calls[-1][1]==dict(stage=phase,expected_id=identity)
        wrong=post('/api/robot/teaching/mobile/finish',dict(outcome='success',expected_id='b'*32,skill_capture_id=capture))
        assert wrong.status_code==409 and native['active']['id']==identity
        finished=post('/api/robot/teaching/mobile/finish',dict(outcome='unknown',expected_id=identity,skill_capture_id=capture,skill_review={'completed_stages':[],'full_task_confirmed':False}))
        assert finished.status_code==200,finished.text
        assert calls[-1][1]==dict(outcome='unknown',expected_id=identity)
        imported=finished.json()['import_job'];assert imported['kind']=='mobile_import'
        store.complete_job(imported['id'],dict(episode_id=identity,observation_only=True,frames=2))
        binding=bind_import_from_capture(store,store.job(imported['id']))
        assert binding['skill_id']==skill and binding['original_episode_id']==identity
        reviewed=workflow(store,skill,version)
        assert reviewed['counts']['accepted_originals']==0 and reviewed['counts']['rejected_originals']==1
        refused=post(base+'/train',{'version':version,'settings':{'act_steps':1}})
        assert refused.status_code==409 and not any(job['kind']=='train_act' for job in store.jobs())
        assert c.get('/api/mobile/evidence').json()['counts']['admitted_for_training']==0
        assert c.get('/api/skill-workflow').json()['imports'][0]['assigned_to']['skill_id']==skill
