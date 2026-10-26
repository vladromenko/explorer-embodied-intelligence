"""Real process exit/RAM release + stochastic optimizer restart, no devices."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid

import numpy as np
import psutil
import pytest
import torch

from explorer_shared.learning.checkpoint_control import (active_optimizer,request_pause,verified_pause_receipt,PAUSED_EXIT_CODE)

ROOT=Path(__file__).parents[1]


def wait_file(path,process,timeout=30):
    began=time.monotonic()
    while not path.is_file():
        if process.poll() is not None:raise AssertionError('Fixture exited before durable pause boundary')
        if time.monotonic()-began>timeout:raise TimeoutError('Isolated fixture did not reach its checkpoint')
        time.sleep(.02)


def same(left,right):
    if isinstance(left,torch.Tensor):assert torch.equal(left,right)
    elif isinstance(left,dict):
        assert left.keys()==right.keys()
        for key in left:same(left[key],right[key])
    elif isinstance(left,(list,tuple)):
        assert len(left)==len(right)
        for a,b in zip(left,right):same(a,b)
    else:assert left==right


@pytest.mark.parametrize('backend',['act','diffusion'])
def test_actual_process_checkpoint_pause_exit_and_resume_preserve_stochastic_weights_optimizer_and_losses(tmp_path,backend):
    if backend=='diffusion' and importlib.util.find_spec('diffusers') is None:pytest.skip('Use isolated optional Diffusion interpreter for this actual process proof')
    source=tmp_path/'immutable.npy';np.save(source,np.arange(14,dtype=np.float32).reshape(7,2)/20)
    environment=dict(os.environ,PYTHONPATH=os.pathsep.join(str(ROOT/name) for name in ('shared','00-control-center/src','01-autonomous-learning/src')),OMP_NUM_THREADS='2',MKL_NUM_THREADS='2')
    def launch(folder,*,pause=False,restart=False):
        folder.mkdir();log=(folder/'worker.log').open('wb')
        command=[sys.executable,str(ROOT/'tests/fixtures/checkpoint_pause_worker.py'),'--backend',backend,'--folder',str(folder),'--source',str(source),
            '--initial-seed','999' if restart else '71','--wait-step','4' if pause else '0']
        process=subprocess.Popen(command,env=environment,stdout=log,stderr=subprocess.STDOUT);log.close();return process
    full=tmp_path/'full';reference=launch(full)
    assert reference.wait(timeout=40)==0,(full/'worker.log').read_text()
    paused=tmp_path/'paused';worker=launch(paused,pause=True)
    try:
        wait_file(paused/'at-step.json',worker);resident=psutil.Process(worker.pid).memory_info().rss
        active=active_optimizer(paused,worker.pid);assert active and active['backend']==backend
        request=request_pause(paused,active,uuid.uuid4().hex);(paused/'release-step').touch()
        assert worker.wait(timeout=15)==PAUSED_EXIT_CODE,(paused/'worker.log').read_text()
        assert not psutil.pid_exists(worker.pid) and resident>0
        receipt=verified_pause_receipt(paused,request)
        assert receipt['completed_steps']==4 and receipt['optimizer_RNG_sampler_durably_saved']
        assert not (paused/'final.pt').exists()
        # A new process with deliberately different initial weights/RNG resumes.
        resumed_log=paused/'resume-worker.log'
        with resumed_log.open('wb') as log:
            restarted=subprocess.Popen([sys.executable,str(ROOT/'tests/fixtures/checkpoint_pause_worker.py'),'--backend',backend,'--folder',str(paused),'--source',str(source),'--initial-seed','999'],env=environment,stdout=log,stderr=subprocess.STDOUT)
        assert restarted.wait(timeout=40)==0,resumed_log.read_text()
        expected=torch.load(full/'final.pt',weights_only=True);actual=torch.load(paused/'final.pt',weights_only=True)
        for key in ('model','optimizer','losses'):same(expected[key],actual[key])
        assert actual['resume']['resumed_from_step']==4 and actual['resume']['optimizer_steps_executed_this_process']==4
        assert expected['resume']['completed_steps']==actual['resume']['completed_steps']==8
        # Stale previous-process request cannot pause a different worker identity.
        assert active_optimizer(paused,os.getpid()) is None
        (tmp_path/'process-pause-receipt.json').write_text(json.dumps(dict(backend=backend,paused_pid=worker.pid,resumed_pid=restarted.pid,
            resident_bytes_before_pause=resident,old_process_gone=True,completed_steps=8,resumed_from_step=4,
            weights_optimizer_losses_exactly_equal=True,physical_io=False),indent=2))
    finally:
        if worker.poll() is None:worker.kill();worker.wait()
