"""ENGINEERING_GLUE: isolated exact native web startup with denied hardware I/O.

Run as a fresh child process (`python -m explorer_shared.native_web_startup_probe`).
Native Python constructors/threads/timers execute; external ROS/MoveIt/evdev,
clock/filesystem and loop scheduling are explicitly injected. Nothing is sent to
any hardware or service. This qualifies source startup requests, not drivers.
"""
import argparse
import ast
import builtins
from copy import deepcopy
import hashlib
import importlib.abc
import importlib.util
import json
import pathlib
import shutil
import socket
import sqlite3
import sys
import tempfile
import threading
import time
import types

import numpy as np


class ProbeBudget(BaseException):
    pass


class ProbeExternalBoundary(BaseException):
    pass


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def run_probe(source_root,*,uptime_s,patched_web=None,cycles=3):
    """Fresh process only: installs module import mocks for the duration."""
    if 'web' in sys.modules or not 1<=cycles<=10:
        raise ValueError('Fresh isolated process and bounded startup cycles required')
    source=pathlib.Path(source_root)
    index=json.loads((source/'startup-source-index.json').read_bytes())
    expected={entry['path']:entry['sha256'] for entry in index}
    for name,digest in expected.items():
        if not name.startswith('src/') or _sha((source/name).read_bytes())!=digest:
            raise ValueError('Original native source inventory changed')
    trace=[];timers=[];threads=[];loops={};errors=[];created=[];executed=[];modules={};external_reads=[];http_status={}
    snapshot=json.loads((source/'manual-runtime-snapshot.json').read_bytes())
    gamepad=snapshot['/api/teaching']['gamepad'];teleop=snapshot['/api/teleop']
    if (gamepad.get('mode')!='DISARMED' or teleop.get('owner') is not None or
            teleop.get('stop_latched') is not True or teleop.get('inputs')):
        raise ValueError('Actual manual snapshot is not idle/DISARMED/STOP')
    clock=[time.time()];initial_wall=clock[0]
    def denied(kind,*args,**kwargs):
        trace.append({'boundary':kind,'attempted':True})
        raise ProbeExternalBoundary(kind)
    def primitive(value):
        if value is None or type(value) in (str,bool,int,float):return value
        if isinstance(value,(tuple,list)):return [primitive(item) for item in value]
        if isinstance(value,dict):return {str(key):primitive(item) for key,item in value.items()}
        return {'external_type':type(value).__name__,'fields':primitive(vars(value))}
    class Fake:
        BOX=1;ADD=0;TRANSIENT_LOCAL=1;RELIABLE=1
        def __init__(self,*args,**kwargs):self.__dict__.update(kwargs)
        def __getattr__(self,name):
            if name in ('send_goal_async','send_goal','execute','publish','call_async'):
                return lambda *a,**kw:denied('external_ros_action:'+name,*a,**kw)
            value=Fake();setattr(self,name,value);return value
        def __call__(self,*args,**kwargs):return Fake(*args,**kwargs)
        def __iter__(self):return iter(())
        def __bool__(self):return False
        def __enter__(self):return self
        def __exit__(self,*args):return None
        def __getitem__(self,key):return Fake()
        def get_global_link_transform(self,*args):return np.eye(4)
    class Publisher:
        def __init__(self,topic):self.topic=topic;created.append(topic)
        def publish(self,message):
            trace.append({'topic':self.topic,'message':primitive(message),'physical_send':False})
        def get_subscription_count(self):return 1
        def wait_for_all_acked(self,*args):return True
    class Node(Fake):
        def create_publisher(self,kind,topic,*args,**kwargs):return Publisher(topic)
        def create_subscription(self,*args,**kwargs):return Fake()
        def create_client(self,*args,**kwargs):return Fake()
        def create_timer(self,period,callback):timers.append((period,callback));return Fake()
        def get_clock(self):return types.SimpleNamespace(now=lambda:types.SimpleNamespace(nanoseconds=int(clock[0]*1e9)))
        def count_publishers(self,*args):return 0
    class CapturedThread:
        def __init__(self,target,args=(),**kwargs):
            self.target,self.args=target,args;self.name=kwargs.get('name',getattr(target,'__name__','?'))
        def start(self):threads.append(self)
        def join(self,*args,**kwargs):return None
        def is_alive(self):return False
    class Event:
        def __init__(self):self.value=False
        def set(self):self.value=True
        def clear(self):self.value=False
        def is_set(self):return self.value
        def wait(self,*args,**kwargs):return self.value
    tm=types.SimpleNamespace(**{key:getattr(time,key) for key in dir(time) if not key.startswith('__')})
    tm.sleep=lambda seconds:clock.__setitem__(0,clock[0]+seconds)
    tm.time=lambda:clock[0]
    tm.monotonic=lambda:uptime_s+(clock[0]-initial_wall)
    tm.monotonic_ns=lambda:int(tm.monotonic()*1e9)
    th=types.SimpleNamespace(**{key:getattr(threading,key) for key in dir(threading) if not key.startswith('__')})
    th.Thread=CapturedThread;th.Event=Event
    class Rewrite(ast.NodeTransformer):
        def visit_Constant(self,node):
            if node.value=='/home/vlad/Explorer':return ast.copy_location(ast.Constant(str(root)),node)
            if node.value=='/proc/uptime':return ast.copy_location(ast.Constant(str(root/'probe-uptime')),node)
            return node
        def visit_While(self,node):
            self.generic_visit(node)
            call=ast.Call(ast.Name('__probe_iteration__',ast.Load()),[ast.Constant(self.name+':'+str(node.lineno))],[])
            node.body.insert(0,ast.Expr(call));return node
    def budget(name):
        loops[name]=loops.get(name,0)+1
        if loops[name]>1:raise ProbeBudget(name)
    class ProbePath(type(pathlib.Path())):
        def read_bytes(self):
            if self==pathlib.Path('/proc/uptime'):
                return (str(uptime_s)+' 0').encode()
            raw=super().read_bytes()
            if self==root/'data/status.json':
                value=json.loads(raw);value['at']=tm.time()
                return json.dumps(value).encode()
            return raw
        def read_text(self,encoding=None,errors=None):
            return self.read_bytes().decode(encoding or 'utf-8',errors or 'strict')
    pp=types.SimpleNamespace(**{key:getattr(pathlib,key) for key in dir(pathlib) if not key.startswith('__')})
    pp.Path=ProbePath
    class Device:
        def __init__(self,*args,**kwargs):
            if gamepad.get('connected') is not True:raise OSError('Snapshot gamepad absent')
            import yaml
            self.config=yaml.safe_load((root/'config/gamepad.yaml').read_bytes())
            self.info=types.SimpleNamespace(vendor=self.config['device']['vendor_id'],product=self.config['device']['product_id'])
            self.fd=-1
        def active_keys(self):return [self.config['buttons'][name] for name in gamepad.get('buttons',[])]
        def absinfo(self,code):
            configured=next((item for item in self.config['axes'].values() if item['code']==code),{})
            return types.SimpleNamespace(value=gamepad.get('axes',{}).get(str(code),configured.get('center',0)))
        def read(self):return []
        def close(self):return None
    class External(types.ModuleType):
        def __getattr__(self,key):
            if key=='__path__':return []
            if key=='Node':return Node
            if key=='TransformException':return RuntimeError
            if key in ('init','spin'):return lambda *args,**kwargs:None
            if key=='InputDevice':return Device
            return Fake
    externals=('rclpy','std_msgs','sensor_msgs','nav_msgs','geometry_msgs','nav2_msgs','slam_toolbox','tf2_ros',
        'arm_msgs','moveit','moveit_msgs','shape_msgs','builtin_interfaces','action_msgs','control_msgs',
        'trajectory_msgs','rosidl_runtime_py','tf_transformations','ament_index_python','std_srvs','evdev')
    def importer(name,globals=None,locals=None,fromlist=(),level=0):
        if name=='threading':return th
        if name=='time':return tm
        if name=='pathlib':return pp
        if name=='select':return types.SimpleNamespace(select=lambda *a:([],[],[]))
        module=builtins.__import__(name,globals,locals,fromlist,level)
        if name=='explorer_shared.native_owner_mount':
            # The new guard reads the SAME injected external telemetry clock
            # and filesystem as original native code, within this child only.
            module.Path=ProbePath;module.time=tm
        if name in ('subprocess','os','cv2','serial'):
            proxy=types.SimpleNamespace(**vars(module))
            blocked={'subprocess':('Popen','run','call','check_call','check_output'),
                     'os':('system','execv','execve','spawnv','spawnve','posix_spawn','posix_spawnp'),
                     'cv2':('VideoCapture',),'serial':('Serial',)}[name]
            for member in blocked:setattr(proxy,member,lambda *a,_name=name+'.'+member,**kw:denied(_name,*a,**kw))
            if name=='subprocess':
                def read_only_native_service(argv,*args,**kwargs):
                    if argv==['systemctl','--user','is-active','explorer-train.service']:
                        external_reads.append({'command':argv,'injected_state':'inactive'})
                        return types.SimpleNamespace(stdout='inactive\n',stderr='',returncode=3)
                    return denied('subprocess.run')
                proxy.run=read_only_native_service
            return proxy
        return module
    class Loader(importlib.abc.Loader):
        def __init__(self,name,path):self.name,self.path=name,path
        def create_module(self,spec):return None
        def exec_module(self,module):
            raw=patched_web if self.name=='web' and patched_web is not None else self.path.read_bytes()
            modules[self.name]=_sha(raw);tree=ast.parse(raw);rewrite=Rewrite();rewrite.name=self.name
            tree=ast.fix_missing_locations(rewrite.visit(tree))
            module.__dict__.update(__file__=str(self.path),__probe_iteration__=budget,
                __builtins__={**vars(builtins),'__import__':importer})
            exec(compile(tree,str(self.path),'exec'),module.__dict__)
    class Finder(importlib.abc.MetaPathFinder):
        def find_spec(self,name,path=None,target=None):
            if name.split('.')[0] in externals:return importlib.util.spec_from_loader(name,self,is_package=True)
            source_path=source/'src'/(name+'.py')
            if source_path.is_file():return importlib.util.spec_from_loader(name,Loader(name,source_path))
            return None
        def create_module(self,spec):return External(spec.name)
        def exec_module(self,module):module.__path__=[]
    with tempfile.TemporaryDirectory(prefix='explorer-full-native-startup-noio-') as folder:
        root=pathlib.Path(folder)
        for directory in ('config','models','assets','vendor','data','src'):
            if (source/directory).is_dir():shutil.copytree(source/directory,root/directory)
        (root/'data').mkdir(exist_ok=True)
        (root/'config/access_token').write_text('offline-probe-token-no-hardware-access')
        (root/'probe-uptime').write_text(str(uptime_s)+' 0')
        for sql in (root/'data').rglob('*.probe-snapshot.sql'):
            target=pathlib.Path(str(sql).removesuffix('.probe-snapshot.sql'))
            with sqlite3.connect(target) as db:db.executescript(sql.read_text())
        finder=Finder();sys.meta_path.insert(0,finder)
        old_connect=socket.socket.connect
        socket.socket.connect=lambda *args,**kwargs:denied('network.socket.connect')
        try:
            import web
            for cycle in range(cycles):
                for item in list(threads):
                    # The original HOME worker is one-shot; never invoke it
                    # again after its initial return in this process.
                    if cycle and item.target.__name__=='_automatic_startup_home':
                        pass
                    else:
                        loops.clear()
                        try:item.target(*item.args)
                        except ProbeBudget:pass
                        except BaseException as exc:
                            errors.append({'thread':item.name,'error_type':type(exc).__name__,'detail':str(exc)})
                        executed.append({'thread':item.name,'cycle':cycle})
                for period,callback in timers:
                    loops.clear()
                    try:callback()
                    except ProbeBudget:pass
                    except BaseException as exc:
                        errors.append({'timer':callback.__qualname__,'error_type':type(exc).__name__,'detail':str(exc)})
                    executed.append({'timer':callback.__qualname__,'cycle':cycle})
            from fastapi.testclient import TestClient
            # Exercise actual ASGI startup/shutdown and read-only routes too.
            # No uvicorn TCP listener, control POST or ROS/device is opened.
            with TestClient(web.app) as client:
                headers={'Authorization':'Bearer offline-probe-token-no-hardware-access'}
                http_status['teleop']=client.get('/api/teleop',headers=headers).status_code
                native_response=client.get('/api/explorer/retarget/status',headers=headers)
                http_status['native_mux']=native_response.status_code
                native_permission=native_response.json().get('execution_permission') if native_response.status_code==200 else None
        except BaseException as exc:
            errors.append({'factory':'web','error_type':type(exc).__name__,'detail':str(exc)})
        finally:
            socket.socket.connect=old_connect;sys.meta_path.remove(finder)
        actuator=[row for row in trace if row.get('topic') in ('/arm6_joints','/explorer/request') or row.get('boundary')]
        input_files={str(path.relative_to(source)):_sha(path.read_bytes()) for directory in ('config','data','vendor')
                     for path in sorted((source/directory).rglob('*')) if path.is_file() and
                     path.suffix in ('.json','.sql','.urdf','.srdf','.yaml','.STL')}
        adapter_files={name:_sha((pathlib.Path(__file__).parent/name).read_bytes()) for name in (
            'native_web_startup_probe.py','native_owner_mount.py','native_stream03.py','native_stream03_api.py','native_activation.py')}
        mounted=getattr(web,'_explorer_native_mount',None) if 'web' in locals() else None
        result={'schema':'explorer_native_web_startup_probe_v1','uptime_s':uptime_s,'cycles':cycles,
            'source_inventory_sha256':_sha((source/'startup-source-index.json').read_bytes()),
            'loaded_source_sha256':modules,'native_snapshot_sha256':_sha((source/'manual-runtime-snapshot.json').read_bytes()),
            'input_files_sha256':input_files,'input_inventory_sha256':_sha(json.dumps(input_files,sort_keys=True).encode()),
            'adapter_source_sha256':adapter_files,'candidate_web_sha256':_sha(patched_web) if patched_web else None,
            'controller_sha256':{name:_sha((source/'config'/name).read_bytes()) for name in (
                'factory-arm-startup.json','arm-motion.json','factory-runtime.json','controller-calibration.json','arm_model.json','gripper-reference.json')},
            'startup_threads':[item.name for item in threads],'timers':[callback.__qualname__ for _,callback in timers],
            'executed':executed,'created_publisher_topics':created,'publication_trace':trace,
            'external_read_only_queries':external_reads,
            'asgi_lifecycle_and_readonly_http_status':http_status,'uvicorn_tcp_server_started':False,
            'actuator_or_external_attempts':actuator,'errors':errors,
            'source_startup_zero_actuator_requests':not(errors or actuator),
            'native_mount':({'same_existing_arm':mounted['mux'].arm is web.manual_arm,
                'same_existing_teleop':mounted['mux'].teleop is web.gamepad_panel.teleop,
                'global_stop_wrapped':web.stop_all==mounted['global_stop'],'default_execution_permission':native_permission}
                if mounted else None),
            'actual_gamepad_connected':gamepad.get('connected'),'actual_gamepad_mode':gamepad.get('mode'),
            'injected_inputs':{'original_ROOT':'temporary isolated copy','telemetry_at':'fresh synthetic receive time only',
                'gamepad':'actual connected/DISARMED snapshot without new operator events','ROS_MoveIt_drivers':'mocks',
                'scheduling':'captured threads; one loop body per cycle; timers explicitly invoked'},
            'external_driver_implementation_executed':False,'physical_commands':0,'original_owner_restarted':False}
        return deepcopy(result)


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--source-root',required=True);parser.add_argument('--uptime',type=float,required=True)
    parser.add_argument('--output',required=True);parser.add_argument('--candidate')
    args=parser.parse_args()
    result=run_probe(args.source_root,uptime_s=args.uptime,
                     patched_web=pathlib.Path(args.candidate).read_bytes() if args.candidate else None)
    pathlib.Path(args.output).write_text(json.dumps(result,indent=2,sort_keys=True)+'\n')
    print(json.dumps({'source_startup_zero_actuator_requests':result['source_startup_zero_actuator_requests'],
        'actuator_or_external_attempts':len(result['actuator_or_external_attempts']),
        'errors':result['errors'],'loaded_native_modules':len(result['loaded_source_sha256']),
        'threads':len(result['startup_threads']),'timers':len(result['timers']),'physical_commands':0}))


if __name__=='__main__':main()
