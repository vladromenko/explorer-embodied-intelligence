"""Source preparation/startup gate/fault/reference cases, no native service I/O."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import base64

from fastapi import FastAPI
import pytest

from explorer_shared import native_owner_mount,native_owner_patch
from explorer_shared.native_stream03 import NativeDispatchTap
from test_native_stream03 import Arm,Teleop


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def files(tmp_path):
    (tmp_path/'src').mkdir();(tmp_path/'config').mkdir();(tmp_path/'data').mkdir()
    (tmp_path/'src/original.py').write_text('original unchanged')
    (tmp_path/'config/factory-arm-startup.json').write_text(json.dumps({'enabled':True,'startup_window_s':180}))
    (tmp_path/'config/autonomous-day.json').write_text(json.dumps({'enabled':False}))
    (tmp_path/'data/status.json').write_text(json.dumps({'at':1000.,'stop_latched':True,'mode':'MANUAL','velocity':[0.,0.,0.]}))
    return ({'src/original.py':sha((tmp_path/'src/original.py').read_bytes())},
            {'factory-arm-startup.json':sha((tmp_path/'config/factory-arm-startup.json').read_bytes())})


def test_restart_guard_uses_actual_factory_window_and_never_changes_settings(tmp_path):
    sources,baseline=files(tmp_path);before={path:path.read_bytes() for path in tmp_path.rglob('*') if path.is_file()}
    report=native_owner_mount.assert_native_restart_safe(tmp_path,source_sha256=sources,baseline=baseline,uptime=10000.,now=1000.5)
    assert report['physical_command_count']==0 and report['factory_home_window_s']==180
    for uptime in (0.,120.,180.,float('nan')):
        with pytest.raises(ValueError,match='HOME_WINDOW'):
            native_owner_mount.assert_native_restart_safe(tmp_path,source_sha256=sources,baseline=baseline,uptime=uptime,now=1000.5)
    assert {path:path.read_bytes() for path in before}==before
    (tmp_path/'src/original.py').write_text('changed')
    with pytest.raises(ValueError,match='module changed'):
        native_owner_mount.assert_native_restart_safe(tmp_path,source_sha256=sources,baseline=baseline,uptime=10000.,now=1000.5)


@pytest.mark.parametrize('mutation',[
    {'stop_latched':False},{'velocity':[0.,None,0.]},{'velocity':[.01,0.,0.]},{'at':998.},{'mode':'AUTONOMOUS'},
])
def test_restart_guard_blocks_stale_unobserved_unknown_or_moving_source(tmp_path,mutation):
    sources,baseline=files(tmp_path);path=tmp_path/'data/status.json'
    value=json.loads(path.read_bytes());value.update(mutation);path.write_text(json.dumps(value))
    with pytest.raises(ValueError,match='FRESH_NATIVE'):
        native_owner_mount.assert_native_restart_safe(tmp_path,source_sha256=sources,baseline=baseline,uptime=10000.,now=1000.5)


def test_autonomous_background_profile_must_be_explicitly_disabled_not_missing(tmp_path):
    sources,baseline=files(tmp_path)
    (tmp_path/'config/autonomous-day.json').write_text('{}')
    with pytest.raises(ValueError,match='AUTONOMOUS_DAY'):
        native_owner_mount.assert_native_restart_safe(tmp_path,source_sha256=sources,baseline=baseline,uptime=10000.,now=1000.5)


def test_mount_creates_no_publisher_no_start_and_preserves_global_stop_invalidation(tmp_path,monkeypatch):
    files(tmp_path)
    monkeypatch.setattr(native_owner_mount,'AUDITED_FACTORY_SHA256',{})
    monkeypatch.setattr(native_owner_mount,'audit_factory_binding',lambda value:{'exact_audited_source':True})
    arm=Arm();arm.factory_timed=True;arm.boot='boot'
    arm.reference=lambda check:{'boot_id':'boot','servo_deg':[90]*6,'at':10.,'measured':False}
    original={'boot_id':'boot','servo_deg':[90]*6,'at':10.,'measured':False,
              'startup_reference':True,'command_completed':True}
    raw=json.dumps(original,indent=2).encode();(tmp_path/'data/arm-startup.json').write_bytes(raw)
    teleop=Teleop(arm);teleop.arm=arm
    mount=native_owner_mount.mount_factory_owner(FastAPI(),tmp_path,arm,teleop,'local-test-token-long',
                                                 clock_domain='nano:boot',status_check=lambda *a:None)
    assert arm.publications==[] and arm.calls==[] and teleop.native_stop==0
    assert mount['reference_reader']()['servo_deg']==[90]*6
    header=mount['tap'].reference
    assert base64.b64decode(header['original_source_bytes_base64'])==raw
    assert header['native_guard_verified'] and header['source_sha256']==sha(raw)
    mux=mount['mux'];mux.clock=lambda:10.
    epoch=mux.begin(observing=True,neutral=True)['control_epoch'];mux.resume(epoch,observing=True)
    from test_native_stream03 import evidence
    mux.leader_intent(epoch,1,[1.]*6,evidence());teleop._update_arm_stream_locked({},False)
    permit=arm.calls[-1]['permit'];assert permit()
    mount['global_stop']()
    assert not permit() and not mux.active and teleop.native_stop==1
    (tmp_path/'data/arm-telemetry-fault.json').write_text(json.dumps({'at':11.}))
    with pytest.raises(ValueError,match='after native telemetry fault'):
        mount['reference_reader']()


def test_current_canonical_reference_cannot_relabel_prepublication_q_as_after_publication(tmp_path):
    tick=[100];tap=NativeDispatchTap(boot_id='boot',clock_domain='nano',clock=lambda:tick[0])
    tap.reference_header({'boot_id':'boot','servo_deg':[90]*6,'measured':False},'a'*64,native_guard_verified=True)
    tap.append('base_stop_host_request',{'velocity':[0.,0.,0.]},source_id='mock',started_ns=90)
    tick[0]=200
    tap.append('arm_host_publish',{'q_estimated_deg':[91.]*6,'q_commanded_deg':[92.]*6},source_id='mock',started_ns=190)
    page=tap.since();assert page['receipts'][-1]['preceding_command9']['values']==[91.]*6+[0.,0.,0.]
    assert page['canonical_reference']['reference_action9']==[92.]*6+[0.,0.,0.]
    assert page['canonical_reference']['stamp']['monotonic_ns']==200


def test_prepared_patch_and_rollback_are_exact_hash_guarded_and_never_restart(tmp_path,monkeypatch):
    root=tmp_path/'native';root.mkdir();sources,baseline=files(root)
    raw=b"from pathlib import Path\nROOT=Path('/home/vlad/Explorer')\nrclpy.init()\ngamepad_panel.bind_arm(manual_arm,reference_arm)\n"
    (root/'src/web.py').write_bytes(raw)
    for index in range(20):
        (root/'src'/('original'+str(index)+'.py')).write_text('VALUE=1')
    monkeypatch.setattr(native_owner_patch,'WEB_SHA256',sha(raw))
    staged=tmp_path/'staged'
    plan=native_owner_patch.prepare_native_owner_patch(root,staged,pinned_shared_path='/immutable/release/shared',
                                                       nano_node_id='actual-nano',baseline=baseline)
    candidate=(staged/'web.patched.py').read_text()
    assert candidate.index('assert_native_restart_safe(ROOT')<candidate.index('rclpy.init()')
    assert 'stop_all=_explorer_native_mount["global_stop"]' in candidate
    assert not plan['automatic_restart'] and plan['default_execution_permission'] is False
    monkeypatch.setattr(native_owner_mount,'assert_native_restart_safe',lambda *a,**kw:{})
    result=native_owner_patch.apply_prepared_native_patch(root,staged)
    assert not result['service_restarted'] and result['physical_commands']==0
    result=native_owner_patch.apply_prepared_native_patch(root,staged,rollback=True)
    assert result['rollback'] and (root/'src/web.py').read_bytes()==raw
    with pytest.raises(ValueError,match='entrypoint changed'):
        native_owner_patch.apply_prepared_native_patch(root,staged,rollback=True)
