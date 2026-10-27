"""Approved04 data-only worker integration with real converter/official Dataset.

Generated JPEGs and original fake Station file writer; all HTTP uses MockTransport.
"""
from copy import deepcopy
import asyncio
from contextlib import suppress
import hashlib
import importlib
import json
from pathlib import Path

import httpx
import numpy as np
import pytest

from explorer import job_runner,source_demo_workflow as flow
from explorer.store import Store
from explorer_shared.native_capture_approval import source_bytes
from test_so101_capture_archive import captured,entries,pack


def revision(wrapper,decision,identity='d'*32):
    changed=deepcopy(wrapper);receipt=changed['source_review']
    receipt.update(previous_review_sha256=changed['review_sha256'],review_id=identity,decision=decision,
        reviewed_native_ns=receipt['reviewed_native_ns']+1)
    changed['transfer_ready']=decision=='approve';changed['review_sha256']=hashlib.sha256(source_bytes(receipt)).hexdigest()
    return changed


def rig(tmp_path,monkeypatch,captured,*,reply=None):
    store=Store(tmp_path/'central');review=captured['review'];ident=captured['id'];raw=captured['path'].read_bytes();calls=[]
    job=flow.queue_source_review(store,ident,captured['sha'],review)
    def transport(request):
        calls.append((request.method,request.url.path,request.headers.get('range')))
        assert request.method=='GET' and request.url.path.startswith('/so101-capture/recordings/'+ident+'/')
        if request.url.path.endswith('/review'):return httpx.Response(200,json=reply() if reply else review)
        assert request.url.path.endswith('/download')
        offset=int(request.headers['range'][6:-1]) if 'range' in request.headers else 0
        return httpx.Response(206 if offset else 200,content=raw[offset:],headers={
            'X-Content-SHA256':captured['sha'],'Content-Length':str(len(raw)-offset),
            **({'Content-Range':f'bytes {offset}-{len(raw)-1}/{len(raw)}'} if offset else {})})
    class SourceClient(httpx.Client):
        def __init__(self,**kwargs):super().__init__(transport=httpx.MockTransport(transport),**kwargs)
    monkeypatch.setattr(job_runner.httpx,'Client',SourceClient)
    config=dict(root=str(Path(__file__).parents[1]),nodes=[dict(id='pi',host='source-software.test',port=8092,token='fake-token')])
    return store,job,config,calls


def test_source04_actual_worker_Range_original_pause_unknown_and_official_dataset(captured,tmp_path,monkeypatch):
    current=[captured['review']]
    store,job,config,calls=rig(tmp_path,monkeypatch,captured,reply=lambda:current[0])
    partial=store.root/'artifacts'/captured['sha'][:2]/(captured['sha']+'.part');partial.parent.mkdir(parents=True)
    partial.write_bytes(captured['path'].read_bytes()[:4096])
    before=captured['raw_manifest'];result=job_runner.station_import(store,job,config)
    assert result['source_format']=='so101_source_capture_v1' and result['frames']==2
    assert result['transfer_evidence']['resumed_bytes']==4096
    assert result['source_manifest_sha256']==hashlib.sha256(before).hexdigest()
    assert Path(result['source_manifest_path']).read_bytes()==before==captured['source'].manifest(captured['id'])
    assert json.loads(before)['outcome']=='unknown' and not result['physical_success_inferred']
    assert Path(result['source_approval_path']).read_bytes()==source_bytes(captured['review'])
    manifest=json.loads(Path(result['dataset_manifest']).read_bytes())
    assert manifest['source_capture_manifest_sha256']==result['capture_manifest_sha256']
    assert [row['frames'] for row in manifest['episodes']]==[1,1]
    selected=[]
    for episode in manifest['episodes']:
        with np.load(Path(result['dataset_manifest']).parent/episode['file']) as data:selected.extend(data['source_sample_index'].tolist())
    assert selected==[0,2] and result['lerobot_v3']['format']=='LeRobotDataset v3.0'
    assert any(path.endswith('/download') and extent=='bytes=4096-' for _,path,extent in calls)
    assert len(store.jobs())==1 and not result['automatic_training_started']
    store.complete_job(job['id'],result)
    assert flow.queue_source_review(store,captured['id'],captured['sha'],captured['review'])['id']==job['id']
    from explorer import lab_skill_workflow as lab
    evidence=lab._source(store,job['id'])[3]
    assert evidence['source_capture']['capture_manifest_sha256']==result['capture_manifest_sha256']
    current[0]=revision(captured['review'],'reject')
    flow.observe_source_review(store,captured['id'],captured['sha'],current[0])
    with pytest.raises(ValueError,match='review'):lab._source(store,job['id'])
    first_files={str(path):hashlib.sha256(path.read_bytes()).hexdigest() for path in Path(result['provenance_path']).parent.rglob('*') if path.is_file()}
    current[0]=revision(current[0],'approve','e'*32)
    second=flow.queue_source_review(store,captured['id'],captured['sha'],current[0])
    imported=job_runner.station_import(store,second,config)
    assert imported['source_approval_path']!=result['source_approval_path'] and imported['dataset_manifest']!=result['dataset_manifest']
    assert imported['transfer_evidence']['existing_archive_reverified'] and imported['frames']==2
    assert all(hashlib.sha256(Path(path).read_bytes()).hexdigest()==sha for path,sha in first_files.items())
    store.complete_job(second['id'],imported)
    assert lab._source(store,second['id'])[3]['source_capture']['source_review_sha256']==current[0]['review_sha256']


