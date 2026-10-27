"""ENGINEERING_GLUE: frozen TRAIN-only audit preparation / explicit CPU forward.

Default preparation never imports Torch. Audit is a separate reviewed invocation;
there is no optimizer, posterior execution, renderer, simulation or native owner.
"""
import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'shared')]
from explorer_shared.contracts import canonical
from explorer_shared.learning.sim_train_forward_audit import (
    SCHEMA, IMAGE_KEY, CELLS, require, file_sha, load_train_source, audit_source)

ORIGINAL_PROTOCOL_SHA = '1a042117e539a481543792e3e4f88f4bc634cbe9a34971f35b7ad9f94df77250'
EPISODE = 'target-v3-train-74104'
MIB = 1024**2


def write(path, value):
    path = Path(path)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with path.open('xb') as stream:
        stream.write(value if isinstance(value, bytes) else canonical(value))
        stream.flush()
        os.fsync(stream.fileno())


def source_inspection():
    dist = importlib.metadata.distribution('lerobot')
    require(dist.version == '0.6.1', 'Pinned installed LeRobot0.6.1 required')
    act = Path(dist.locate_file('lerobot/policies/act/modeling_act.py')).resolve()
    backend = ROOT / 'shared/explorer_shared/learning/act_backend.py'
    text, trainer = act.read_text(), backend.read_text()
    expected = ('if self.config.use_vae and ACTION in batch and self.training:',
                'latent_sample = mu + log_sigma_x2.div(2).exp() * torch.randn_like(mu)',
                'latent_sample = torch.zeros([batch_size, self.config.latent_dim]',
                'valid_mask = ~batch["action_is_pad"].unsqueeze(-1)',
                'num_valid = valid_mask.sum() * abs_err.shape[-1]',
                'l1_loss = (abs_err * valid_mask).sum() / num_valid.clamp_min(1)',
                'loss = l1_loss + mean_kld * self.config.kl_weight')
    require(all(value in text for value in expected) and 'policy.train()' in trainer
            and 'loss, terms = policy(batch)' in trainer, 'Installed loss/latent source differs; new review required')
    files = [act, Path(dist.locate_file('lerobot/policies/act/configuration_act.py')).resolve(), backend,
             ROOT / 'shared/explorer_shared/learning/sim_temporal_route.py',
             ROOT / 'shared/explorer_shared/learning/sim_object_route.py',
             ROOT / 'scripts/run_sim_temporal_learning.py']
    versions={}
    for name in ('lerobot','numpy','torch','torchvision'):
        package=importlib.metadata.distribution(name)
        versions[name]=package.version
        metadata=next(item for item in package.files if str(item).endswith('.dist-info/METADATA'))
        files.append(Path(package.locate_file(metadata)).resolve())
    files.append(Path(sys.executable).resolve())
    return dict(source_files_sha256={str(path): file_sha(path) for path in files},
                installed_package_version=dist.version, installed_source_upstream_identity_separately_asserted=False,
                installed_versions=versions,python_version=sys.version,original_OS_dependency_closure='PARTIAL_NOT_REQUALIFIED',
                train='policy.train(); posterior(mu,sigma) consumes TRAIN future action labels and pad mask; samples using torch.randn_like; dropout active',
                deployment='predict_action_chunk calls eval(); no action labels; latent=zeros[batch,16]; dropout disabled',
                training_loss='masked_L1_sum/(valid_action_slots*9) + 0.1*mean_batch(sum_latent_KL(q||N(0,I)))',
                audit_metric='float64 accumulated masked absolute errors/(valid_action_slots*9), not a stochastic training-loss replay',
                posterior_forward_executed=False, future_labels_available_to_actor=False)


def load_source(plan):
    return load_train_source(plan['training_manifest'], plan['training_manifest_sha256'],
                             episode_id=EPISODE, skill_id=plan['skill_id'], skill_version=3,
                             count=439, image_size=64)


