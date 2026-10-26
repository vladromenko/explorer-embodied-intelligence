"""Opt-in exact original factory with forward-repinned entrypoint; no devices."""
import json
import hashlib
import os
from pathlib import Path
import subprocess
import sys

import pytest

from explorer_shared.native_owner_patch import prepare_native_owner_patch
from explorer_shared.native_owner_repin import prepare_native_owner_repin


@pytest.mark.parametrize('fresh',[False,True])
def test_exact_forward_repin_full_factory_startup(tmp_path,fresh):
    source=os.environ.get('EXPLORER_NATIVE_STARTUP_SOURCE')
    if not source:pytest.skip('Exact original private snapshot not requested')
    source=Path(source);shared=Path(__file__).resolve().parents[1]/'shared'
    baseline=json.loads((shared.parent/'_registry/controller-config-baseline.json').read_bytes())
    old_pin=tmp_path/'old-pinned-shared';old_pin.symlink_to(shared,target_is_directory=True)
    old=tmp_path/'old';prepare_native_owner_patch(source,old,pinned_shared_path=str(old_pin),nano_node_id='nano',baseline=baseline)
    runtime=tmp_path/'native';(runtime/'src').mkdir(parents=True);(runtime/'config').symlink_to(source/'config',target_is_directory=True)
    for path in (source/'src').glob('*.py'):
        if path.name!='web.py':(runtime/'src'/path.name).symlink_to(path)
    (runtime/'src/web.py').write_bytes((old/'web.patched.py').read_bytes())
    new=tmp_path/'forward';prepare_native_owner_repin(runtime,old,new,pinned_shared_path=str(shared),baseline=baseline,
        expected_previous_plan_sha256=hashlib.sha256((old/'plan.json').read_bytes()).hexdigest())
    output=tmp_path/'receipt.json'
    process=subprocess.run([sys.executable,'-m','explorer_shared.native_web_startup_probe',
        '--source-root',str(source),'--uptime','120' if fresh else '18002.49',
        '--candidate',str(new/'web.repin.py'),'--output',str(output)],
        env={**os.environ,'PYTHONPATH':str(shared)},capture_output=True,text=True,timeout=40)
    assert process.returncode==0,process.stderr
    receipt=json.loads(output.read_bytes())
    assert receipt['physical_commands']==0 and receipt['actuator_or_external_attempts']==[]
    if fresh:
        assert receipt['errors'][0]['detail']=='FACTORY_STARTUP_HOME_WINDOW_ACTIVE_OR_UNKNOWN'
        assert receipt['startup_threads']==[]
    else:
        assert receipt['errors']==[] and receipt['source_startup_zero_actuator_requests']
        assert receipt['native_mount']['default_execution_permission'] is False
        assert receipt['asgi_lifecycle_and_readonly_http_status']=={'teleop':200,'native_mux':200}
