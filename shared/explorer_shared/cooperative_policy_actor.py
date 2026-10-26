"""ENGINEERING_GLUE: private bounded IPC inside one scheduled CPU worker.

No model/device imports and no process creation. Predict callbacks may read
cached observations; no actuator, hardware lease or physical authorization API.
"""
from copy import deepcopy
import json
import os
from pathlib import Path
import re
import secrets
import socket
import stat
import struct
import time
import uuid

from .contracts import canonical,digest
from .learning.checkpoint_control import atomic_json,worker_identity

REQUEST_LIMIT=16*1024
REPLY_LIMIT=2*1024**2


def _job(value):
    if not isinstance(value,str) or not re.fullmatch('[0-9a-f]{32}',value):raise ValueError('Exact server-issued warm worker job ID required')
    return value


def socket_path(job_id,*,socket_root=None):
    root=Path(socket_root) if socket_root is not None else Path('/run/user')/str(os.getuid())/'explorer-policy05'
    root.mkdir(parents=True,exist_ok=True,mode=0o700)
    info=root.lstat()
    if root.is_symlink() or not stat.S_ISDIR(info.st_mode) or info.st_uid!=os.getuid() or info.st_mode&0o077:
        raise ValueError('Server-owned private Unix IPC directory required')
    path=root/(_job(job_id)+'.sock')
    if len(os.fsencode(path))>=104:raise ValueError('Bounded fixed Unix IPC path required')
    return path


def lease_path(job_root):return Path(job_root)/'cooperative-warm-lease.json'


def read_lease(job_root):
    path=lease_path(job_root);info=path.lstat()
    if path.is_symlink() or not stat.S_ISREG(info.st_mode) or info.st_uid!=os.getuid() or info.st_mode&0o077 or info.st_size>128*1024:
        raise ValueError('Private bounded actor lease record required')
    return json.loads(path.read_bytes())


def public_lease(value):return {key:deepcopy(item) for key,item in value.items() if key!='token'}


def _receive(connection,limit):
    data=bytearray()
    while len(data)<=limit:
        chunk=connection.recv(min(65536,limit+1-len(data)))
        if not chunk:raise ValueError('Truncated private actor message')
        data.extend(chunk)
        if b'\n' in chunk:
            if data.count(b'\n')!=1 or not data.endswith(b'\n'):raise ValueError('Exactly one bounded actor JSON message required')
            return json.loads(data[:-1])
    raise ValueError('Private actor message exceeds bound')