def freeze(output, original):
    require(not subprocess.check_output(['git', '-C', str(ROOT), 'status', '--porcelain'], text=True).strip(), 'Commit clean new preparation source first')
    original = Path(original).resolve()
    require(file_sha(original) == ORIGINAL_PROTOCOL_SHA, 'Exact C-v2 provenance required')
    old = json.loads(original.read_bytes())
    inspection = source_inspection()
    plan = dict(schema=SCHEMA, source_commit=subprocess.check_output(['git', '-C', str(ROOT), 'rev-parse', 'HEAD'], text=True).strip(),
                original_protocol=str(original), original_protocol_sha256=ORIGINAL_PROTOCOL_SHA,
                training_manifest=old['training_source_manifest'], training_manifest_sha256=old['training_source_manifest_sha256'],
                episode_id=EPISODE, skill_id=old['skill_id'], training_source_skill_version=3,
                models=old['original_models'], cells=CELLS, original_rate_profile=old['original_rate_profile'],
                clock_horizon_s=old['clock_horizon_s'], frames=439, chunk_size=16, actor_state_dimension=10, action_dimension=9,
                query_batch_size=1, forward_calls_per_cell=439, total_forward_calls=1756,
                valid_target_action_slots_per_cell=sum(439-h for h in range(16)),
                horizon_ranges={'0..7': list(range(8)), '8..15': list(range(8,16))},
                resources=dict(cpu_quota=2,memory_max_bytes=3*1024**3,memory_swap_max=0,max_seconds_per_cell=900,
                               serial_cells=True,optimizer_device=None,forward_device='cpu',EGL=False,CUDA=False),
                actor_admission=dict(only_inputs=['actual_saved_TRAIN_RGB','causal_literal_prior9_plus_clock'],
                                     future_actions_phase_or_teacher_in_actor=False,posterior_forward_forbidden=True,
                                     strict_successful_train_only=True,masked9_channels_never_fabricated=True,
                                     label_float32_stored_view_matches_original_training=True),
                labels_and_phases='Scorer only, padding excluded; origin and target phases separately; native label error endpoint-vs-increment distinguished',
                baselines=dict(ZERO_NORMALIZED='constant normalized output0 (ABS midpoint; INCR zero delta/basevelocity)',
                               HOLD_ORIGIN_NATIVE9='constant origin prior9; future true prior never used for baseline'),
                policy='All4 same fixed checkpoints once; no selection/tuning using DEV/SEALED, no new weights/teacher/physics/dispatch. TRAIN audit is not mastery.',
                source_inspection=inspection,physical_io=False,optimizer_updates=0,closed_loop_success_evaluated=False)
    source = load_source(plan)
    sources = dict(old['source_files_sha256'])
    sources.update(source.provenance['source_files_sha256'])
    sources.update(inspection['source_files_sha256'])
    files = [Path(__file__), ROOT/'shared/explorer_shared/learning/sim_train_forward_audit.py',
             ROOT/'tests/test_sim_train_forward_audit.py', ROOT/'tests/test_train_forward_driver.py',
             ROOT/'research/protocols/TARGET_TRAIN_FORWARD_AUDIT_C_RU.md', original]
    for cell, model in plan['models'].items():
        require(model['optimizer_steps'] == 1200 and model['reload_max_abs_error'] == 0
                and model['settings'] == old['settings'], 'Original1200 models required')
        checkpoint = Path(model['checkpoint_path'])
        config = json.loads((checkpoint/'config.json').read_bytes())
        require(config['input_features']['observation.state']['shape'] == [10]
                and config['output_features']['action']['shape'] == [9]
                and config['chunk_size'] == 16 and config['use_vae'] is True
                and config['latent_dim'] == 16 and config['kl_weight'] == .1
                and config['device'] == 'cpu', 'Exact original ACT10/9 prior-latent config required')
        for name, checksum in model['checkpoint_files_sha256'].items():
            require(file_sha(checkpoint/name) == checksum, 'Original checkpoint changed')
            sources[str(checkpoint/name)] = checksum
        training = checkpoint.parent/'training-result.json'
        files.append(training)
        model['original_training_result'] = str(training)
    sources.update({str(path.resolve()): file_sha(path) for path in files})
    for path, checksum in sources.items():
        require(file_sha(path) == checksum, 'Existing source/input changed: '+path)
    plan['source_files_sha256'] = sources
    plan['admitted_source'] = source.provenance
    output = Path(output).resolve()
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    write(output/'protocol.json', plan)
    print(json.dumps(dict(protocol=str(output/'protocol.json'),sha256=file_sha(output/'protocol.json'),pins=len(sources),forward_calls=1756,forward_executed=False)))


def resources():
    relative = next(row.split('::',1)[1] for row in Path('/proc/self/cgroup').read_text().splitlines() if row.startswith('0::'))
    folder = Path('/sys/fs/cgroup')/relative.lstrip('/')
    return dict(pid=os.getpid(),cgroup=str(folder),limits={name:(folder/name).read_text().strip() for name in ('cpu.max','memory.max','memory.swap.max')},
                memory_peak_bytes=int((folder/'memory.peak').read_text()),memory_events=(folder/'memory.events').read_text())


