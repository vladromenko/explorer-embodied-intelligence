"""Fixed DATA transport with in-process HTTP fixtures; no device access."""
import httpx
import pytest

from explorer.transfer_capture_http import NanoCaptureData,nano_capture_connection
from explorer.source_evaluation_transport import PiStationHttp,bounded_request


def test_existing_pi_and_new_nano_share_bounded_authenticated_transport():
    calls=[]
    def reply(request):
        calls.append((request.method,request.url.path,request.headers.get('Authorization')))
        return httpx.Response(200,json={'available':True})
    factory=lambda **kwargs:httpx.Client(transport=httpx.MockTransport(reply),**kwargs)
    config={'nodes':[dict(id='nano',host='fixture',port=8091,token='test-only')]}
    with nano_capture_connection(config,client_factory=factory) as data:
        assert data.capabilities()['available']
        assert data.manifest('a'*32)['available']
        assert data.record_operation('finish','a'*32,{'outcome':{'operator_outcome':'unknown'}})['available']
    with factory(base_url='http://pi-fixture') as client:
        assert PiStationHttp(client).status()['available']
    assert calls==[('GET','/native-capture/capabilities','Bearer test-only'),
        ('GET','/native-capture/recordings/'+32*'a'+'/manifest','Bearer test-only'),
        ('POST','/native-capture/record/finish','Bearer test-only'),('GET','/station/status',None)]


def test_data_transport_exposes_no_controller_dispatch_and_rejects_unowned_paths():
    with httpx.Client(transport=httpx.MockTransport(lambda request:pytest.fail('Invalid request reached transport'))) as client:
        data=NanoCaptureData(client)
        for operation in ('begin','home','stop','policy','grant','../control'):
            with pytest.raises(ValueError):data.record_operation(operation,'a'*32,{})
        with pytest.raises(ValueError):data.record_operation('finish','a'*32,{'recording_id':'b'*32})
        with pytest.raises(ValueError):data.manifest('../control')


def test_both_consumers_retain_response_bounds_and_typed_error():
    with httpx.Client(base_url='http://fixture',transport=httpx.MockTransport(lambda _:httpx.Response(200,content=b'12345'))) as client:
        for source in ('Pi','Nano DATA'):
            with pytest.raises(ValueError,match=source+' source response exceeds'):
                bounded_request(client,'GET','/fixture',limit=4,source_name=source)
    with httpx.Client(base_url='http://fixture',transport=httpx.MockTransport(lambda _:httpx.Response(503))) as client:
        with pytest.raises(ValueError,match='Pi source endpoint unavailable'):PiStationHttp(client).status()
        with pytest.raises(ValueError,match='Nano DATA source endpoint unavailable'):NanoCaptureData(client).status()
