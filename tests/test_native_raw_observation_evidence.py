"""Native observations with no command samples retain independently verified RGB."""
import hashlib
from io import BytesIO
from pathlib import Path
import json

import httpx
import pytest
from PIL import Image

from explorer import job_runner
from explorer.store import Store
from explorer.mobile_dataset_evidence import inspect_mobile_import,mobile_evidence_summary
from test_native_capture import setup,sealed


@pytest.mark.parametrize('project,kind',[('02','mobile_import'),('03','retarget_import')])
def test_raw_observation_count_is_independent_of_action_expert_and_model_admission(tmp_path,monkeypatch,project,kind):
    writer,tap,camera,clock,ident,_=setup(tmp_path/'source',project=project)
    try:
        for index in range(2):
            content=BytesIO();Image.new('RGB',(8,6),(index*70,20,30)).save(content,format='JPEG')
            clock.value=3000+index*1000;camera.publish(content.getvalue(),8,6,monotonic_ns=clock.value-10)
            assert writer.submit_observation(ident,camera.before(clock.value),{'channels':{'imu':{'available':True,'mask':True,'value':[0,0,0]}}},clock.value)
        manifest=sealed(writer,ident);assert not manifest['embodied_episode']['samples']
        archive=writer.archive(ident).read_bytes();checksum=hashlib.sha256(archive).hexdigest();review=writer.review(ident,'approve','SOFTWARE-OWNER',checksum)
        def source(request):
            assert request.method=='GET'
            return httpx.Response(200,json=review) if request.url.path.endswith('/review') else httpx.Response(200,content=archive,headers={'X-Content-SHA256':checksum})
        client=httpx.Client;monkeypatch.setattr(job_runner.httpx,'Client',lambda **kw:client(transport=httpx.MockTransport(source),**kw))
        store=Store(tmp_path/'state');job=store.create_import_job(kind,project,ident,checksum,source_format='native_capture_v1',source_review_sha256=review['review_sha256'])
        result=job_runner.approved_native_capture_import(store,job,{'nodes':[{'id':'nano','host':'software.invalid','port':8092,'token':'fixture'}]});store.complete_job(job['id'],result)
        original={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in writer.folder.rglob('*') if p.is_file()}
        report=inspect_mobile_import(store.job(job['id']),managed_root=store.root)
        assert report['raw_frames_recorded']==report['raw_rgb_verified_count']==2
        assert report['raw_observation_evidence']['all_RGB_payloads_verified']
        assert report['raw_observation_evidence']['available_source_channel_counts']=={'camera_rgb':2,'imu':2}
        assert not report['admitted_for_training'] and not report['rgb_verified'] and not report['visual_ACT']['trained_RGB_model_verified']
        assert report['canonical_samples_recorded']==0
        queued=store.create_import_job('mobile_import' if project=='03' else 'retarget_import','02' if project=='03' else '03','f'*32,'b'*64,source_format='native_capture_v1')
        summary=mobile_evidence_summary(store.jobs(),managed_root=store.root,project_id=project)
        assert summary['project_id']==project and summary['counts']['imported']==1 and summary['counts']['pending_imports']==0 and summary['counts']['raw_frames_recorded']==2
        other=mobile_evidence_summary(store.jobs(),managed_root=store.root,project_id=queued['project_id'])
        assert other['counts']['imported']==0 and other['counts']['pending_imports']==1
        assert original=={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in writer.folder.rglob('*') if p.is_file()}
        imported=Path(result['embodied_manifest']).parent
        frame=next((imported/'frames').glob('*.jpg'));frame.write_bytes(frame.read_bytes()+b'corrupt')
        rejected=inspect_mobile_import(store.job(job['id']),managed_root=store.root)
        assert not rejected.get('raw_observation_evidence',{}).get('all_RGB_payloads_verified') and not rejected['admitted_for_training']
    finally:writer.close()


def test_API_evidence_scopes_project_and_does_not_call_queued_copy_imported(tmp_path,monkeypatch):
    import importlib
    from fastapi.testclient import TestClient
    module=importlib.import_module('explorer.app');store=Store(tmp_path/'state')
    for project,kind in [('02','mobile_import'),('03','retarget_import')]:
        store.create_import_job(kind,project,project+'a'*30,'b'*64,source_format='native_capture_v1')
    monkeypatch.setattr(module,'STORE',store)
    monkeypatch.setattr(module,'CONFIG',dict(module.CONFIG,nodes=[],password_salt='fixture',password_hash=hashlib.pbkdf2_hmac('sha256',b'fixture',b'fixture',310000).hex()))
    client=TestClient(module.app)
    assert client.get('/api/mobile/evidence?project_id=03').status_code==401
    assert client.post('/api/login',json={'password':'fixture'}).status_code==200
    for project in ('02','03'):
        response=client.get('/api/mobile/evidence?project_id='+project);assert response.status_code==200
        payload=response.json();assert payload['project_id']==project
        assert payload['counts']['imported']==0 and payload['counts']['pending_imports']==1
        assert len(payload['imports'])==1 and payload['imports'][0]['import_job_project_id']==project
    assert client.get('/api/mobile/evidence?project_id=04').status_code==400
    client.close()
