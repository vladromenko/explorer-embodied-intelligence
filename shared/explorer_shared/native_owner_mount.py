"""ENGINEERING_GLUE: read-only startup gate and same-instance factory mount.

No ROS/publisher/camera/serial creation, no actuator call, no service restart.
The gate is an activation precondition, never a controller setting or watchdog.
It intentionally refuses a fresh-boot restart where factory HOME could run.
"""
import hashlib
from copy import deepcopy
import json
from pathlib import Path
import time
import sys
import math

from .native_stream03 import AUDITED_FACTORY_SHA256,audit_factory_binding,NativeDispatchTap,NativeTeleopMux
from .native_stream03_api import mount_native_stream_api
from .native_activation import NativeCachedObservation,ObservedNativeActivation


def assert_native_restart_safe(root,*,source_sha256,baseline=None,uptime=None,now=None):
    root=Path(root)
    if baseline is None:
        baseline=json.loads((Path(__file__).resolve().parents[2]/'_registry/controller-config-baseline.json').read_bytes())
    if not isinstance(source_sha256,dict) or not source_sha256:
        raise ValueError('Pinned original source inventory is required')
    hashes={}
    for name,expected in source_sha256.items():
        path=(root/name).resolve()
        if not name.startswith('src/') or not path.is_relative_to((root/'src').resolve()) or name=='src/web.py':
            raise ValueError('Only pinned original native modules, excluding patched entrypoint')
        actual=hashlib.sha256(path.read_bytes()).hexdigest()
        if actual!=expected:
            raise ValueError('Original native module changed: '+name)
        hashes[name]=actual
    configs={}
    for name,expected in baseline.items():
        if Path(name).name!=name:
            raise ValueError('Controller baseline basename required')
        actual=hashlib.sha256((root/'config'/name).read_bytes()).hexdigest()
        if actual!=expected:
            raise ValueError('Original controller configuration changed: '+name)
        configs[name]=actual
    if (root/'config/controller-profile.json').exists():
        raise ValueError('Audited factory ManualArm branch changed')
    startup=json.loads((root/'config/factory-arm-startup.json').read_bytes())
    uptime=float(Path('/proc/uptime').read_text().split()[0]) if uptime is None else uptime
    window=startup.get('startup_window_s',180)
    if (type(uptime) not in (int,float) or type(window) not in (int,float) or
            not 0<=window<uptime):
        raise ValueError('FACTORY_STARTUP_HOME_WINDOW_ACTIVE_OR_UNKNOWN')
    profile=json.loads((root/'config/autonomous-day.json').read_bytes())
    if profile.get('enabled') is not False:
        raise ValueError('AUTONOMOUS_DAY_MAY_DISPATCH_ON_STARTUP')
    raw=(root/'data/status.json').read_bytes();status=json.loads(raw)
    now=time.time() if now is None else now
    stamp=status.get('at');velocity=status.get('velocity')
    if (type(stamp) not in (int,float) or not 0<=now-stamp<1 or
            status.get('stop_latched') is not True or status.get('mode')!='MANUAL' or
            not isinstance(velocity,list) or len(velocity)!=3 or
            any(type(value) not in (int,float) or value!=0 for value in velocity)):
        raise ValueError('FRESH_NATIVE_MANUAL_STOP_AND_ACTUAL_ZERO_VELOCITY_REQUIRED')
    return {'schema':'explorer_native_restart_guard_v1','uptime_s':uptime,'factory_home_window_s':window,
            'source_sha256':hashes,'controller_sha256':configs,
            'status_sha256':hashlib.sha256(raw).hexdigest(),'autonomous_day_enabled':False,
            'controller_settings_written':False,'physical_command_count':0,
            'scope':'conditional_current_boot_startup_gate_not_physical_motion_validation'}