class ScheduledNativeActor:
    """serve() runs in the SAME queue worker after both models are loaded."""
    def __init__(self,job_id,job_root,binding,*,launch_nonce,compute_ttl_s,predict,valid,runtime_update,
                 model_receipts,clock=time.monotonic_ns,socket_root=None):
        if type(compute_ttl_s) is not int or not 30<=compute_ttl_s<=600:raise ValueError('Explicit compute TTL30..600s required')
        if not isinstance(launch_nonce,str) or not re.fullmatch('[0-9a-f]{32}',launch_nonce):raise ValueError('Durable scheduler launch nonce required')
        self.job_id,self.root=_job(job_id),Path(job_root);self.binding=deepcopy(binding)
        self.nonce,self.ttl,self.predict,self.valid,self.update=launch_nonce,compute_ttl_s,predict,valid,runtime_update
        self.models=deepcopy(model_receipts);self.clock,self.socket_root=clock,socket_root
        self.identity=worker_identity();self.instance=uuid.uuid4().hex;self.token=secrets.token_urlsafe(32)
        self.lease=None;self.listener=None;self.requests=0;self.closed=False;self.seen={}
    def _check(self):
        if self.closed or not self.valid():raise ValueError('Durable worker/session lease revoked')
        if self.lease is not None and self.clock()>=self.lease['expires_monotonic_ns']:raise ValueError('COMPUTE_TTL_EXPIRED')
    def _publish(self,phase):
        self.lease.update(phase=phase,heartbeat_monotonic_ns=self.clock())
        atomic_json(lease_path(self.root),self.lease);self.update(public_lease(self.lease))
    def serve(self):
        self._check();path=socket_path(self.job_id,socket_root=self.socket_root)
        previous=read_lease(self.root) if lease_path(self.root).exists() else None
        # Never unlink somebody else's current listener/lease. A dead previous
        # worker can leave a socket inode; its exact private identity must die.
        if path.exists() or path.is_symlink():
            if previous is None:raise ValueError('Orphan socket lacks its exact private worker lease')
            info=path.lstat();owner=previous.get('worker') or {}
            if (not stat.S_ISSOCK(info.st_mode) or info.st_uid!=os.getuid() or previous.get('job_id')!=self.job_id or
                    worker_identity(owner.get('pid',-1))==owner):raise ValueError('Existing actor listener cannot be replaced')
            path.unlink()
        ready=self.clock();expires=ready+self.ttl*1_000_000_000;domain='nx:'+self.identity['boot_id']
        if previous is not None:
            if (previous.get('job_id')!=self.job_id or previous.get('binding_sha256')!=digest(self.binding) or
                    previous.get('clock_domain')!=domain or previous.get('compute_ttl_s')!=self.ttl):
                raise ValueError('Previous compute session boot/source/binding differs; explicit new job required')
            ready=previous['ready_monotonic_ns'];expires=previous['expires_monotonic_ns']
        self.lease={'schema':'explorer_cooperative_cpu_actor_lease_v1','job_id':self.job_id,
            'actor_instance_id':self.instance,'worker':self.identity,'launch_nonce':self.nonce,'token':self.token,
            'binding_sha256':digest(self.binding),'binding':deepcopy(self.binding),'model_receipts':self.models,
            'ready_monotonic_ns':ready,'expires_monotonic_ns':expires,
            'compute_ttl_s':self.ttl,'phase':'STARTING','clock_domain':domain,
            'physical_execution_authorized':False,'hardware_lease_renewed':False}
        listener=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM);self.listener=listener;reason='WORKER_CLOSED'
        socket_identity=None
        try:
            self._check();listener.bind(str(path));path.chmod(0o600)
            info=path.lstat();socket_identity=(info.st_dev,info.st_ino)
            listener.listen(2);listener.settimeout(.1);self._publish('READY')
            last_heartbeat=self.clock()
            while not self.closed:
                self._check()
                if self.clock()-last_heartbeat>=500_000_000:self._publish('READY');last_heartbeat=self.clock()
                connection=None
                try:connection,_=listener.accept()
                except socket.timeout:pass
                if connection is not None:
                    with connection:
                        connection.settimeout(2)
                        uid=struct.unpack('3i',connection.getsockopt(socket.SOL_SOCKET,socket.SO_PEERCRED,12))[1]
                        if uid!=os.getuid():raise ValueError('Private actor peer UID differs')
                        try:
                            request=_receive(connection,REQUEST_LIMIT);self._check()
                            if (not isinstance(request,dict) or set(request)!={'schema','token','actor_instance_id','binding_sha256','request_id','request_monotonic_ns','operation','role'} or
                                    request.get('schema')!='explorer_cooperative_cpu_actor_request_v1' or
                                    not secrets.compare_digest(str(request.get('token')),self.token) or
                                    request.get('actor_instance_id')!=self.instance or request.get('binding_sha256')!=digest(self.binding) or
                                    not re.fullmatch('[0-9a-f]{32}',str(request.get('request_id')))):
                                raise ValueError('Exact private actor token/instance/binding/request nonce required')
                            now=self.clock();stamp=request.get('request_monotonic_ns')
                            self.seen={key:value for key,value in self.seen.items() if 0<=now-value<=2_000_000_000}
                            if (type(stamp) is not int or not 0<=now-stamp<=2_000_000_000 or
                                    request['request_id'] in self.seen or len(self.seen)>=4096):
                                raise ValueError('Fresh one-use same-host inference request nonce required')
                            self.seen[request['request_id']]=now
                            operation=request.get('operation');role=request.get('role')
                            if operation!='predict' or role not in ('giver','receiver'):raise ValueError('Only named native pure prediction is supported')
                            self._publish('BUSY');result=self.predict(role);self._check();self.requests+=1
                            self._publish('READY')
                            reply={'schema':'explorer_cooperative_cpu_actor_reply_v1','request_id':request['request_id'],
                                'actor_instance_id':self.instance,'binding_sha256':digest(self.binding),'role':role,
                                'prediction':result,'dispatch_performed':False,'physical_execution_authorized':False}
                        except (ValueError,OSError,RuntimeError) as error:
                            reply={'schema':'explorer_cooperative_cpu_actor_error_v1','error':str(error),
                                'actor_instance_id':self.instance,'physical_execution_authorized':False}
                        raw=canonical(reply)+b'\n'
                        if len(raw)>REPLY_LIMIT:raise ValueError('Bounded native inference reply required')
                        try:connection.sendall(raw)
                        except OSError:pass  # A vanished read-only caller cannot create authority.
        except (ValueError,OSError,RuntimeError) as error:reason=str(error)
        finally:
            self.closed=True;listener.close()
            if path.exists() and not path.is_symlink() and stat.S_ISSOCK(path.lstat().st_mode):
                info=path.lstat()
                if socket_identity==(info.st_dev,info.st_ino):path.unlink()
            self.lease.update(token=None,closed_reason=reason);self._publish('REVOKED')
        return {'schema':'explorer_cooperative_cpu_actor_closed_v1','reason':reason,'requests':self.requests,
            'actor_instance_id':self.instance,'physical_execution_authorized':False,'hardware_commands_dispatched':False}


