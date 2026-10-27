"""Finite private queue-proof helpers; never configure a fleet or load a model."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import subprocess
import tarfile
import time

from explorer_shared.learning.checkpoint_control import worker_identity


def sha(path):
    with Path(path).open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()


def freeze_source(repo,root,source_files):
    repo,root=Path(repo).resolve(),Path(root)
    if subprocess.check_output(['git','-C',str(repo),'status','--porcelain'],text=True).strip():
        raise ValueError('Queue qualification requires a clean committed source tree')
    commit=subprocess.check_output(['git','-C',str(repo),'rev-parse','HEAD'],text=True).strip()
    root.mkdir(parents=True,exist_ok=True);archive=root/'source.tar';release=root/'releases'/commit
    with archive.open('xb') as output:
        subprocess.run(['git','-C',str(repo),'archive',commit],stdout=output,check=True)
    release.mkdir(parents=True)
    with tarfile.open(archive) as source:source.extractall(release,filter='data')
    (release/'RELEASE_COMMIT').write_text(commit+'\n')
    hashes={name:sha(release/name) for name in source_files}
    if any(sha(repo/name)!=expected for name,expected in hashes.items()):raise ValueError('Frozen archive differs from original committed source bytes')
    return release,{'source_commit':commit,'archive_sha256':sha(archive),'frozen_source_files_sha256':hashes,'release_path':str(release)}


def private_config(path,state):
    path=Path(path);state=Path(state).resolve()
    production=(Path.home()/'.local/share/explorer/state').resolve()
    if state==production or state.is_relative_to(production):raise ValueError('Qualification must not use production state')
    path.write_text(json.dumps({'data_dir':str(state),'nodes':[]}));path.chmod(0o600)
    return path


class QueueTrace:
    """Observe real Popen and scheduler side-table; no fake worker implementation."""
    def __init__(self,store):self.store=store;self.launches=[];self.admitted={};self.closed=[]
    def popen(self,command,**kwargs):
        process=subprocess.Popen(command,**kwargs)
        self.launches.append({'job_id':command[3],'command':list(command),'worker':worker_identity(process.pid),
            'pythonpath':kwargs['env'].get('PYTHONPATH'),'lease_nonce':kwargs['env'].get('EXPLORER_WORKER_LEASE_NONCE'),
            'process':process})
        return process
    def observe(self):
        for launch in self.launches:
            identity=launch['job_id'];record=self.store.schedule_status(identity);runtime=record['runtime']
            if runtime.get('bootstrap_admitted') is True and runtime.get('worker')==launch['worker']:
                self.admitted[identity]=deepcopy(record)
            if launch['process'].poll() is not None and identity not in self.closed:self.closed.append(identity)
    def public(self):
        return [{key:value for key,value in launch.items() if key!='process'} for launch in self.launches]


def pump(supervisor,trace,predicate,*,timeout=180,required_job_id=None):
    began=time.monotonic()
    while not predicate():
        if time.monotonic()-began>timeout:raise TimeoutError('Private native02 queue did not reach the expected actual process state')
        supervisor.tick();trace.observe()
        if required_job_id is not None:
            job=trace.store.job(required_job_id)
            if job['status'] in ('failed','cancelled'):raise AssertionError('Actual queued worker failed before admission: '+str(job))
        time.sleep(.02)
    trace.observe()


def stop_fixture_workers(supervisor):
    for process in list(supervisor.processes.values()):
        if process.poll() is None:
            process.terminate()
            try:process.wait(timeout=5)
            except subprocess.TimeoutExpired:process.kill();process.wait()