def mount_factory_owner(app,root,manual_arm,teleop,token,*,clock_domain,observed_test_permit=None,
                        status_check=None):
    root=Path(root)
    binding=audit_factory_binding({name:(root/name).read_bytes() for name in AUDITED_FACTORY_SHA256})
    if not binding['exact_audited_source']:
        raise ValueError('Original factory source changed; no mount')
    if getattr(manual_arm,'factory_timed',False) is not True or getattr(manual_arm,'native',False):
        raise ValueError('Existing factory no-readback ManualArm instance required')
    if status_check is None:
        # This is the existing pure telemetry guard, not an arm/MCU reader.
        from arm_commissioning import coordinated_status
        # Start reads the reference while the original STOP is still latched;
        # that must use the original stationary guard. Active/recenter uses the
        # original coordinated guard, whose contract requires STOP released.
        read_status_check=lambda:None if teleop.stop_latched else coordinated_status
    else:
        read_status_check=lambda:status_check
    if not isinstance(clock_domain,str) or not clock_domain.endswith(':'+manual_arm.boot):
        raise ValueError('Actual observer clock-domain name and same kernel boot required')
    tap=NativeDispatchTap(boot_id=manual_arm.boot,clock_domain=clock_domain)
    mux=NativeTeleopMux(teleop,manual_arm,tap,chassis_motion_allowed=False)
    activation=ObservedNativeActivation(mux,NativeCachedObservation(root,manual_arm.boot))
    mux.observation_permit=activation.allowed
    mux.observation_evidence=activation.receipt
    mux.on_invalidate=activation.revoke
    mux.chassis_permit=activation.base_allowed

    def guarded_reference(*,clear_cooperative_fault=True):
        current=manual_arm.reference(read_status_check())
        # Preserve the real native startup/reference identity across streamed
        # state updates. Never accept/erase a later telemetry fault ourselves.
        # Original reference() already invalidates current on such a fault.
        path=root/'data/arm-startup.json';raw=path.read_bytes();original=json.loads(raw)
        if (original.get('boot_id')!=manual_arm.boot or original.get('measured') is not False or
                original.get('startup_reference') is not True or original.get('command_completed') is not True or
                type(original.get('at')) not in (int,float) or original['at']>current.get('at',-1)):
            raise ValueError('Original completed same-boot factory reference artifact unavailable')
        fault=root/'data/arm-telemetry-fault.json'
        if fault.exists() and json.loads(fault.read_bytes()).get('at',0)>original['at']:
            raise ValueError('New observed factory reference required after native telemetry fault')
        source_sha=hashlib.sha256(raw).hexdigest()
        if cooperative is not None and cooperative.guard.reference_fault is not None:
            if not clear_cooperative_fault:raise ValueError('Read-only parked scope cannot clear an original cooperative reference fault')
            fault=cooperative.guard.reference_fault;ended=original.get('ends_monotonic')
            if (source_sha==fault.get('original_reference_sha256') or type(ended) not in (int,float) or
                    not math.isfinite(ended) or ended*1e9<=fault['invalidated_native_ns']):
                raise ValueError('New user-observed original reference required after blocked/unknown cooperative publication')
            with cooperative.guard.lock:
                if cooperative.guard.reference_fault!=fault:raise ValueError('Cooperative reference fault changed while validating original receipt')
                cooperative.guard.reference_fault=None
        if tap.reference is None or tap.reference['source_sha256']!=source_sha:
            tap.reference_header(original,source_sha,native_guard_verified=True,source_bytes=raw)
        return current

    cooperative=None
    module=sys.modules.get(type(manual_arm).__module__)
    limits=getattr(module,'HARD_LIMITS',None)
    if callable(getattr(getattr(manual_arm,'pub',None),'publish',None)) and limits is not None:
        from .native_cooperative05 import NativeCooperativeOwner,ExistingBaseDriveBoundary
        cooperative=NativeCooperativeOwner(mux,activation,guarded_reference,target_limits=limits,
                    scope_reference_reader=lambda:guarded_reference(clear_cooperative_fault=False),
            state_path=root/'data'/'explorer-cooperative-giver-hold.json')
        def invalidate(reason):
            result=activation.revoke(reason);cooperative.guard.revoke(reason);return result
        mux.on_invalidate=invalidate
        mux.chassis_permit=lambda:not cooperative.guard.base_motion_blocked() and activation.base_allowed()
        mux.publication_evidence=lambda:deepcopy(cooperative.guard.last_publication)
    mux.install()
    if cooperative is not None:teleop.drive=ExistingBaseDriveBoundary(teleop.drive,cooperative.guard)
    mounted=mount_native_stream_api(app,mux,token=token,
                                    observed_test_permit=observed_test_permit or activation.allowed,
                                    activation=activation,
                                    reference_reader=guarded_reference,cooperative=cooperative)
    return {'mount':mounted,'binding':binding,'mux':mux,'tap':tap,'activation':activation,'cooperative':cooperative,'reference_reader':guarded_reference,
            # Assign web.stop_all to this callback as well. Wrapping only the
            # teleop field would miss original /api/control STOP's global call.
            'global_stop':mux._stop_all}
