"""ENGINEERING_GLUE: explicit audit B snapshot; retained A remains untouched.

run_cell is retained A source with ONLY the distribution/module version guard
replaced. All actor inputs, zero-latent checks, scoring and outputs are identical.
Preparation never imports Torch or executes torch/version.py.
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

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'shared'),str(ROOT/'scripts')]
import run_train_forward_audit as retained_A
from explorer_shared.learning.torch_build_identity import SCHEMA as BUILD_SCHEMA,pin_torch_build,check_torch_build

SCHEMA,CELLS,IMAGE_KEY=retained_A.SCHEMA,retained_A.CELLS,retained_A.IMAGE_KEY
require,file_sha,write=retained_A.require,retained_A.file_sha,retained_A.write
load_source,audit_source=retained_A.load_source,retained_A.audit_source
resources,summarize=retained_A.resources,retained_A.summarize
canonical=retained_A.canonical
A_PROTOCOL_SHA='bbd092ca6da934a484d5e18f16c718eda78ceb709c14c645c0d74501d5431eca'
A_SOURCE='7002a18c32c99472bad5e4cd22a3197dd127897a'


def freeze(output,original,prior):
    require(not subprocess.check_output(['git','-C',str(ROOT),'status','--porcelain'],text=True).strip(),'Commit clean B preparation source first')
    prior=Path(prior).resolve()
    require(file_sha(prior)==A_PROTOCOL_SHA,'Exact preserved A protocol required')
    a=json.loads(prior.read_bytes());require(a['source_commit']==A_SOURCE,'Original A source identity differs')
    for path,checksum in a['source_files_sha256'].items():require(file_sha(path)==checksum,'Preserved A2090 source/input changed: '+path)
    first=prior.parent/'cells/ZERO_ABS'
    require(len(list((prior.parent/'cells').glob('*/started.json')))==1
            and not (first/'completed.json').exists() and not list((first/'predictions').glob('*.json'))
            and all(not (prior.parent/'cells'/cell).exists() for cell in CELLS if cell!='ZERO_ABS'),
            'A must retain pre-model failure0queries +3NOT_STARTED')
    failure=json.loads((first/'failure.json').read_bytes())
    require(failure==dict(type='AuditRejected',detail='Frozen framework/Python runtime differs'),'Original A pre-model failure differs')
    dist=importlib.metadata.distribution('torch')
    item=next(path for path in dist.files if str(path).endswith('.dist-info/METADATA'))
    build=pin_torch_build(dist.locate_file('torch/version.py'),dist.locate_file(item),dist.version)
    require('torch' not in sys.modules,'Torch import forbidden during B preparation')
    output=Path(output).resolve();output.mkdir(mode=0o700,parents=True,exist_ok=False)
    try:
        # A's unchanged admission/freeze is itself preserved as an immutable base.
        retained_A.freeze(output/'base-admission',original)
        base=output/'base-admission/protocol.json';plan=json.loads(base.read_bytes())
        plan.update(protocol_revision='B_exact_module_and_distribution_identity',runtime_guard_schema=BUILD_SCHEMA,
                    base_admission_protocol=dict(path=str(base),sha256=file_sha(base)),
                    original_A=dict(protocol=str(prior),protocol_sha256=A_PROTOCOL_SHA,source_commit=A_SOURCE,
                                    outcome='PRE_MODEL_RUNTIME_GUARD_FAILURE',actual_forward_queries=0,remaining_NOT_STARTED=3,
                                    model_performance_evaluated=False,originals_unchanged=True))
        plan['source_inspection']['torch_module_build']=build
        files=[Path(__file__),ROOT/'shared/explorer_shared/learning/torch_build_identity.py',
               ROOT/'tests/test_torch_build_identity.py',ROOT/'research/protocols/TARGET_TRAIN_FORWARD_AUDIT_B_RU.md',
               prior,first/'started.json',first/'failure.json',first/'resources-final.json',first/'unit-launch-observed.json',
               prior.parent/'runtime-version-guard-diagnostic.json',base,Path(build['module_file']),Path(build['metadata_file'])]
        plan['source_files_sha256'].update({str(path.resolve()):file_sha(path) for path in files})
        require(all(plan['source_files_sha256'].get(path)==checksum for path,checksum in a['source_files_sha256'].items()),'All A2090 exact pins must be preserved')
        write(output/'protocol.json',plan)
        print(json.dumps(dict(protocol=str(output/'protocol.json'),sha256=file_sha(output/'protocol.json'),pins=len(plan['source_files_sha256']),forward_calls=1756,forward_executed=False)))
    except BaseException as error:
        write(output/'preparation-failure.json',dict(type=type(error).__name__,detail=str(error)))
        raise


def checked(path,expected,unit):
    plan,before=retained_A.checked(path,expected,unit)
    require(plan.get('protocol_revision')=='B_exact_module_and_distribution_identity'
            and plan.get('runtime_guard_schema')==BUILD_SCHEMA
            and plan['original_A']['protocol_sha256']==A_PROTOCOL_SHA,'Exact separately reviewed B protocol required')
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
    check_torch_build(plan['source_inspection']['torch_module_build'],
                      module_version=str(torch.__version__),distribution_version=importlib.metadata.version('torch'),
                      cuda_build=torch.version.cuda,git_version=torch.version.git_version,module_file=torch.version.__file__)
    require(np.__version__==plan['source_inspection']['installed_versions']['numpy']
            and sys.version==plan['source_inspection']['python_version'], 'Frozen NumPy/Python runtime differs')
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

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('stage',choices=['freeze','audit','summary'])
    for name in ('output','original','prior','plan','sha','unit','cell'):parser.add_argument('--'+name)
    args=parser.parse_args()
    if args.stage=='freeze':return freeze(args.output,args.original,args.prior)
    plan,before=checked(args.plan,args.sha,args.unit)
    if args.stage=='audit':require(args.cell in CELLS,'One registered fixed cell required')
    root=Path(args.plan).resolve().parent
    folder=root/'cells'/args.cell if args.stage=='audit' else root/'summary'
    folder.mkdir(mode=0o700,parents=True,exist_ok=False)
    write(folder/'started.json',dict(time=time.time(),resources=before,protocol_sha256=args.sha,cell=args.cell))
    try:
        result=run_cell(plan,folder,args.cell) if args.stage=='audit' else summarize(plan,folder,root)
        for source,expected in plan['source_files_sha256'].items():require(file_sha(source)==expected,'Source changed during audit B')
        if args.stage=='audit':
            write(folder/'completed.json',dict(result_sha256=file_sha(folder/'result.json'),source_pins_revalidated=True,queries=result['queries'],physical_io=False))
            print(json.dumps(dict(cell=args.cell,queries=result['queries'],model_normalized_MAE=result['metrics']['MODEL_PRIOR_ZERO']['groups']['all']['normalized_MAE'],result_sha256=file_sha(folder/'result.json'),closed_loop_success_evaluated=False)))
        else:print(json.dumps(dict(summary_sha256=file_sha(folder/'summary.json'),total_forward_calls=1756,closed_loop_success_evaluated=False)))
    except BaseException as error:
        write(folder/'failure.json',dict(type=type(error).__name__,detail=str(error)))
        raise
    finally:write(folder/'resources-final.json',resources())


if __name__=='__main__':main()
