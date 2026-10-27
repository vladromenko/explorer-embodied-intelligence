"""ENGINEERING_GLUE: guarded forward pin of an already reviewed native mount.

Local source preparation/file mutation only, never restart, network or control.
Only the one immutable shared import path may change in the known patched web.
"""
import hashlib
import json
from pathlib import Path
import tempfile
import time

from . import native_owner_patch as patch


def _sha(raw):return hashlib.sha256(raw).hexdigest()


def _inventory(root):
    return {str(path.relative_to(root)):_sha(path.read_bytes()) for path in sorted((root/'src').glob('*.py')) if path.name!='web.py'}


def _baseline(root,baseline):
    if not isinstance(baseline,dict) or not baseline:raise ValueError('Explicit original controller baseline required')
    for name,expected in baseline.items():
        if Path(name).name!=name or _sha((root/'config'/name).read_bytes())!=expected:
            raise ValueError('Original controller baseline changed during forward pin')


def _original_patch(previous,old,raw_plan,*,original_prepared=None,expected_original_plan_sha256=None):
    """Retain the original anchored factory across any number of forward pins."""
    schema=old.get('schema')
    if schema=='explorer_native_owner_patch_plan_v1':
        folder=previous;original_plan=raw_plan;original=(folder/'web.original.py').read_bytes()
    elif schema=='explorer_native_owner_repin_plan_v1':
        if (previous/'original-plan.json').is_file():
            original_plan=(previous/'original-plan.json').read_bytes();original=(previous/'web.original.py').read_bytes()
            if _sha(original_plan)!=old.get('original_patch_plan_sha256'):
                raise ValueError('Retained original patch plan anchor changed')
        else:
            # The first historical forward plan did not retain factory bytes.
            # It needs an independently anchored original preparation once.
            if original_prepared is None or expected_original_plan_sha256 is None:
                raise ValueError('Historical forward pin requires independently anchored original preparation')
            folder=Path(original_prepared);original_plan=(folder/'plan.json').read_bytes()
            if _sha(original_plan)!=expected_original_plan_sha256:
                raise ValueError('Independent original patch plan SHA changed')
            original=(folder/'web.original.py').read_bytes()
    else:raise ValueError('Known original patch or forward pin plan required')
    if len(original_plan)>1024**2:raise ValueError('Bounded original patch plan required')
    factory=json.loads(original_plan)
    if (factory.get('schema')!='explorer_native_owner_patch_plan_v1' or _sha(original)!=patch.WEB_SHA256 or
            factory.get('original_web_sha256')!=patch.WEB_SHA256 or factory.get('source_sha256')!=old.get('source_sha256') or
            factory.get('controller_baseline')!=old.get('controller_baseline') or factory.get('nano_node_id')!=old.get('nano_node_id') or
            factory.get('default_execution_permission') is not False):
        raise ValueError('Exact original factory ancestry/source/controller baseline required')
    return original,original_plan


def prepare_native_owner_repin(root,previous_prepared,output_dir,*,pinned_shared_path,baseline,expected_previous_plan_sha256,
                               original_prepared=None,expected_original_plan_sha256=None):
    root=Path(root);previous=Path(previous_prepared);output=Path(output_dir)
    if output.exists() or output.is_symlink():raise ValueError('New private forward-pin preparation directory required')
    raw_plan=(previous/'plan.json').read_bytes()
    if (not isinstance(expected_previous_plan_sha256,str) or len(raw_plan)>1024**2 or
            _sha(raw_plan)!=expected_previous_plan_sha256):raise ValueError('Independently retained previous patch plan SHA required')
    old=json.loads(raw_plan)
    original,original_plan=_original_patch(previous,old,raw_plan,original_prepared=original_prepared,
        expected_original_plan_sha256=expected_original_plan_sha256)
    current=(root/'src/web.py').read_bytes()
    old_candidate=(previous/('web.patched.py' if old['schema']=='explorer_native_owner_patch_plan_v1' else 'web.repin.py')).read_bytes()
    sources=_inventory(root)
    if (_sha(old_candidate)!=old.get('candidate_web_sha256') or current!=old_candidate or sources!=old.get('source_sha256') or
            baseline!=old.get('controller_baseline') or old.get('default_execution_permission') is not False or
            old['schema']=='explorer_native_owner_repin_plan_v1' and old.get('factory_original_web_sha256')!=patch.WEB_SHA256):
        raise ValueError('Exact original/known patched owner plan and full source inventory required')
    _baseline(root,baseline)
    pinned=Path(pinned_shared_path)
    if not pinned.is_absolute() or '..' in pinned.parts or pinned==Path(old['pinned_shared_path']):
        raise ValueError('A distinct explicit immutable absolute shared path is required')
    # Reuse the authoritative original patch generator, rather than accepting a
    # hand-edited candidate/plan whose hashes were changed together. This small
    # temporary tree only links source text; it imports/executes no native code.
    with tempfile.TemporaryDirectory(prefix='explorer-repin-source-') as temporary:
        stage=Path(temporary)/'original';(stage/'src').mkdir(parents=True)
        (stage/'src/web.py').write_bytes(original)
        for name in sources:(stage/name).symlink_to((root/name).resolve())
        regenerated=Path(temporary)/'regenerated'
        patch.prepare_native_owner_patch(stage,regenerated,pinned_shared_path=old['pinned_shared_path'],
            nano_node_id=old['nano_node_id'],baseline=baseline)
        if (regenerated/'web.patched.py').read_bytes()!=current:
            raise ValueError('Mounted candidate differs from exact original patch generator')
    old_line=('_explorer_mount_sys.path.insert(0, '+repr(old['pinned_shared_path'])+')').encode()
    new_line=('_explorer_mount_sys.path.insert(0, '+repr(str(pinned))+')').encode()
    if current.count(old_line)!=1:raise ValueError('Exactly one reviewed shared import pin required')
    candidate=current.replace(old_line,new_line)
    if candidate.replace(new_line,old_line)!=current:raise ValueError('Only the reviewed import pin may change')
    compile(candidate,'prepared-native-web-repin.py','exec')
    # Recheck before sealing preparation; no source/config file is changed.
    if _inventory(root)!=sources or (root/'src/web.py').read_bytes()!=current:
        raise ValueError('Native original source changed while preparing forward pin')
    _baseline(root,baseline);output.mkdir(parents=True,exist_ok=False)
    patch._atomic(output/'web.original.py',original);patch._atomic(output/'original-plan.json',original_plan)
    patch._atomic(output/'web.previous.py',current);patch._atomic(output/'web.repin.py',candidate)
    plan={'schema':'explorer_native_owner_repin_plan_v1','prepared_at':time.time(),
        'previous_plan_sha256':_sha(raw_plan),'original_patch_plan_sha256':_sha(original_plan),'factory_original_web_sha256':patch.WEB_SHA256,
        'previous_web_sha256':_sha(current),'candidate_web_sha256':_sha(candidate),'source_sha256':sources,
        'controller_baseline':dict(baseline),'previous_pinned_shared_path':old['pinned_shared_path'],
        'pinned_shared_path':str(pinned),'nano_node_id':old['nano_node_id'],'default_execution_permission':False,
        'change_scope':'one_shared_import_path_literal_only','automatic_restart':False,
        'controller_settings_written':False,'physical_qualification':False}
    patch._atomic(output/'plan.json',json.dumps(plan,sort_keys=True,indent=2).encode())
    return plan