@pytest.mark.parametrize('condition',['missing_review','rejected','changed_review','cancelled','wrong_kind','wrong_project'])
def test_source04_worker_denies_before_bulk_or_conversion(captured,tmp_path,monkeypatch,condition):
    current=revision(captured['review'],'reject' if condition=='rejected' else 'approve')
    store,job,config,calls=rig(tmp_path,monkeypatch,captured,reply=lambda:current if condition in ('rejected','changed_review') else captured['review'])
    if condition=='missing_review':job['config'].pop('source_review_sha256')
    elif condition=='cancelled':store.update_job(job['id'],status='cancel_requested')
    elif condition=='wrong_kind':job['kind']='mobile_import'
    elif condition=='wrong_project':job['project_id']='03'
    with pytest.raises((ValueError,RuntimeError)):job_runner.station_import(store,job,config)
    assert not any(path.endswith('/download') for _,path,_ in calls)
    assert not (store.root/'datasets').exists()
    if condition=='rejected':
        with pytest.raises(ValueError):flow.current_source_review(store,captured['id'],captured['sha'],captured['review']['review_sha256'])


@pytest.mark.parametrize('late',['reject','cancel'])
def test_late_rejection_or_cancel_after_conversion_retains_data_without_completing(captured,tmp_path,monkeypatch,late):
    current=[captured['review']];store,job,config,calls=rig(tmp_path,monkeypatch,captured,reply=lambda:current[0])
    from explorer_sim import dataset_export
    def rejected_export(*args,**kwargs):
        if late=='reject':current[0]=revision(current[0],'reject')
        else:store.update_job(job['id'],status='cancel_requested')
        return {'software_stub':'late review/cancel race only'}
    monkeypatch.setattr(dataset_export,'export_lerobot_v3',rejected_export)
    with pytest.raises((ValueError,RuntimeError)):job_runner.station_import(store,job,config)
    assert list((store.root/'episodes'/'station-approved').glob('*/'+captured['id']+'/import-provenance.json'))
    assert store.job(job['id'])['status']==('queued' if late=='reject' else 'cancel_requested')
    if late=='reject':
        with pytest.raises(ValueError):flow.current_source_review(store,captured['id'],captured['sha'],captured['review']['review_sha256'])


def test_source04_renewed_approval_preserves_completed_job_and_old_review(captured,tmp_path):
    store=Store(tmp_path/'state');first=flow.queue_source_review(store,captured['id'],captured['sha'],captured['review'])
    store.complete_job(first['id'],{'fixture_only':True})
    rejected=revision(captured['review'],'reject');assert flow.queue_source_review(store,captured['id'],captured['sha'],rejected) is None
    approved=revision(rejected,'approve','e'*32);second=flow.queue_source_review(store,captured['id'],captured['sha'],approved)
    assert second['id']!=first['id'] and second['config']['review_revision_of_job_id']==first['id']
    assert store.job(first['id'])['config']==first['config'] and store.job(first['id'])['result']=={'fixture_only':True}
    assert flow.queue_source_review(store,captured['id'],captured['sha'],approved)['id']==second['id']
    with pytest.raises(ValueError,match='Stale'):flow.observe_source_review(store,captured['id'],captured['sha'],captured['review'])
    with pytest.raises(ValueError):store.create_import_job('station_import','04',captured['id'])
    with pytest.raises(ValueError):store.create_import_job('station_import','04',captured['id'],'f'*64,
        source_format='so101_source_capture_v1',source_review_sha256=approved['review_sha256'])