class NativeActorClient:
    """A client pins one exact actor instance. It cannot follow a replacement."""
    def __init__(self,job_id,job_root,binding,*,actor_instance_id,valid,clock=time.monotonic_ns,socket_root=None):
        self.job_id,self.root,self.binding=_job(job_id),Path(job_root),deepcopy(binding)
        self.instance,self.valid,self.clock,self.socket_root=actor_instance_id,valid,clock,socket_root
    def _lease(self):
        value=read_lease(self.root);now=self.clock()
        if (not self.valid(value) or value.get('schema')!='explorer_cooperative_cpu_actor_lease_v1' or
                value.get('job_id')!=self.job_id or value.get('actor_instance_id')!=self.instance or
                value.get('binding_sha256')!=digest(self.binding) or value.get('binding')!=self.binding or
                value.get('phase') not in ('READY','BUSY') or not isinstance(value.get('token'),str) or
                not value.get('ready_monotonic_ns',now+1)<=now<value.get('expires_monotonic_ns',0) or
                not 0<=now-value.get('heartbeat_monotonic_ns',0)<=2_000_000_000 or
                worker_identity((value.get('worker') or {}).get('pid',-1))!=value.get('worker')):
            raise ValueError('Current exact scheduled warm actor lease unavailable')
        return value
    def predict(self,role):
        lease=self._lease();request_id=uuid.uuid4().hex
        body={'schema':'explorer_cooperative_cpu_actor_request_v1','token':lease['token'],'request_monotonic_ns':self.clock(),
            'actor_instance_id':self.instance,'binding_sha256':digest(self.binding),'request_id':request_id,'operation':'predict','role':role}
        begin=self.clock()
        with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as connection:
            connection.settimeout(2);connection.connect(str(socket_path(self.job_id,socket_root=self.socket_root)))
            pid,uid,_=struct.unpack('3i',connection.getsockopt(socket.SOL_SOCKET,socket.SO_PEERCRED,12))
            if uid!=os.getuid() or pid!=lease['worker']['pid']:raise ValueError('Actual private actor process identity differs')
            connection.sendall(canonical(body)+b'\n');reply=_receive(connection,REPLY_LIMIT)
        current=self._lease()
        if (self.clock()-begin>2_000_000_000 or current.get('token')!=lease['token'] or
                reply.get('schema')!='explorer_cooperative_cpu_actor_reply_v1' or reply.get('request_id')!=request_id or
                reply.get('actor_instance_id')!=self.instance or reply.get('binding_sha256')!=digest(self.binding) or
                reply.get('role')!=role or reply.get('dispatch_performed') is not False or
                reply.get('physical_execution_authorized') is not False):
            raise ValueError('Native inference reply expired/revoked or correlation differs')
        return reply['prediction']
