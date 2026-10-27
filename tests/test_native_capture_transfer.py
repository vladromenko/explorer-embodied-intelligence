"""Bounded Source Range and actual loopback interrupted/resumed GETs only."""
from email.message import Message
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import hashlib
import io
import json
import threading

import httpx
import pytest

from explorer.native_capture_transfer import transfer_native_capture
from explorer_shared.native_capture_download import send_native_capture_download,CHUNK_BYTES
from test_native_capture_approval import approved


class Handler:
    def __init__(self,headers=()):
        self.headers=Message();self.output={};self.wfile=io.BytesIO()
        for key,value in headers:self.headers.add_header(key,value)
    def send_response(self,status):self.status=status
    def send_header(self,key,value):self.output[key]=value
    def end_headers(self):pass
    def send_error(self,status,message):self.status=status;self.error=message


@pytest.mark.parametrize('ranges,condition,status,offset',[(None,None,200,0),('bytes=7-',None,206,7),
    ('bytes=7-','mismatch',200,0),('bytes=-5',None,416,None),('bytes=0-10',None,416,None),('bytes=99999-',None,416,None)])
def test_source_range_exact_whole_sha_fallback_and_no_original_mutation(tmp_path,ranges,condition,status,offset):
    raw=b'original-source-bytes'*100;path=tmp_path/'recording.tar';path.write_bytes(raw);checksum=hashlib.sha256(raw).hexdigest();headers=[]
    if ranges:headers.append(('Range',ranges))
    if condition:headers.append(('If-Range',condition))
    handler=Handler(headers);send_native_capture_download(handler,path,'a'*32)
    assert handler.status==status and path.read_bytes()==raw
    if offset is not None:
        assert handler.wfile.getvalue()==raw[offset:] and handler.output['X-Content-SHA256']==checksum
        assert handler.output['ETag']=='"'+checksum+'"' and int(handler.output['Content-Length'])==len(raw)-offset
    else:assert handler.output['Content-Range']=='bytes */'+str(len(raw))


def test_source_duplicate_ranges_or_non_server_identity_rejected(tmp_path):
    path=tmp_path/'recording.tar';path.write_bytes(b'original')
    handler=Handler([('Range','bytes=0-'),('Range','bytes=1-')]);send_native_capture_download(handler,path,'a'*32)
    assert handler.status==400
    handler=Handler();send_native_capture_download(handler,path,'../foreign');assert handler.status==400


def test_actual_authenticated_interrupted_download_retries_exact_range_and_rechecks_approval(tmp_path):
    raw=bytes(range(256))*1800;source=tmp_path/'source.tar';source.write_bytes(raw);checksum=hashlib.sha256(raw).hexdigest()
    wrapper=approved(archive_sha256=checksum);calls=[];interrupted=[];token='localhost-fixture-token'
    class Source(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_GET(self):
            assert self.headers.get('Authorization')=='Bearer '+token
            calls.append((self.command,self.path,self.headers.get('Range')))
            if self.path.endswith('/review'):
                value=json.dumps(wrapper).encode();self.send_response(200);self.send_header('Content-Length',str(len(value)));self.end_headers();self.wfile.write(value)
            elif not interrupted:
                interrupted.append(True);self.send_response(200);self.send_header('X-Content-SHA256',checksum);self.send_header('ETag','"'+checksum+'"')
                self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw[:CHUNK_BYTES]);self.wfile.flush();self.close_connection=True
            else:send_native_capture_download(self,source,'a'*32)
    server=ThreadingHTTPServer(('127.0.0.1',0),Source);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
        with httpx.Client(headers={'Authorization':'Bearer '+token},trust_env=False,timeout=2) as client:
            base='http://127.0.0.1:'+str(server.server_port)
            arguments=(client,base,'a'*32,checksum,wrapper['review_sha256'],tmp_path/'artifacts')
            with pytest.raises(httpx.RemoteProtocolError):transfer_native_capture(*arguments)
            part=tmp_path/'artifacts'/checksum[:2]/(checksum+'.part');assert part.stat().st_size==CHUNK_BYTES
            target,review,receipt=transfer_native_capture(*arguments)
            assert target.read_bytes()==raw and review==wrapper and receipt['resumed_bytes']==CHUNK_BYTES
            assert receipt['downloaded_bytes']==len(raw)-CHUNK_BYTES and not part.exists()
            assert ('GET','/native-capture/recordings/'+'a'*32+'/download','bytes='+str(CHUNK_BYTES)+'-') in calls
            before=len(calls);_,_,existing=transfer_native_capture(*arguments)
            assert existing['existing_archive_reverified'] and all(path.endswith('/review') for _,path,_ in calls[before:])
            assert all(method=='GET' for method,_,_ in calls) and source.read_bytes()==raw
    finally:server.shutdown();server.server_close();thread.join(2)


def test_rejected_source_review_blocks_bulk_get_and_complete_partial_recovers_without_416(tmp_path):
    raw=b'completed-before-crash';checksum=hashlib.sha256(raw).hexdigest();wrapper=approved(archive_sha256=checksum);calls=[]
    def source(request):
        calls.append(request.url.path)
        assert request.url.path.endswith('/review'),'No bulk GET permitted by this fixture'
        return httpx.Response(200,json=wrapper)
    with httpx.Client(transport=httpx.MockTransport(source)) as client:
        folder=tmp_path/'artifacts'/checksum[:2];folder.mkdir(parents=True);(folder/(checksum+'.part')).write_bytes(raw)
        path,_,result=transfer_native_capture(client,'http://source','a'*32,checksum,wrapper['review_sha256'],tmp_path/'artifacts')
        assert path.read_bytes()==raw and result['completed_partial_recovered'] and result['downloaded_bytes']==0
        wrapper['source_review']['decision']='reject';wrapper['transfer_ready']=False
        from explorer.native_capture_approval import source_bytes
        wrapper['review_sha256']=hashlib.sha256(source_bytes(wrapper['source_review'])).hexdigest()
        with pytest.raises(ValueError,match='not approved'):transfer_native_capture(client,'http://source','a'*32,checksum,wrapper['review_sha256'],tmp_path/'artifacts')