@pytest.mark.parametrize('kind,project,format',[('mobile_import','02','so101_source_capture_v1'),('station_import','04','native_capture_v1'),('station_import','04','unknown')])
def test_source04_typed_queue_rejects_cross_producer(tmp_path,kind,project,format):
    with pytest.raises(ValueError):Store(tmp_path).create_import_job(kind,project,'a'*32,'b'*64,source_format=format,source_review_sha256='c'*64)


@pytest.mark.parametrize('format',[None,'unknown'])
def test_legacy_station_import_cannot_bypass_source04_approval(captured,tmp_path,monkeypatch,format):
    inner=entries(captured['source'].archive(captured['id']).read_bytes());manifest=json.loads(inner[0][1])
    manifest['source_context']['context']['source04_demo_permission']={'software_fixture_only':True}
    inner[0]=(inner[0][0],source_bytes(manifest),inner[0][2],inner[0][3]);raw=pack(inner);calls=[]
    def transport(request):
        calls.append(request.url.path)
        return httpx.Response(200,content=raw,headers={'X-Content-SHA256':hashlib.sha256(raw).hexdigest()})
    class LegacyClient(httpx.Client):
        def __init__(self,**kwargs):super().__init__(transport=httpx.MockTransport(transport),**kwargs)
    monkeypatch.setattr(job_runner.httpx,'Client',LegacyClient)
    store=Store(tmp_path/'central');job=store.create_job('station_import','04',{'episode_id':captured['id'],**({'source_format':format} if format else {})})
    config={'root':str(Path(__file__).parents[1]),'nodes':[{'id':'pi','host':'software.test','port':8092,'token':'fake'}]}
    with pytest.raises(ValueError,match='legacy'):job_runner.station_import(store,job,config)
    assert not (store.root/'datasets').exists()
    assert bool(calls)==(format is None)
    if format is None:assert list((store.root/'artifacts').glob('*/*.tar'))


@pytest.mark.parametrize('decision',['approve','reject'])
def test_actual_inventory_poll_recovers_review_metadata_and_only_queues_approved04(captured,tmp_path,monkeypatch,decision):
    module=importlib.import_module('explorer.app');store=Store(tmp_path/'central')
    wrapper=captured['review'] if decision=='approve' else revision(captured['review'],'reject')
    row=dict(recording_id=captured['id'],project_id='04',producer_schema='explorer_so101_source_capture_v1',
        archive_sha256=captured['sha'],**wrapper)
    calls=[]
    def transport(request):
        calls.append((request.method,request.url.path))
        assert request.method=='GET'
        if request.url.path=='/inventory':return httpx.Response(200,json={'station_capture':{'ready_recordings':[row]}})
        assert request.url.path=='/arm-retarget/recordings'
        return httpx.Response(200,json={'recordings':[]})
    monkeypatch.setattr(module,'STORE',store)
    monkeypatch.setattr(module,'CONFIG',{'nodes':[{'id':'pi','host':'software.test','port':8092,'token':'fixture'}]})
    original=flow.queue_source_review
    async def exercise():
        done=asyncio.Event();loop=asyncio.get_running_loop()
        def observe(*args):
            result=original(*args);loop.call_soon_threadsafe(done.set);return result
        monkeypatch.setattr(flow,'queue_source_review',observe)
        async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
            monkeypatch.setattr(module,'CLIENT',client);task=asyncio.create_task(module.poll_nodes())
            try:await asyncio.wait_for(done.wait(),2)
            finally:
                task.cancel()
                with suppress(asyncio.CancelledError):await task
    asyncio.run(exercise())
    assert len(store.jobs())==(1 if decision=='approve' else 0)
    if decision=='reject':
        with pytest.raises(ValueError):flow.current_source_review(store,captured['id'],captured['sha'],wrapper['review_sha256'])
    assert calls and all(method=='GET' for method,_ in calls)
