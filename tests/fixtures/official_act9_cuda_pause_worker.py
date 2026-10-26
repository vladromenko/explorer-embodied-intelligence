"""ENGINEERING_GLUE: finite official ACT9 CUDA optimizer fixture, no robot IO."""
import argparse
import importlib.metadata
import json
import os
from pathlib import Path
import random
import resource
import time

import numpy as np
import torch

from explorer_shared.learning.act_backend import make_act_policy, optimize_act
from explorer_shared.learning.artifacts import state_digest, write_json
from explorer_shared.learning.runtime_profiles import ACT9_CUDA_DETERMINISTIC_PROFILE
from explorer_sim.recorded_training import RecordedChunks


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--folder', required=True)
    parser.add_argument('--manifest', required=True)
    parser.add_argument('--wait-step', type=int, default=0)
    parser.add_argument('--different-initialization', action='store_true')
    args = parser.parse_args()
    folder, manifest_path = Path(args.folder), Path(args.manifest)
    folder.mkdir(parents=True, exist_ok=True)
    manifest = json.loads(manifest_path.read_text())
    assert manifest['format'] == 'explorer_robot_episode_v1'
    assert manifest['source'] == 'contact_verified_scripted_truth_IK_not_PPO'
    assert len(manifest['action_names']) == 9 and manifest['state_basis'] == 'command_estimate'
    assert not manifest['contains_physical_recordings']
    assert manifest['episodes'] and all(row.get('split') == 'train' and row.get('outcome', {}).get('success') is True for row in manifest['episodes'])
    random.seed(71)
    np.random.seed(71)
    torch.set_num_threads(2)
    settings = dict(device='cuda', seed=321041, image_size=64, batch_size=2, chunk_size=4, act_steps=8,
        cuda_execution_profile=ACT9_CUDA_DETERMINISTIC_PROFILE)
    samples = RecordedChunks(manifest_path, manifest, manifest['episodes'], 4, 64)
    deterministic_candidate = os.environ.get('EXPLORER_ACT9_CUDA_DETERMINISTIC_CANDIDATE') == '1'
    if deterministic_candidate:
        assert os.environ.get('CUBLAS_WORKSPACE_CONFIG') == ':4096:8'
        torch.use_deterministic_algorithms(True)
        torch.backends.cudnn.deterministic = True
        torch.backends.cudnn.benchmark = False
        samples.resume_context['qualification_runtime_constraints'] = dict(candidate_only=True,
            deterministic_algorithms=True, cudnn_deterministic=True, cudnn_benchmark=False,
            cublas_workspace_config=os.environ['CUBLAS_WORKSPACE_CONFIG'])
    policy = make_act_policy(settings, 9, 9)
    if args.different_initialization:
        with torch.no_grad():
            next(policy.parameters()).add_(.25)
        random.seed(999)
        np.random.seed(999)
        torch.manual_seed(999)
    initial = state_digest(policy)

    def update(*unused):
        pointer = json.loads((folder / 'act_resume/latest.json').read_text())
        if pointer['completed_steps'] == args.wait_step:
            write_json(folder / 'at-step.json', dict(completed_steps=pointer['completed_steps']))
            began = time.monotonic()
            while not (folder / 'release-step').is_file():
                if time.monotonic() - began > 30:
                    raise TimeoutError('Isolated pause coordinator did not release its own worker')
                time.sleep(.01)

    optimizer, losses, _ = optimize_act(policy, samples, settings, folder, update, checkpoint_interval=4)
    write_json(folder / 'finished.json', dict(initial_weights_sha256=initial, final_weights_sha256=state_digest(policy),
        training_resume=optimizer.explorer_resume_info, optimizer_steps=len(losses), settings=settings,
        official_policy=type(policy).__module__ + '.' + type(policy).__qualname__,
        parameter_device=str(next(policy.parameters()).device), parameter_dtype=str(next(policy.parameters()).dtype),
        tf32_enabled=bool(torch.backends.cuda.matmul.allow_tf32 or torch.backends.cudnn.allow_tf32),
        versions={name: importlib.metadata.version(name) for name in ('torch', 'torchvision', 'lerobot', 'numpy')},
        process_peak_RSS_bytes=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024,
        cuda_peak_allocated_bytes=torch.cuda.max_memory_allocated(),
        cuda_peak_reserved_bytes=torch.cuda.max_memory_reserved(),
        runtime_profile_receipt=policy.explorer_runtime_profile_receipt,
        deterministic_candidate_only=deterministic_candidate,
        deterministic_algorithms=torch.are_deterministic_algorithms_enabled(),
        cudnn_deterministic=torch.backends.cudnn.deterministic,
        cublas_workspace_config=os.environ.get('CUBLAS_WORKSPACE_CONFIG'), physical_io=False))


if __name__ == '__main__':
    main()
