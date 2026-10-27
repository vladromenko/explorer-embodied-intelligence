"""Memory-only HTTP archive import through both real worker dispatch functions."""
import hashlib
import json
from pathlib import Path

import httpx
import pytest

from explorer import job_runner
from explorer.store import Store
from test_native_capture import setup,publication,sealed,outcomes
from test_native_capture_approval import approved


@pytest.mark.parametrize('project,kind,node',[('02','mobile_import','nano'),('03','retarget_import','pi')])
def test_native_worker_import_preserves_source_and_bypasses_legacy_npz_converter(tmp_path,monkeypatch,project,kind,node):
    writer,tap,camera,clock,ident,_=setup(tmp_path/'source',project=project)
    try:
        packet,_=publication(tap,camera,clock);assert writer.submit(ident,packet)
        sealed(writer,ident,outcomes(ident));archive=writer.archive(ident);raw=archive.read_bytes();checksum=hashlib.sha256(raw).hexdigest()
        original=(writer.folder/'manifest.json').read_bytes();calls=[]
        review=approved(ident,checksum,actor='SOFTWARE-FIXTURE-OWNER')
        def transport(request):
            calls.append((request.method,request.url.path))
            if request.url.path.endswith('/review'):return httpx.Response(200,json=review)
            return httpx.Response(200,content=raw,headers={'X-Content-SHA256':checksum})
        client_type=httpx.Client
        monkeypatch.setattr(job_runner.httpx,'Client',lambda **kwargs:client_type(transport=httpx.MockTransport(transport),**kwargs))
        store=Store(tmp_path/'nx');job=store.create_import_job(kind,project,ident,checksum,**({'source_format':'native_capture_v1'} if project=='02' else {}))
        if project=='02':job['config']['source_review_sha256']=review['review_sha256']
        config=dict(root=str(Path(__file__).parents[1]),nodes=[dict(id=node,host='memory.test',port=8092,token='fixture')])
        result=(job_runner.station_import if project=='02' else job_runner.retarget_import)(store,job,config)
        assert result['episode_id']==ident and result['dataset_manifest'] is None
        assert result['source_archive_sha256']==checksum and not result['physical_success_inferred']
        assert Path(result['embodied_manifest']).read_bytes()==original
        assert (writer.folder/'manifest.json').read_bytes()==original
        assert all(method=='GET' for method,_ in calls)
        assert calls[0][1]==('/native-capture/recordings/'+ident+'/review' if project=='02' else '/arm-retarget/recordings/'+ident+'/download')
        if project=='02':assert result['source_approval']==review and Path(result['source_approval_path']).is_file()
        if project=='02':
            from explorer.mobile_dataset_evidence import inspect_mobile_import
            store.complete_job(job['id'],result)
            with store.connect() as db:db.execute('UPDATE jobs SET config=? WHERE id=?',(json.dumps(job['config']),job['id']))
            report=inspect_mobile_import(store.job(job['id']),managed_root=store.root)
            assert report['source_approval']['verified'] and report['source_approval']['review_sha256']==review['review_sha256']
            path=Path(result['source_approval_path']);path.write_bytes(path.read_bytes()+b' ')
            report=inspect_mobile_import(store.job(job['id']),managed_root=store.root)
            assert not report['admitted_for_training'] and any('source_approval' in reason for reason in report['reasons'])
        assert store.create_import_job(kind,project,ident,checksum,**({'source_format':'native_capture_v1'} if project=='02' else {}))['id']==job['id']
        with pytest.raises(ValueError,match='Conflicting'):store.create_import_job(kind,project,ident,'0'*64,**({'source_format':'native_capture_v1'} if project=='02' else {}))
        assert json.loads(original)['embodied_episode']['project_id']==project
    finally:writer.close()


@pytest.mark.parametrize('condition',['missing_pin','rejected','changed_review'])
def test_native_worker_never_requests_bulk_archive_without_current_pinned_source_approval(tmp_path,monkeypatch,condition):
    from explorer.native_capture_approval import source_bytes
    ident='a'*32;checksum='b'*64;review=approved(ident,checksum);expected=review['review_sha256'];calls=[]
    if condition=='rejected':
        review['source_review']['decision']='reject';review['transfer_ready']=False
        review['review_sha256']=hashlib.sha256(source_bytes(review['source_review'])).hexdigest();expected=review['review_sha256']
    elif condition=='changed_review':
        review['source_review']['review_id']='c'*32;review['review_sha256']=hashlib.sha256(source_bytes(review['source_review'])).hexdigest()
    def transport(request):
        calls.append(request.url.path);assert request.url.path.endswith('/review'),'No unapproved bulk download'
        return httpx.Response(200,json=review)
    client_type=httpx.Client
    monkeypatch.setattr(job_runner.httpx,'Client',lambda **kwargs:client_type(transport=httpx.MockTransport(transport),**kwargs))
    store=Store(tmp_path/'state');job=store.create_job('mobile_import','02',{'episode_id':ident,'source_format':'native_capture_v1','archive_sha256':checksum,
        **({'source_review_sha256':expected} if condition!='missing_pin' else {})})
    with pytest.raises(ValueError):job_runner.station_import(store,job,{'nodes':[{'id':'nano','host':'mock-source','port':8092,'token':'mock-source-token'}]})
    assert all(path.endswith('/review') for path in calls) and not (store.root/'episodes'/'native-mobile'/ident).exists()
