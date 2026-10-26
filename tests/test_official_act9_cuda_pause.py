"""Opt-in actual CUDA new-process resume proof on the already verified CAD source."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import uuid

import psutil
import pytest
import torch

from explorer_shared.learning.checkpoint_control import active_optimizer, request_pause, verified_pause_receipt

ROOT = Path(__file__).parents[1]


def same(left, right):
    if isinstance(left, torch.Tensor):
        assert left.dtype == right.dtype and left.layout == right.layout and left.shape == right.shape
        assert torch.equal(left, right)
    elif isinstance(left, dict):
        assert left.keys() == right.keys()
        for key in left:
            same(left[key], right[key])
    elif isinstance(left, (tuple, list)):
        assert len(left) == len(right)
        for a, b in zip(left, right):
            same(a, b)
    else:
        assert left == right


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def checkpoint(folder):
    directory = folder / 'act_resume'
    pointer = json.loads((directory / 'latest.json').read_text())
    assert Path(pointer['checkpoint']).name == pointer['checkpoint']
    path = directory / pointer['checkpoint']
    assert sha(path) == pointer['sha256']
    payload = torch.load(path, map_location='cpu', weights_only=True)
    assert payload['signature'] == pointer['signature'] and payload['completed_steps'] == pointer['completed_steps']
    return payload


@pytest.mark.skipif(os.environ.get('EXPLORER_RUN_ACT9_CUDA_PAUSE') != '1', reason='Separate finite real CUDA process restart qualification')
def test_actual_official_cuda_ACT9_four_pause_exit_and_new_process_four_equal_uninterrupted_eight(tmp_path):
    manifest_path = Path(os.environ['EXPLORER_ACT9_CUDA_PAUSE_MANIFEST']).resolve()
    manifest = json.loads(manifest_path.read_text())
    assert manifest['format'] == 'explorer_robot_episode_v1'
    assert manifest['source'] == 'contact_verified_scripted_truth_IK_not_PPO'
    assert manifest['state_basis'] == 'command_estimate' and not manifest['contains_physical_recordings']
    assert len(manifest['episodes']) == 1 and manifest['episodes'][0]['outcome']['success']
    assert manifest['episodes'][0]['split'] == 'train'
    sources = {str(manifest_path): sha(manifest_path)}
    for descriptor in manifest['episodes']:
        source = manifest_path.parent / descriptor['file']
        assert sha(source) == descriptor['sha256']
        sources[str(source)] = descriptor['sha256']
    environment = dict(os.environ, PYTHONPATH=os.pathsep.join(str(ROOT / name) for name in ('shared', '00-control-center/src', '01-autonomous-learning/src')),
        HF_HUB_OFFLINE='1', OMP_NUM_THREADS='2', MKL_NUM_THREADS='2')
    def launch(folder, *extra):
        folder.mkdir(exist_ok=True)
        with (folder / ('resume.log' if extra == ('--different-initialization',) else 'worker.log')).open('wb') as log:
            return subprocess.Popen([sys.executable, str(ROOT / 'tests/fixtures/official_act9_cuda_pause_worker.py'),
                '--manifest', str(manifest_path), '--folder', str(folder), *extra], env=environment, stdout=log, stderr=subprocess.STDOUT)
    reference, paused = tmp_path / 'reference', tmp_path / 'paused'
    full, worker, restarted = None, None, None
    try:
        full = launch(reference)
        assert full.wait(timeout=80) == 0, (reference / 'worker.log').read_text()
        worker = launch(paused, '--wait-step', '4')
        began = time.monotonic()
        while not (paused / 'at-step.json').is_file():
            if worker.poll() is not None:
                raise AssertionError((paused / 'worker.log').read_text())
            if time.monotonic() - began > 80:
                raise TimeoutError('Actual GPU optimizer did not reach four updates')
            time.sleep(.02)
        assert json.loads((paused / 'at-step.json').read_text())['completed_steps'] == 4
        active = active_optimizer(paused, worker.pid)
        assert active and active['backend'] == 'act'
        resident = psutil.Process(worker.pid).memory_info().rss
        request = request_pause(paused, active, uuid.uuid4().hex)
        (paused / 'release-step').touch()
        assert worker.wait(timeout=30) == 75, (paused / 'worker.log').read_text()
        assert not psutil.pid_exists(worker.pid)
        receipt = verified_pause_receipt(paused, request)
        assert receipt['completed_steps'] == 4 and not (paused / 'finished.json').exists()
        snapshot = tmp_path / 'pause-snapshot'
        snapshot.mkdir()
        pointer = json.loads((paused / 'act_resume/latest.json').read_text())
        for name in ('latest.json', pointer['checkpoint']):
            shutil.copyfile(paused / 'act_resume' / name, snapshot / name)
        assert sha(snapshot / pointer['checkpoint']) == receipt['checkpoint_sha256']
        assert sha(snapshot / 'latest.json') == receipt['checkpoint_pointer_sha256']
        restarted = launch(paused, '--different-initialization')
        assert restarted.wait(timeout=80) == 0, (paused / 'resume.log').read_text()
        expected, actual = checkpoint(reference), checkpoint(paused)
        for key in ('model', 'optimizer', 'losses', 'sampler', 'rng'):
            same(expected[key], actual[key])
        summary = json.loads((paused / 'finished.json').read_text())
        ref_summary = json.loads((reference / 'finished.json').read_text())
        assert summary['initial_weights_sha256'] != ref_summary['initial_weights_sha256']
        assert summary['final_weights_sha256'] == ref_summary['final_weights_sha256']
        assert summary['training_resume']['resumed_from_step'] == 4 and summary['training_resume']['optimizer_steps_executed_this_process'] == 4
        assert summary['parameter_device'].startswith('cuda') and summary['parameter_dtype'] == 'torch.float32' and not summary['tf32_enabled']
        assert all(sha(path) == expected_sha for path, expected_sha in sources.items())
        (tmp_path / 'qualification.json').write_text(json.dumps(dict(schema='explorer_official_act9_cuda_process_resume_qualification_v1',
            source_kind='SCRIPTED_SIMULATION', source_files_sha256=sources, reference=ref_summary, resumed=summary,
            paused_pid=worker.pid, resumed_pid=restarted.pid, old_process_gone=True, old_resident_bytes=resident,
            checkpoint_pause_receipt=receipt, model_optimizer_losses_all_RNG_sampler_exactly_equal=True,
            optimizer_steps=8, paused_completed_steps=4, updates_in_new_process=4, source_bytes_preserved=True,
            CUDA_process_resume_verified=True, physical_io=False, task_success_claimed=False,
            deterministic_candidate_only=summary['deterministic_candidate_only'],
            production_runtime_or_math_changed=False,
            scope='Same64/2/4 FP32 officialACT9 RGB+preceding command9 optimizer on existing CAD source; no other actor shapes/devices or queue-controller scope'), indent=2))
    finally:
        for process in (full, worker, restarted):
            if process is not None and process.poll() is None:
                process.kill()
                process.wait()