def _runtime_idle(receipt):
    native=receipt.get('native_status') or {};activation=native.get('activation') or {}
    cooperative=receipt.get('cooperative_status') or {};boundary=cooperative.get('release_boundary') or {}
    capture=receipt.get('capture_status') or {}
    if (native.get('schema')!='explorer_native_retarget_mux_v1' or native.get('execution_permission') is not False or
            native.get('active') is not False or native.get('stop_latched') is not True or
            activation.get('execution_permission') is not False or activation.get('authorization_id') is not None or
            native.get('chassis_motion_allowed') is not False or
            cooperative.get('schema')!='explorer_native_cooperative_owner_v1' or cooperative.get('policy') is not None or
            boundary.get('armed') is not False or any(boundary.get(key) is not None for key in ('binding','grant','pending_challenge')) or
            capture.get('schema')!='explorer_native_capture_service_v1' or capture.get('active_recording_id') is not None or
            capture.get('fetch_inflight') is not False or capture.get('finish_pending') is not False or receipt.get('active_jobs')!=[]):
        raise ValueError('Current authenticated native idle/no grants/no dual-hold/no capture/no active jobs required')


def apply_prepared_native_repin(root,prepared_dir,*,runtime_guard,rollback=False):
    """File-only CAS. Caller supplies fresh authenticated GET-only idle checks.

Caller owns maintenance exclusion, exact staged shared-source verification,
full startup qualification and a separately controlled web-only restart/monitor.
"""
    root=Path(root);folder=Path(prepared_dir);plan=json.loads((folder/'plan.json').read_bytes())
    before=(folder/'web.previous.py').read_bytes();after=(folder/'web.repin.py').read_bytes()
    old_line=('_explorer_mount_sys.path.insert(0, '+repr(plan.get('previous_pinned_shared_path'))+')').encode()
    new_line=('_explorer_mount_sys.path.insert(0, '+repr(plan.get('pinned_shared_path'))+')').encode()
    if (plan.get('schema')!='explorer_native_owner_repin_plan_v1' or plan.get('factory_original_web_sha256')!=patch.WEB_SHA256 or
            plan.get('default_execution_permission') is not False or _sha(before)!=plan.get('previous_web_sha256') or
            _sha(after)!=plan.get('candidate_web_sha256') or before.count(old_line)!=1 or before.replace(old_line,new_line)!=after):
        raise ValueError('Prepared one-path forward pin/rollback proof changed')
    expected=after if rollback else before;replacement=before if rollback else after;path=root/'src/web.py'
    if path.read_bytes()!=expected:raise ValueError('Current known native entrypoint changed; refusing forward pin/rollback')
    runtime=guard=None
    if not rollback or runtime_guard is not None:
        if not callable(runtime_guard):raise ValueError('Fresh authenticated runtime idle guard callback required')
        started=time.monotonic_ns();runtime=runtime_guard();finished=time.monotonic_ns()
        if not 0<=finished-started<=2_000_000_000:raise ValueError('Runtime idle guard transport expired')
        _runtime_idle(runtime)
    if not rollback:
        from .native_owner_mount import assert_native_restart_safe
        guard=assert_native_restart_safe(root,source_sha256=plan['source_sha256'],baseline=plan['controller_baseline'])
    if _inventory(root)!=plan['source_sha256'] or path.read_bytes()!=expected:
        raise ValueError('Original source or known entrypoint changed during forward pin guard')
    _baseline(root,plan['controller_baseline'])
    mode=path.stat().st_mode&0o777;patch._atomic(path,replacement);path.chmod(mode)
    return {'schema':'explorer_native_owner_repin_result_v1','web_sha256':_sha(replacement),'rollback':rollback,
        'startup_guard':guard,'runtime_idle_receipt_sha256':_sha(json.dumps(runtime,sort_keys=True,allow_nan=False).encode()) if runtime else None,
        'file_only_rollback_without_runtime_observation':rollback and runtime is None,
        'service_restarted':False,'controller_settings_written':False,'physical_commands':0,
        'staged_shared_source_qualification_and_controlled_restart_are_caller_owned':True}
