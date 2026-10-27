"""Forward import pin preparation/CAS/idle checks; local fake source only."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest

from explorer_shared import native_owner_patch as patch,native_owner_mount as mount
from explorer_shared.native_owner_repin import prepare_native_owner_repin,apply_prepared_native_repin
from test_native_owner_mount import files


def idle():
    return {'native_status':{'schema':'explorer_native_retarget_mux_v1','execution_permission':False,'active':False,
        'stop_latched':True,'chassis_motion_allowed':False,'activation':{'execution_permission':False,'authorization_id':None}},
        'cooperative_status':{'schema':'explorer_native_cooperative_owner_v1','policy':None,
            'release_boundary':{'armed':False,'binding':None,'grant':None,'pending_challenge':None}},
        'capture_status':{'schema':'explorer_native_capture_service_v1','active_recording_id':None,'fetch_inflight':False,'finish_pending':False},
        'active_jobs':[]}


def prepared(tmp_path,monkeypatch):
    root=tmp_path/'native';root.mkdir();sources,baseline=files(root)
    for index in range(21):(root/'src'/('original'+str(index)+'.py')).write_text('VALUE=1\n')
    raw=b"from pathlib import Path\nROOT=Path('/home/vlad/Explorer')\nrclpy.init()\ngamepad_panel.bind_arm(manual_arm,reference_arm)\n"
    (root/'src/web.py').write_bytes(raw);monkeypatch.setattr(patch,'WEB_SHA256',hashlib.sha256(raw).hexdigest())
    old=tmp_path/'old';patch.prepare_native_owner_patch(root,old,pinned_shared_path='/immutable/old/shared',nano_node_id='nano',baseline=baseline)
    (root/'src/web.py').write_bytes((old/'web.patched.py').read_bytes())
    new=tmp_path/'new';plan=prepare_native_owner_repin(root,old,new,pinned_shared_path='/immutable/final/shared',baseline=baseline,
        expected_previous_plan_sha256=hashlib.sha256((old/'plan.json').read_bytes()).hexdigest())
    original_guard=mount.assert_native_restart_safe
    monkeypatch.setattr(mount,'assert_native_restart_safe',lambda root,**kwargs:original_guard(root,**kwargs,uptime=10000,now=1000.5))
    return root,old,new,plan,baseline


def test_forward_repin_changes_only_known_path_and_rollback_restores_exact_existing_mount(tmp_path,monkeypatch):
    root,old,new,plan,baseline=prepared(tmp_path,monkeypatch)
    before={str(path.relative_to(root)):path.read_bytes() for path in root.rglob('*') if path.is_file()}
    original=(new/'web.previous.py').read_bytes();candidate=(new/'web.repin.py').read_bytes()
    assert candidate.replace(b"'/immutable/final/shared'",b"'/immutable/old/shared'")==original
    assert plan['source_sha256']==json.loads((old/'plan.json').read_bytes())['source_sha256']
    result=apply_prepared_native_repin(root,new,runtime_guard=idle)
    assert not result['service_restarted'] and result['physical_commands']==0
    assert (root/'src/web.py').read_bytes()==candidate
    result=apply_prepared_native_repin(root,new,runtime_guard=idle,rollback=True)
    assert result['rollback'] and {str(path.relative_to(root)):path.read_bytes() for path in root.rglob('*') if path.is_file()}==before
    apply_prepared_native_repin(root,new,runtime_guard=idle)
    result=apply_prepared_native_repin(root,new,runtime_guard=None,rollback=True)
    assert result['file_only_rollback_without_runtime_observation'] and result['startup_guard'] is None
    assert (root/'src/web.py').read_bytes()==original and not result['service_restarted']


@pytest.mark.parametrize('change',[
    lambda r:r['native_status'].update(execution_permission=True),
    lambda r:r['native_status']['activation'].update(authorization_id='old-owner'),
    lambda r:r['native_status'].update(active=True),
    lambda r:r['cooperative_status']['release_boundary'].update(armed=True),
    lambda r:r['cooperative_status'].update(policy={'model_id':'not-idle'}),
    lambda r:r['capture_status'].update(active_recording_id='actual-recording'),
    lambda r:r.update(active_jobs=['actual-worker']),
])
def test_grant_dual_hold_recording_or_job_blocks_before_any_file_write(tmp_path,monkeypatch,change):
    root,old,new,plan,_=prepared(tmp_path,monkeypatch);before=(root/'src/web.py').read_bytes();snapshot=idle();change(snapshot)
    with pytest.raises(ValueError,match='idle/no grants'):apply_prepared_native_repin(root,new,runtime_guard=lambda:snapshot)
    assert (root/'src/web.py').read_bytes()==before


def test_candidate_and_plan_hashes_changed_together_cannot_smuggle_new_factory_behavior(tmp_path,monkeypatch):
    root,old,new,plan,baseline=prepared(tmp_path,monkeypatch)
    raw=(old/'web.patched.py').read_bytes()+b'\nUNREVIEWED_BEHAVIOR=1\n'
    value=json.loads((old/'plan.json').read_bytes());value['candidate_web_sha256']=hashlib.sha256(raw).hexdigest()
    (old/'plan.json').write_text(json.dumps(value));(old/'web.patched.py').write_bytes(raw);(root/'src/web.py').write_bytes(raw)
    with pytest.raises(ValueError,match='exact original patch generator'):
        prepare_native_owner_repin(root,old,tmp_path/'evil',pinned_shared_path='/immutable/other/shared',baseline=baseline,
            expected_previous_plan_sha256=hashlib.sha256((old/'plan.json').read_bytes()).hexdigest())
    assert not (tmp_path/'evil').exists()


def test_changed_original_or_config_rejected_and_late_web_writer_never_overwritten(tmp_path,monkeypatch):
    root,old,new,plan,_=prepared(tmp_path,monkeypatch)
    path=root/'src/original.py';before=path.read_bytes();path.write_bytes(b'CHANGED')
    with pytest.raises(ValueError,match='module changed'):apply_prepared_native_repin(root,new,runtime_guard=idle)
    path.write_bytes(before)
    def concurrent_writer():(root/'src/web.py').write_bytes(b'OTHER_OWNER_EDIT');return idle()
    with pytest.raises(ValueError,match='entrypoint changed during'):apply_prepared_native_repin(root,new,runtime_guard=concurrent_writer)
    assert (root/'src/web.py').read_bytes()==b'OTHER_OWNER_EDIT'


def test_independent_previous_plan_anchor_and_fresh_boot_guard_fail_closed(tmp_path,monkeypatch):
    root,old,new,plan,baseline=prepared(tmp_path,monkeypatch);original=(root/'src/web.py').read_bytes()
    with pytest.raises(ValueError,match='previous patch plan SHA'):
        prepare_native_owner_repin(root,old,tmp_path/'bad-anchor',pinned_shared_path='/immutable/another/shared',baseline=baseline,
            expected_previous_plan_sha256='0'*64)
    def fresh(*args,**kwargs):raise ValueError('FACTORY_STARTUP_HOME_WINDOW_ACTIVE_OR_UNKNOWN')
    monkeypatch.setattr(mount,'assert_native_restart_safe',fresh)
    with pytest.raises(ValueError,match='HOME_WINDOW'):apply_prepared_native_repin(root,new,runtime_guard=idle)
    assert (root/'src/web.py').read_bytes()==original


def test_repeated_forward_pins_retain_anchored_factory_and_exact_one_path_changes(tmp_path,monkeypatch):
    root,old,first,plan,baseline=prepared(tmp_path,monkeypatch)
    apply_prepared_native_repin(root,first,runtime_guard=idle)
    for index in range(2,5):
        previous=first if index==2 else tmp_path/('forward'+str(index-1));target=tmp_path/('forward'+str(index))
        before=(root/'src/web.py').read_bytes();parent=json.loads((previous/'plan.json').read_bytes())
        result=prepare_native_owner_repin(root,previous,target,pinned_shared_path='/immutable/release'+str(index)+'/shared',baseline=baseline,
            expected_previous_plan_sha256=hashlib.sha256((previous/'plan.json').read_bytes()).hexdigest())
        assert result['previous_plan_sha256']==hashlib.sha256((previous/'plan.json').read_bytes()).hexdigest()
        assert (target/'web.original.py').read_bytes()==(old/'web.original.py').read_bytes()
        assert result['original_patch_plan_sha256']==hashlib.sha256((old/'plan.json').read_bytes()).hexdigest()
        apply_prepared_native_repin(root,target,runtime_guard=idle)
        assert (root/'src/web.py').read_bytes().replace(repr(result['pinned_shared_path']).encode(),repr(parent['pinned_shared_path']).encode())==before
        apply_prepared_native_repin(root,target,runtime_guard=None,rollback=True);assert (root/'src/web.py').read_bytes()==before
        apply_prepared_native_repin(root,target,runtime_guard=idle)


def test_historical_forward_missing_original_requires_independent_ancestor_and_rejects_tamper(tmp_path,monkeypatch):
    root,old,first,plan,baseline=prepared(tmp_path,monkeypatch);apply_prepared_native_repin(root,first,runtime_guard=idle)
    (first/'web.original.py').unlink();(first/'original-plan.json').unlink()
    plan.pop('original_patch_plan_sha256');(first/'plan.json').write_text(json.dumps(plan))
    options=dict(pinned_shared_path='/immutable/next/shared',baseline=baseline,expected_previous_plan_sha256=hashlib.sha256((first/'plan.json').read_bytes()).hexdigest())
    before=(root/'src/web.py').read_bytes()
    with pytest.raises(ValueError,match='independently anchored'):prepare_native_owner_repin(root,first,tmp_path/'missing',**options)
    with pytest.raises(ValueError,match='Independent original'):prepare_native_owner_repin(root,first,tmp_path/'bad',**options,original_prepared=old,expected_original_plan_sha256='0'*64)
    result=prepare_native_owner_repin(root,first,tmp_path/'good',**options,original_prepared=old,expected_original_plan_sha256=hashlib.sha256((old/'plan.json').read_bytes()).hexdigest())
    assert result['previous_plan_sha256']==options['expected_previous_plan_sha256'] and (root/'src/web.py').read_bytes()==before
    # A matching changed plan/hash still cannot introduce new factory behavior.
    raw=(first/'web.repin.py').read_bytes()+b'\nUNREVIEWED_FACTORY=1\n';(first/'web.repin.py').write_bytes(raw);(root/'src/web.py').write_bytes(raw)
    plan['candidate_web_sha256']=hashlib.sha256(raw).hexdigest();(first/'plan.json').write_text(json.dumps(plan));options['expected_previous_plan_sha256']=hashlib.sha256((first/'plan.json').read_bytes()).hexdigest()
    with pytest.raises(ValueError,match='exact original patch generator'):
        prepare_native_owner_repin(root,first,tmp_path/'evil',**options,original_prepared=old,expected_original_plan_sha256=hashlib.sha256((old/'plan.json').read_bytes()).hexdigest())
    assert not (tmp_path/'evil').exists()
