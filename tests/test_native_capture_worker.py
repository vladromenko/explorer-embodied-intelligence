"""Memory-only HTTP archive import through both real worker dispatch functions."""
import hashlib
import json
from pathlib import Path

import httpx
import pytest

from explorer import job_runner
from explorer.store import Store
from test_native_capture import setup,publication,sealed,outcomes


@pytest.mark.parametrize('project,kind,node',[('02','mobile_import','nano'),('03','retarget_import','pi')])
def test_native_worker_import_preserves_source_and_bypasses_legacy_npz_converter(tmp_path,monkeypatch,project,kind,node):
    writer,tap,camera,clock,ident,_=setup(tmp_path/'source',project=project)
    try:
        packet,_=publication(tap,camera,clock);assert writer.submit(ident,packet)
        sealed(writer,ident,outcomes(ident));archive=writer.archive(ident);raw=archive.read_bytes();checksum=hashlib.sha256(raw).hexdigest()
        original=(writer.folder/'manifest.json').read_bytes();calls=[]
        def transport(request):
            calls.append((request.method,request.url.path))
            return httpx.Response(200,content=raw,headers={'X-Content-SHA256':checksum})
        client_type=httpx.Client
        monkeypatch.setattr(job_runner.httpx,'Client',lambda **kwargs:client_type(transport=httpx.MockTransport(transport),**kwargs))
        store=Store(tmp_path/'nx');job=store.create_import_job(kind,project,ident,checksum,**({'source_format':'native_capture_v1'} if project=='02' else {}))
        config=dict(root=str(Path(__file__).parents[1]),nodes=[dict(id=node,host='memory.test',port=8092,token='fixture')])
        result=(job_runner.station_import if project=='02' else job_runner.retarget_import)(store,job,config)
        assert result['episode_id']==ident and result['dataset_manifest'] is None
        assert result['source_archive_sha256']==checksum and not result['physical_success_inferred']
        assert Path(result['embodied_manifest']).read_bytes()==original
        assert (writer.folder/'manifest.json').read_bytes()==original
        assert all(method=='GET' for method,_ in calls)
        assert calls[0][1]==('/native-capture/recordings/' if project=='02' else '/arm-retarget/recordings/')+ident+'/download'
        assert store.create_import_job(kind,project,ident,checksum,**({'source_format':'native_capture_v1'} if project=='02' else {}))['id']==job['id']
        with pytest.raises(ValueError,match='Conflicting'):store.create_import_job(kind,project,ident,'0'*64,**({'source_format':'native_capture_v1'} if project=='02' else {}))
        assert json.loads(original)['embodied_episode']['project_id']==project
    finally:writer.close()
