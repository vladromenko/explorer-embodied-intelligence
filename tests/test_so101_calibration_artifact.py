"""Original-file export and both geometry consumers; fake metadata, no devices."""
import base64
from copy import deepcopy
import hashlib
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import threading
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request,urlopen

import pytest

from explorer_shared.node_agent import make_handler
from explorer_shared.so101_calibration_artifact import configured_calibration_artifact
from explorer_shared.source_geometry_context import SO101GeometryContext
from explorer_shared.native_leader_registration import NativeLeaderContext
from test_transfer_mapping04 import CALIBRATION,ROOT


def station(tmp_path):
    paths={};hashes={}
    for index,role in enumerate(('leader','follower')):
        body=deepcopy(CALIBRATION);body['elbow_flex']['homing_offset']+=index
        raw=json.dumps(body,indent=2).encode();path=tmp_path/(role+'.json');path.write_bytes(raw)
        paths[role]={'calibration':str(path),'port':'SOFTWARE_NO_DEVICE_'+role};hashes[role]=hashlib.sha256(raw).hexdigest()
    return SimpleNamespace(lock=threading.RLock(),configuration={'roles':paths},profiles={}),hashes


@pytest.mark.parametrize('role',['leader','follower'])
def test_export_is_exact_original_bytes_without_connected_hardware_or_any_measurement(tmp_path,role):
    source,hashes=station(tmp_path);path=Path(source.configuration['roles'][role]['calibration']);before=path.read_bytes()
    result=configured_calibration_artifact(source,role,hashes[role]);assert base64.b64decode(result['raw_base64'],validate=True)==before
    assert result['bytes']==len(before) and result['role']==role and result['sha256']==hashes[role]
    assert all(result[k] is False for k in ['device_IO_performed','controller_settings_written','physical_execution_authorized','measured_state_provided','connected_device_reverified_by_this_read','historically_verified_on_connected_hardware'])
    assert path.read_bytes()==before and source.profiles=={}
    with pytest.raises(ValueError):configured_calibration_artifact(source,role,hashes['follower' if role=='leader' else 'leader'])
    path.write_bytes(before+b' ')
    with pytest.raises(ValueError):configured_calibration_artifact(source,role,hashes[role])


def test_same_shared_core_preserves_existing03_leader_and_uses_distinct06_follower_CAD(tmp_path):
    source,hashes=station(tmp_path);contexts={}
    for role in ('leader','follower'):
        envelope=configured_calibration_artifact(source,role,hashes[role]);raw=base64.b64decode(envelope['raw_base64'],validate=True)
        contexts[role]=SO101GeometryContext(ROOT,raw,expected_sha256=envelope['sha256'],source_role=role)
        status=contexts[role].status();assert status['all_computable_A_resolved'] and not status['physical_unit_axis_registration_inferred'] and not status['metric_aperture_known']
        assert contexts[role].preview([0,0,0,0,0,50])['physical_commands']==0
    original03=NativeLeaderContext(ROOT,source.configuration['roles']['leader']['calibration'],expected_sha256=hashes['leader'])
    assert contexts['leader'].status()==original03.status()
    assert contexts['leader'].preview([1,2,3,4,5,50])==original03.preview([1,2,3,4,5,50])
    assert contexts['leader'].source.source_sha256=='bc947e2fea65dfc91b0fc40e4cff0c01c6bf426687995937e62e2b0f41122db5'
    assert contexts['follower'].source.source_sha256=='3a65d2d35e68a8d2f0c2cc176d19b884506543c93ba72980145b80abe276022c'
    assert contexts['leader'].target.source_sha256==contexts['follower'].target.source_sha256
    with pytest.raises(ValueError):SO101GeometryContext(ROOT,b'{}',expected_sha256=hashlib.sha256(b'{}').hexdigest(),source_role='other')


def test_actual_authenticated_local_GET_accepts_only_role_and_SHA_and_never_reads_status_or_opens_owner(tmp_path):
    source,hashes=station(tmp_path);token='OWN_SOFTWARE_HTTP_TOKEN'
    agent=SimpleNamespace(token=token,mode='pi',station=source,station_observed=None,mobile_records=None,arm_retarget=None)
    server=ThreadingHTTPServer(('127.0.0.1',0),make_handler(agent));server.daemon_threads=True
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start();url='http://127.0.0.1:'+str(server.server_port)+'/station/calibration-artifact'
    try:
        query='?'+urlencode({'role':'follower','expected_sha256':hashes['follower']})
        with pytest.raises(HTTPError) as error:urlopen(url+query,timeout=2)
        assert error.value.code==401
        with urlopen(Request(url+query,headers={'Authorization':'Bearer '+token}),timeout=2) as response:packet=json.load(response)
        assert packet['sha256']==hashes['follower'] and not packet['device_IO_performed']
        for invalid in ('','?role=follower','?role=follower&role=leader&expected_sha256='+hashes['follower'],query+'&file=/etc/passwd',query.replace('follower','/etc/passwd')):
            with pytest.raises(HTTPError):urlopen(Request(url+invalid,headers={'Authorization':'Bearer '+token}),timeout=2)
    finally:server.shutdown();server.server_close();thread.join(2)
