"""Opt-in actual retained source+snapshot qualification; never SSH/hardware."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from explorer_shared.native_owner_patch import prepare_native_owner_patch


@pytest.mark.parametrize('candidate,fresh',[(False,False),(False,True),(True,False),(True,True)])
def test_exact_native_web_startup_factory_and_fresh_boot_negative_control(tmp_path,candidate,fresh):
    source=os.environ.get('EXPLORER_NATIVE_STARTUP_SOURCE')
    if not source:
        pytest.skip('Exact original private source/config/metadata snapshot not requested')
    source=Path(source)
    shared=Path(__file__).resolve().parents[1]/'shared'
    command=[sys.executable,'-m','explorer_shared.native_web_startup_probe','--source-root',str(source),
             '--uptime','120' if fresh else '18002.49','--output',str(tmp_path/'receipt.json')]
    if candidate:
        baseline=json.loads((shared.parent/'_registry/controller-config-baseline.json').read_bytes())
        prepare_native_owner_patch(source,tmp_path/'prepared',pinned_shared_path=str(shared),nano_node_id='nano',baseline=baseline)
        command+=['--candidate',str(tmp_path/'prepared/web.patched.py')]
    result=subprocess.run(command,env={**os.environ,'PYTHONPATH':str(shared)},capture_output=True,text=True,timeout=40)
    assert result.returncode==0,result.stderr
    report=json.loads((tmp_path/'receipt.json').read_bytes())
    assert len(report['adapter_source_sha256'])==9 and all(name in report['adapter_source_sha256'] for name in ('giver_phase_lifecycle05.py','giver_parked_release05.py'))
    assert report['physical_commands']==0 and report['original_owner_restarted'] is False
    if candidate and fresh:
        assert report['actuator_or_external_attempts']==[]
        assert report['errors'][0]['detail']=='FACTORY_STARTUP_HOME_WINDOW_ACTIVE_OR_UNKNOWN'
        assert report['startup_threads']==[]  # gate runs before rclpy or owner creation
    else:
        assert report['errors']==[] and len(report['loaded_source_sha256'])>=80
        assert report['asgi_lifecycle_and_readonly_http_status']=={'teleop':200,'native_mux':200 if candidate else 404}
        assert len(report['startup_threads'])==11 and len(report['timers'])==3
        if fresh:
            calls=report['actuator_or_external_attempts'];assert len(calls)==1
            assert calls[0]['topic']=='/arm6_joints'
            assert calls[0]['message']['fields']=={'time':4000,**{'joint'+str(index):90 for index in range(1,7)}}
            assert report['source_startup_zero_actuator_requests'] is False
        else:
            assert report['source_startup_zero_actuator_requests'] and report['actuator_or_external_attempts']==[]
        if candidate:
            assert report['native_mount']=={'same_existing_arm':True,'same_existing_teleop':True,
                'global_stop_wrapped':True,'default_execution_permission':False}
