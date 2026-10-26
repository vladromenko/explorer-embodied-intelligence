"""ENGINEERING_GLUE: exact source AST characterization with injected publishers.

Never imports the native web/ROS/device modules. Tests the ORIGINAL manual
constructors/idle ticks/home-window decision, with threads captured, not run.
This scoped result is not a full external ROS/web-service lifecycle proof.
"""
import ast
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace

import yaml

from .native_stream03 import AUDITED_FACTORY_SHA256


def _class(source,name,namespace):
    tree=ast.parse(source)
    nodes=[node for node in tree.body if isinstance(node,ast.ClassDef) and node.name in (name,'StreamCancelled')]
    if not any(node.name==name for node in nodes):
        raise ValueError('Original class missing')
    exec(compile(ast.Module(body=nodes,type_ignores=[]),'exact-audited-'+name,'exec'),namespace)
    return namespace[name]


def characterize_native_manual_startup(source_root,*,uptime_s):
    root=Path(source_root);hashes={}
    for name,expected in AUDITED_FACTORY_SHA256.items():
        raw=(root/name).read_bytes();hashes[name]=hashlib.sha256(raw).hexdigest()
        if hashes[name]!=expected:
            raise ValueError('Native source/config changed: '+name)
    sources={name:(root/'src'/name).read_bytes() for name in ('manual_controls.py','gamepad_panel.py')}
    hashes.update({'src/'+name:hashlib.sha256(raw).hexdigest() for name,raw in sources.items()})
    for name,expected in {
        'src/gamepad_panel.py':'c34fad6bdd7783066569531d591b0879463b09eab72b20be66bda1f0a2af3788',
        'src/manual_controls.py':'9ac0553f788bc1258f694d7065e1952696be44bdf307ad05c00cc153af5c0618',
    }.items():
        if hashes[name]!=expected:
            raise ValueError('Original manual source changed: '+name)
    for name in ('src/web.py','config/factory-arm-startup.json','config/gamepad.yaml'):
        hashes[name]=hashlib.sha256((root/name).read_bytes()).hexdigest()
    queued=[];publications=[];base_calls=[];home_calls=[]
    class CapturedThread:
        def __init__(self,target,args=(),**kwargs):
            self.target,self.args=target,args
        def start(self):
            queued.append(self)
    clocks=SimpleNamespace(time=lambda:1000.,monotonic=lambda:500.,sleep=lambda seconds:None)
    threads=SimpleNamespace(Thread=CapturedThread,Lock=threading.Lock,RLock=threading.RLock,Event=threading.Event)
    publisher=SimpleNamespace(publish=lambda message:publications.append(message),get_subscription_count=lambda:1)
    node=SimpleNamespace(create_publisher=lambda *args:publisher)
    controls={'__name__':'audited_manual_controls'}
    exec(compile(sources['manual_controls.py'],'audited-manual-controls','exec'),controls)
    namespace={**controls,'__name__':'audited_manual_arm','json':json,'math':math,'Path':Path,
        'threading':threads,'time':clocks,'ArmJoints':type('ArmJoints',(),{}),'Duration':lambda **kw:kw,
        'HARD_LIMITS':[(0,180)]*4+[(0,270),(30,180)],
        'stationary_status':lambda *args:None,'coordinated_status':lambda *args:None,
        'coordinated_policy_status':lambda *args:None}
    Arm=_class((root/'src/manual_arm.py').read_bytes(),'ManualArm',namespace)
    Teleop=_class((root/'src/manual_teleop.py').read_bytes(),'ManualTeleop',namespace)
    namespace.update(ManualTeleop=Teleop,yaml=yaml)
    Panel=_class(sources['gamepad_panel.py'],'GamepadPanel',namespace)
    with tempfile.TemporaryDirectory(prefix='explorer-native-startup-noio-') as folder:
        scratch=Path(folder);(scratch/'config').mkdir();(scratch/'data').mkdir()
        for name in ('factory-arm-startup.json','arm-motion.json','gamepad.yaml'):
            (scratch/'config'/name).write_bytes((root/'config'/name).read_bytes())
        arm=Arm(scratch,node,lambda:SimpleNamespace())
        arm._uptime=lambda:uptime_s
        # Test the original decision branch, substituting only its terminal
        # home call so even the fresh-boot negative control cannot actuate.
        arm.home_reference=lambda *args:home_calls.append(args)
        (scratch/'data/status.json').write_text(json.dumps({'stop_latched':True}))
        for target in list(queued):
            if target.target.__name__=='_automatic_startup_home':
                target.target(*target.args)
        panel=Panel(scratch,SimpleNamespace(),lambda:base_calls.append('stop'),
                    drive=lambda value:base_calls.append(value),release=lambda:base_calls.append('release'))
        panel.bind_arm(arm,lambda:SimpleNamespace())
        for tick in range(1000):
            panel.teleop.tick(now=500.+tick*.025);panel.feed(500.+tick*.025)
        arm.stop()
        result={'schema':'explorer_native_manual_startup_characterization_v1','source_sha256':hashes,
            'uptime_s':uptime_s,'original_constructor_classes':['ManualArm','ManualTeleop','GamepadPanel'],
            'captured_thread_targets':[item.target.__name__ for item in queued],
            'idle_tick_count':1000,'fake_arm_publications':len(publications),'fake_base_callbacks':len(base_calls),
            'automatic_home_attempts':len(home_calls),'would_automatically_home':bool(home_calls),
            'manual_startup_zero_command_under_tested_conditions':not(publications or base_calls or home_calls),
            'full_web_ros_startup_proven':False,'physical_commands':0,'original_owner_restarted':False,
            'scope':'exact_original_manual_classes_with_fake_publishers_captured_threads_no_evdev_or_ROS'}
    return deepcopy(result)
