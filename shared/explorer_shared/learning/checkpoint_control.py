"""ENGINEERING_GLUE: durable optimizer pause protocol; no Torch/robot dependency.

A pause is acknowledged only after the actual optimizer/RNG/sampler checkpoint
is fsynced. SystemExit75 bypasses ordinary worker failure handlers and releases
process memory. The scheduler independently verifies receipt and process exit.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import time
import uuid

SCHEMA='explorer_optimizer_checkpoint_control_v1'
PAUSED_EXIT_CODE=75
DIRECTORIES={'act':'act_resume','diffusion':'diffusion_resume'}


class OptimizerPaused(SystemExit):
    def __init__(self):super().__init__(PAUSED_EXIT_CODE)


def sha(path):
    with Path(path).open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()


def worker_identity(pid=None):
    pid=os.getpid() if pid is None else pid
    try:
        tail=Path('/proc/'+str(pid)+'/stat').read_text().rsplit(')',1)[1].split()
        return dict(pid=pid,start_ticks=int(tail[19]),boot_id=Path('/proc/sys/kernel/random/boot_id').read_text().strip())
    except (OSError,ValueError,IndexError):return None


def read_json(path):
    path=Path(path)
    if not path.is_file():return None
    if path.is_symlink() or path.stat().st_size>65536:raise ValueError('Optimizer control record path/size is invalid')
    return json.loads(path.read_text())


def atomic_json(path,body):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_name('.'+path.name+'.'+uuid.uuid4().hex+'.tmp')
    try:
        descriptor=os.open(temporary,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
        with os.fdopen(descriptor,'wb') as stream:
            stream.write(json.dumps(body,sort_keys=True,allow_nan=False).encode());stream.flush();os.fsync(stream.fileno())
        os.replace(temporary,path)
        descriptor=os.open(path.parent,os.O_RDONLY|os.O_DIRECTORY)
        try:os.fsync(descriptor)
        finally:os.close(descriptor)
    finally:temporary.unlink(missing_ok=True)


def active_optimizer(folder,pid):
    active=read_json(Path(folder)/'optimizer-active.json')
    if (not isinstance(active,dict) or active.get('schema')!=SCHEMA or active.get('phase')!='optimizing'
            or active.get('worker')!=worker_identity(pid) or active.get('backend') not in DIRECTORIES
            or not re.fullmatch('[0-9a-f]{64}',str(active.get('signature','')))):
        return None
    return active


def request_pause(folder,active,request_id):
    if not isinstance(request_id,str) or not re.fullmatch('[0-9a-f]{32}',request_id):raise ValueError('Server pause request identity required')
    request=dict(schema=SCHEMA,operation='pause',request_id=request_id,worker=active['worker'],signature=active['signature'],backend=active['backend'])
    atomic_json(Path(folder)/'optimizer-control.json',request);return request


def clear_pause_request(folder):
    (Path(folder)/'optimizer-control.json').unlink(missing_ok=True)


def verified_pause_receipt(folder,request):
    folder=Path(folder);receipt=read_json(folder/'optimizer-paused.json')
    if not isinstance(receipt,dict) or any(receipt.get(key)!=request.get(key) for key in ('schema','request_id','worker','signature','backend')):raise ValueError('Actual optimizer pause receipt does not match this worker/request')
    if (receipt.get('optimizer_RNG_sampler_durably_saved') is not True or receipt.get('process_exit_code')!=PAUSED_EXIT_CODE
            or type(receipt.get('completed_steps')) is not int or receipt['completed_steps']<0 or receipt.get('physical_io') is not False):
        raise ValueError('Actual durable optimizer pause acknowledgement is incomplete')
    directory=folder/DIRECTORIES[request['backend']];pointer=read_json(directory/'latest.json')
    if (not pointer or sha(directory/'latest.json')!=receipt.get('checkpoint_pointer_sha256')
            or pointer.get('signature')!=request['signature'] or pointer.get('completed_steps')!=receipt.get('completed_steps')
            or not re.fullmatch('state-[0-9]{8}-[0-9a-f]{32}\\.pt',str(pointer.get('checkpoint','')))
            or sha(directory/pointer['checkpoint'])!=pointer.get('sha256') or pointer['sha256']!=receipt.get('checkpoint_sha256')):
        raise ValueError('Actual durable pause checkpoint/pointer checksum differs')
    return receipt


class OptimizerCheckpointControl:
    def __init__(self,folder,backend,signature,completed_steps):
        if backend not in DIRECTORIES or not re.fullmatch('[0-9a-f]{64}',str(signature)):raise ValueError('Only source-bound resumable optimizer may advertise checkpoint pause')
        self.folder=Path(folder);self.backend,self.signature,self.worker=backend,signature,worker_identity()
        self.announce('optimizing',completed_steps)

    def announce(self,phase,completed_steps):
        atomic_json(self.folder/'optimizer-active.json',dict(schema=SCHEMA,worker=self.worker,backend=self.backend,signature=self.signature,
            phase=phase,completed_steps=completed_steps,updated_monotonic_ns=time.monotonic_ns(),exact_optimizer_resume=True,
            step_basis='actual_finished_step_at_announcement_checkpoint_durability_is_in_latest_pointer',process_memory_released=False))

    def maybe_pause(self,completed_steps,save,validate_source):
        request=read_json(self.folder/'optimizer-control.json')
        if isinstance(request,dict) and request.get('operation')=='pause' and all(request.get(key)==value for key,value in
                (('schema',SCHEMA),('worker',self.worker),('signature',self.signature),('backend',self.backend))):
            validate_source()
            directory=self.folder/DIRECTORIES[self.backend];pointer=read_json(directory/'latest.json')
            if not pointer or pointer['completed_steps']!=completed_steps:pointer=save()
            receipt=dict(request,completed_steps=completed_steps,checkpoint_sha256=pointer['sha256'],checkpoint_pointer_sha256=sha(directory/'latest.json'),
                acknowledged_monotonic_ns=time.monotonic_ns(),optimizer_RNG_sampler_durably_saved=True,process_exit_code=PAUSED_EXIT_CODE,
                memory_release_basis='process_exit_must_be_verified_by_supervisor',physical_io=False)
            atomic_json(self.folder/'optimizer-paused.json',receipt);self.announce('paused_checkpoint_saved',completed_steps)
            raise OptimizerPaused()
