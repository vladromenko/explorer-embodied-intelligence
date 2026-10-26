import io
import json
from http.server import ThreadingHTTPServer
import threading
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.request import Request,urlopen

import pytest

from explorer_shared.legacy_bridge import LegacyBridge,parse_dispatch_query
from explorer_shared.node_agent import make_handler
from explorer_shared.native_stream03 import NativeDispatchTap


@pytest.mark.parametrize('query',['after_sequence=-1','after_sequence=1&after_sequence=2','other=1',
                                  'after_sequence=','after_sequence=9223372036854775808',
                                  'after_sequence=１','after_sequence=1&limit=1000000'])
def test_query_rejects_unknown_duplicate_negative_oversized_or_non_ascii_values(query):
    with pytest.raises(ValueError):
        parse_dispatch_query(query)


def test_legacy_proxy_forwards_only_typed_fixed_path_cursor(tmp_path,monkeypatch):
    (tmp_path/'config').mkdir(); (tmp_path/'config/access_token').write_text('mock-original-token')
    seen=[]
    def opener(request,timeout):
        seen.append(request.full_url)
        return io.BytesIO(b'{"receipts":[]}')
    monkeypatch.setattr('explorer_shared.legacy_bridge.urlopen',opener)
    bridge=LegacyBridge(tmp_path)
    # BytesIO lacks HTTP status; a context with the explicit original HTTP ABI.
    class Response(io.BytesIO):
        status=200
    monkeypatch.setattr('explorer_shared.legacy_bridge.urlopen',lambda request,timeout:
                        (seen.append(request.full_url) or Response(b'{"receipts":[]}')))
    bridge.request('explorer/retarget/dispatch',query={'after_sequence':12})
    assert seen[-1]=='http://127.0.0.1:8080/api/explorer/retarget/dispatch?after_sequence=12'
    with pytest.raises(ValueError):
        bridge.request('status',query={'after_sequence':12})
    with pytest.raises(ValueError):
        bridge.request('explorer/retarget/input',{},query={'after_sequence':12})


def test_authenticated_real_local_node_http_preserves_only_valid_cursor():
    seen=[]; token='mock-node-token-012345678901'
    def legacy(path,payload=None,query=None):
        seen.append((path,query)); return 200,{'cursor':query['after_sequence'] if query else None}
    agent=SimpleNamespace(mode='nano',mobile_records=None,legacy_bridge=object(),token=token,legacy_request=legacy)
    server=ThreadingHTTPServer(('127.0.0.1',0),make_handler(agent)); server.daemon_threads=True
    thread=threading.Thread(target=server.serve_forever,daemon=True); thread.start()
    origin='http://127.0.0.1:'+str(server.server_port)
    try:
        with pytest.raises(HTTPError) as failure:
            urlopen(origin+'/legacy/explorer/retarget/dispatch?after_sequence=7',timeout=2)
        assert failure.value.code==401 and seen==[]
        request=Request(origin+'/legacy/explorer/retarget/dispatch?after_sequence=7',
                         headers={'Authorization':'Bearer '+token})
        with urlopen(request,timeout=2) as response:
            assert json.load(response)['cursor']==7
        assert seen==[('explorer/retarget/dispatch',{'after_sequence':7})]
        request=Request(origin+'/legacy/explorer/retarget/dispatch?after_sequence=7&after_sequence=8',
                         headers={'Authorization':'Bearer '+token})
        with pytest.raises(HTTPError):
            urlopen(request,timeout=2)
        assert len(seen)==1
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=2)


def test_bounded_pages_cursor_never_skips_unreturned_receipts():
    tap=NativeDispatchTap(boot_id='mock',clock_domain='mock',capacity=10)
    for index in range(5):
        tap.append('arm_host_publish',{'q_commanded_deg':[90]*6},source_id='mock')
    first=tap.since(0,limit=2)
    assert first['next_sequence']==2 and first['available_latest_sequence']==5
    second=tap.since(first['next_sequence'],limit=2)
    third=tap.since(second['next_sequence'],limit=2)
    assert [row['sequence'] for page in (first,second,third) for row in page['receipts']]==[1,2,3,4,5]
    assert tap.since(5,limit=2)['next_sequence']==5


def test_slow_receipt_serialization_does_not_hold_native_publication_lock():
    tap=NativeDispatchTap(boot_id='mock',clock_domain='mock',capacity=10)
    tap.append('arm_host_publish',{'q_commanded_deg':[90]*6},source_id='mock')
    entered,release,appended=threading.Event(),threading.Event(),threading.Event()
    class SlowCopy:
        def __deepcopy__(self,memo):
            entered.set(); assert release.wait(2); return 'copied'
    tap.rows[0]['original_payload']['test_only_slow_copy']=SlowCopy()
    reader=threading.Thread(target=lambda:tap.since(0,limit=1)); reader.start()
    try:
        assert entered.wait(1)
        def publication():
            tap.append('arm_host_publish',{'q_commanded_deg':[91]*6},source_id='mock'); appended.set()
        writer=threading.Thread(target=publication); writer.start()
        assert appended.wait(1), 'Native publication was blocked by source-page deepcopy'
        writer.join(1)
    finally:
        release.set(); reader.join(2)
