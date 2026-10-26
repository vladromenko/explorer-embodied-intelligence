"""ENGINEERING_GLUE: prepare exact original-owner source patch and rollback.

Pure local files only. This module never restarts a service or commands hardware.
Apply/rollback is deliberately conditional on exact known content SHA256.
"""
import hashlib
import json
from pathlib import Path
import os
import time

WEB_SHA256='be68ea5233d1edb8aef476151b2ebe32c88f6bd90a761e23ef31ce54099f74f5'


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _atomic(path,raw):
    path=Path(path);temporary=path.with_suffix(path.suffix+'.explorer-pending')
    with temporary.open('xb') as stream:
        stream.write(raw);stream.flush();os.fsync(stream.fileno())
    temporary.replace(path)
    descriptor=os.open(path.parent,os.O_DIRECTORY)
    try:os.fsync(descriptor)
    finally:os.close(descriptor)


def prepare_native_owner_patch(source_root,output_dir,*,pinned_shared_path,nano_node_id,baseline):
    root=Path(source_root);output=Path(output_dir)
    raw=(root/'src/web.py').read_bytes()
    if _sha(raw)!=WEB_SHA256:
        raise ValueError('Exact original web entrypoint changed; review before preparing')
    pinned=Path(pinned_shared_path)
    if not pinned.is_absolute() or not isinstance(nano_node_id,str) or not nano_node_id or ':' in nano_node_id:
        raise ValueError('Explicit immutable shared path and actual Nano observer node ID required')
    sources={str(path.relative_to(root)):_sha(path.read_bytes()) for path in sorted((root/'src').glob('*.py'))
             if path.name!='web.py'}
    if len(sources)<20:
        raise ValueError('Full original source inventory required')
    guard=('\n# Explorer same-owner integration: source-only restart preflight.\n'
           'import sys as _explorer_mount_sys\n'
           '_explorer_mount_sys.path.insert(0, '+repr(str(pinned))+')\n'
           'from explorer_shared.native_owner_mount import assert_native_restart_safe, mount_factory_owner\n'
           '_explorer_restart_guard=assert_native_restart_safe(ROOT, source_sha256='+repr(sources)+
           ', baseline='+repr(baseline)+')\n')
    mount=('\n# Reuse both already-bound native objects; default execution remains disabled.\n'
           '_explorer_native_mount=mount_factory_owner(app,ROOT,manual_arm,gamepad_panel.teleop,TOKEN,\n'
           '    clock_domain='+repr(nano_node_id+':')+'+manual_arm.boot)\n'
           'stop_all=_explorer_native_mount["global_stop"]\n')
    text=raw.decode('utf-8')
    root_anchor="ROOT=Path('/home/vlad/Explorer')\n"
    mount_anchor='gamepad_panel.bind_arm(manual_arm,reference_arm)\n'
    if text.count(root_anchor)!=1 or text.count(mount_anchor)!=1:
        raise ValueError('Exact native entrypoint anchors changed')
    candidate=text.replace(root_anchor,root_anchor+guard).replace(mount_anchor,mount_anchor+mount).encode('utf-8')
    compile(candidate,'prepared-native-web.py','exec')
    output.mkdir(parents=True,exist_ok=False)
    _atomic(output/'web.original.py',raw);_atomic(output/'web.patched.py',candidate)
    plan={'schema':'explorer_native_owner_patch_plan_v1','prepared_at':time.time(),
          'original_web_sha256':_sha(raw),'candidate_web_sha256':_sha(candidate),
          'source_sha256':sources,'controller_baseline':dict(baseline),'pinned_shared_path':str(pinned),
          'nano_node_id':nano_node_id,'default_execution_permission':False,
          'new_publishers':0,'controller_settings_written':False,'automatic_restart':False,
          'physical_qualification':False}
    _atomic(output/'plan.json',json.dumps(plan,indent=2,sort_keys=True).encode())
    return plan


def apply_prepared_native_patch(root,prepared_dir,*,rollback=False):
    """Explicit file mutation only; parent controls idle-window service action."""
    root=Path(root);prepared=Path(prepared_dir);plan=json.loads((prepared/'plan.json').read_bytes())
    original=(prepared/'web.original.py').read_bytes();candidate=(prepared/'web.patched.py').read_bytes()
    if (_sha(original)!=plan['original_web_sha256'] or _sha(candidate)!=plan['candidate_web_sha256'] or
            plan.get('default_execution_permission') is not False):
        raise ValueError('Prepared source/rollback plan changed')
    current=(root/'src/web.py').read_bytes()
    expected=plan['candidate_web_sha256'] if rollback else plan['original_web_sha256']
    if _sha(current)!=expected:
        raise ValueError('Original owner entrypoint changed; refusing replacement')
    if not rollback:
        from .native_owner_mount import assert_native_restart_safe
        assert_native_restart_safe(root,source_sha256=plan['source_sha256'],baseline=plan['controller_baseline'])
    for name,digest in plan['controller_baseline'].items():
        if _sha((root/'config'/name).read_bytes())!=digest:
            raise ValueError('Controller baseline changed; refusing apply/rollback')
    path=root/'src/web.py';mode=path.stat().st_mode&0o777
    replacement=original if rollback else candidate
    _atomic(path,replacement);path.chmod(mode)
    return {'schema':'explorer_native_owner_patch_result_v1','web_sha256':_sha(replacement),
            'rollback':rollback,'service_restarted':False,'physical_commands':0,
            'controller_settings_written':False}