def require_resources(value, unit):
    quota, period = value['limits']['cpu.max'].split()
    require(Path(value['cgroup']).name == unit+'.service' and quota != 'max' and int(quota) <= 2*int(period)
            and value['limits']['memory.max'] == str(3*1024**3) and value['limits']['memory.swap.max'] == '0', 'Own CPU<=2/3GiB/swap0 unit required before Torch import')


def checked(path, checksum, unit):
    path = Path(path).resolve()
    require(file_sha(path) == checksum, 'Frozen audit protocol differs')
    plan = json.loads(path.read_bytes())
    require(plan['schema'] == SCHEMA and plan['total_forward_calls'] == 1756 and plan['cells'] == CELLS
            and plan['episode_id'] == EPISODE and plan['frames'] == 439, 'Exact reviewed TRAIN-only protocol required')
    require(subprocess.check_output(['git','-C',str(ROOT),'rev-parse','HEAD'],text=True).strip() == plan['source_commit']
            and not subprocess.check_output(['git','-C',str(ROOT),'status','--porcelain'],text=True).strip(), 'Exact clean reviewed source required')
    for source, expected in plan['source_files_sha256'].items():
        require(file_sha(source) == expected, 'Immutable input/source/model changed: '+source)
    before = resources()
    require_resources(before,unit)
    return plan,before


def run_cell(plan, folder, cell):
    # Source admission precedes ALL Torch/policy imports.
    source = load_source(plan)
    os.environ.update(CUDA_VISIBLE_DEVICES='',OMP_NUM_THREADS='2',MKL_NUM_THREADS='2',OPENBLAS_NUM_THREADS='2',HF_HUB_OFFLINE='1')
    import numpy as np
    import torch
    from lerobot.policies.act.modeling_act import ACTPolicy
    from explorer_shared.learning.act_backend import prepare_rgb
    torch.set_num_threads(2)
    require(not torch.cuda.is_initialized(), 'CUDA initialized in CPU-only audit')
    require(str(torch.__version__)==plan['source_inspection']['installed_versions']['torch']
            and np.__version__==plan['source_inspection']['installed_versions']['numpy']
            and sys.version==plan['source_inspection']['python_version'], 'Frozen framework/Python runtime differs')
    model = plan['models'][cell]
    policy = ACTPolicy.from_pretrained(model['checkpoint_path'],local_files_only=True,strict=True).to('cpu').eval()
    policy.reset()

    def tensor_sha(value):
        return hashlib.sha256(value.detach().cpu().contiguous().numpy().tobytes()).hexdigest()

    def state_sha():
        return hashlib.sha256(canonical({name:dict(shape=list(value.shape),dtype=str(value.dtype),sha256=tensor_sha(value)) for name,value in sorted(policy.state_dict().items())})).hexdigest()

    counts = dict(latent_zero_checks=0,posterior_calls=0)
    def latent_zero(module,args):
        value = args[0]
        require(value.shape == (1,16) and torch.count_nonzero(value).item() == 0, 'Only actual all-zero prior latent allowed')
        counts['latent_zero_checks'] += 1
    def posterior_forbidden(module,args):
        counts['posterior_calls'] += 1
        raise ValueError('Posterior execution forbidden in deployment audit')
    hooks=[policy.model.encoder_latent_input_proj.register_forward_pre_hook(latent_zero),
           policy.model.vae_encoder.register_forward_pre_hook(posterior_forbidden)]
    initial_state = state_sha()
    initial_rng = tensor_sha(torch.get_rng_state())
    last = {}
    retained_outputs = {}
    def predict(actor):
        rgb = prepare_rgb(actor.rgb[None])
        state = torch.from_numpy(actor.state10.copy()[None])
        batch={IMAGE_KEY:rgb,'observation.state':state}
        require(set(batch)=={IMAGE_KEY,'observation.state'} and not policy.training, 'Actor input allowlist/eval boundary violated')
        last.clear()
        last.update(rgb_uint8_sha256=hashlib.sha256(actor.rgb.tobytes()).hexdigest(),prepared_RGB_tensor_sha256=tensor_sha(rgb),state10_tensor_sha256=tensor_sha(state))
        with torch.inference_mode():
            return policy.predict_action_chunk(batch)[0].cpu().numpy().copy()
    def retain(index,actor,prediction):
        path=folder/'predictions'/(str(index).zfill(6)+'.json')
        write(path,dict(origin=index,actor_input=dict(last),state10=actor.state10.tolist(),full_normalized_chunk9=prediction.tolist(),posterior_or_future_labels_in_actor=False))
        retained_outputs[path.name]=file_sha(path)
    try:
        result=audit_source(source,cell,plan['original_rate_profile'],plan['clock_horizon_s'],predict,retain)
        require(result['queries']==counts['latent_zero_checks']==439 and counts['posterior_calls']==0, 'Actual prior-latent query count differs')
        require(state_sha()==initial_state and tensor_sha(torch.get_rng_state())==initial_rng and not torch.cuda.is_initialized(), 'Model/RNG/device changed during eval')
        training=json.loads(Path(model['original_training_result']).read_bytes())
        result.update(cell=cell,checkpoint_sha256=model['checkpoint_sha256'],actual_latent_checks=counts,model_state_digest_before_after=initial_state,
                      Torch_RNG_digest_before_after=initial_rng,source_admission=source.provenance,source_inspection=plan['source_inspection'],
                      predictions_files_sha256=retained_outputs,
                      historical_training_last50_means={key:sum(row[key] for row in training['losses'][-50:])/50 for key in ('loss','l1_loss','kld_loss')},
                      historical_training_loss_not_equal_to_prior_forward_metric=True,policy_promoted=False)
        write(folder/'result.json',result)
        return result
    finally:
        for hook in hooks:hook.remove()


def summarize(plan, folder, root):
    rows={}
    for cell in CELLS:
        source=root/'cells'/cell
        require(not (source/'failure.json').exists(), 'Failed/partial audit cannot enter completed summary')
        receipt=json.loads((source/'completed.json').read_bytes())
        require(receipt['source_pins_revalidated'] is True and receipt['queries']==439
                and receipt['result_sha256']==file_sha(source/'result.json'), 'Bound completed audit required')
        result=json.loads((source/'result.json').read_bytes())
        require(result['cell']==cell and result['checkpoint_sha256']==plan['models'][cell]['checkpoint_sha256']
                and result['queries']==439 and result['actual_latent_checks']==dict(latent_zero_checks=439,posterior_calls=0)
                and result['optimizer_updates']==0 and result['future_labels_or_phase_in_actor'] is False,
                'Fixed-model/prior-only audit result differs')
        paths=sorted((source/'predictions').glob('*.json'))
        require([path.name for path in paths]==[str(i).zfill(6)+'.json' for i in range(439)], 'All439 raw query outputs required')
        require(set(result['predictions_files_sha256'])=={path.name for path in paths}
                and all(file_sha(path)==result['predictions_files_sha256'][path.name] for path in paths), 'Actual raw prediction bytes changed')
        rows[cell]=dict(result_sha256=file_sha(source/'result.json'),metrics=result['metrics'],checkpoint_sha256=result['checkpoint_sha256'])
    result=dict(schema=SCHEMA,cells=rows,total_forward_calls=1756,all_four_fixed_models_assessed=True,
                model_selection_performed=False,new_training_or_physics=False,physical_io=False,
                closed_loop_success_evaluated=False,whole_master_prompt_complete=False)
    write(folder/'summary.json',result)
    return result


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('stage',choices=['freeze','audit','summary'])
    for name in ('output','original','plan','sha','unit','cell'):parser.add_argument('--'+name)
    args=parser.parse_args()
    if args.stage=='freeze':return freeze(args.output,args.original)
    plan,before=checked(args.plan,args.sha,args.unit)
    if args.stage=='audit':require(args.cell in CELLS,'One registered fixed cell required')
    root=Path(args.plan).resolve().parent
    folder=root/'cells'/args.cell if args.stage=='audit' else root/'summary'
    folder.mkdir(mode=0o700,parents=True,exist_ok=False)
    write(folder/'started.json',dict(time=time.time(),resources=before,protocol_sha256=args.sha,cell=args.cell))
    try:
        result=run_cell(plan,folder,args.cell) if args.stage=='audit' else summarize(plan,folder,root)
        for source,expected in plan['source_files_sha256'].items():require(file_sha(source)==expected,'Source changed during audit')
        if args.stage=='audit':
            write(folder/'completed.json',dict(result_sha256=file_sha(folder/'result.json'),source_pins_revalidated=True,queries=result['queries'],physical_io=False))
            print(json.dumps(dict(cell=args.cell,queries=result['queries'],model_normalized_MAE=result['metrics']['MODEL_PRIOR_ZERO']['groups']['all']['normalized_MAE'],result_sha256=file_sha(folder/'result.json'),closed_loop_success_evaluated=False)))
        else:print(json.dumps(dict(summary_sha256=file_sha(folder/'summary.json'),total_forward_calls=1756,closed_loop_success_evaluated=False)))
    except BaseException as error:
        write(folder/'failure.json',dict(type=type(error).__name__,detail=str(error)))
        raise
    finally:write(folder/'resources-final.json',resources())


if __name__=='__main__':main()
