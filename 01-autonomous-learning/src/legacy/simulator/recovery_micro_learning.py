"""Bounded BC/official ACT comparison after a successful physical teacher gate.

New experimental weights only. No task clock, teacher labels or hidden state
enter either policy. This small diagnostic is not a qualified robot package.
"""
import argparse
import hashlib
import json
from pathlib import Path
import random
import shutil
import time

import numpy as np
from PIL import Image
import torch
from torch import nn

from .learning import sha256, write_json
from .recovery_pilot import PROTOCOL, collect_teacher
from .environment import ExplorerEnv
from .recovery_contracts import SCHEMA, validate_teacher_rows, safe_image_path, validate_stats


def decode_relative_bc(logits, anchor):
    """One-step action-space parameterization, not clipping a servo request.

    Network outputs are dimensionless logits. The entire supported action
    interval is defined from known command limits BEFORE selecting an action.
    No object pose, teacher phase, target clock, or measured joint is used.
    Independent physical raw/issued guards still inspect the decoded request.
    """
    low=anchor.new_tensor([0.,0.,0.,0.,0.,30.])
    high=anchor.new_tensor([180.,180.,180.,180.,270.,170.])
    lower=torch.maximum(anchor[:,:6]-2.,low)
    upper=torch.minimum(anchor[:,:6]+2.,high)
    arm=lower+(upper-lower)*torch.sigmoid(logits[:,:6])
    limits=anchor.new_tensor([.2,.2,.6])
    limits=torch.nextafter(limits,torch.zeros_like(limits))
    base=torch.tanh(logits[:,6:])*limits
    return torch.cat([arm,base],dim=-1)


class ReactiveBC(nn.Module):
    def __init__(self):
        super().__init__()
        self.vision = nn.Sequential(nn.Conv2d(3, 16, 5, 4, 2), nn.ReLU(),
            nn.Conv2d(16, 32, 3, 2, 1), nn.ReLU(), nn.AdaptiveAvgPool2d((3, 4)), nn.Flatten())
        self.head = nn.Sequential(nn.Linear(384+9, 256), nn.ReLU(), nn.Linear(256, 256),
            nn.ReLU(), nn.Linear(256, 9))

    def forward(self, state, image):
        return self.head(torch.cat([state, self.vision(image)], dim=1))


def dataset(folder, *, episode_name=None):
    states=[]; actions=[]; images=[]; horizons=[]; pads=[]; hashes={}
    for episode in sorted(p for p in Path(folder).glob('teacher-*')
                          if episode_name is None or p.name == episode_name):
        result=json.loads((episode/'result.json').read_text())
        if result['success'] is not True or result['collision_evidence']:
            raise ValueError('Teacher failed physical gate')
        rows=[json.loads(line) for line in (episode/'samples.jsonl').read_text().splitlines()]
        has_prefix=any(row.get('training_target_valid') is False for row in rows)
        if has_prefix:
            prefix=result.get('student_prefix',{})
            if not prefix.get('student_targets_masked') or prefix.get('verified_steps')!=prefix.get('steps'):
                raise ValueError('Unverified student prefix cannot enter a correction dataset')
            first_expert=next((i for i,row in enumerate(rows) if row.get('training_target_valid') is True),len(rows))
            if any(row.get('training_target_valid') is not True for row in rows[first_expert:]):
                raise ValueError('Only one leading masked student segment is supported')
        validate_teacher_rows(rows, ExplorerEnv.validate_command,require_expert=not has_prefix)
        hashes[str(episode/'samples.jsonl')]=sha256(episode/'samples.jsonl')
        for index,row in enumerate(rows):
            path=safe_image_path(episode, row['image'])
            if sha256(path)!=row['image_sha256']:
                raise ValueError('Teacher exposure changed')
            if row['training_target_valid'] is False:
                # Retained for provenance; never a teacher target or chunk.
                pass
            else:
                _append_expert_row(rows,index,row,episode,states,actions,images,horizons,pads)
    if not states:
        raise ValueError('No validated teacher data')
    return dict(state=torch.tensor(np.asarray(states),dtype=torch.float32),
        action=torch.tensor(np.asarray(actions),dtype=torch.float32),
        images=torch.from_numpy(np.asarray(images)).permute(0,3,1,2),
        horizons=torch.tensor(np.asarray(horizons),dtype=torch.float32),
        pads=torch.tensor(pads,dtype=torch.bool),sha256=hashes)


def _append_expert_row(rows,index,row,episode,states,actions,images,horizons,pads):
            state=np.asarray(row['observation_state'],float); action=np.asarray(row['applied_action'],float)
            if (not row['training_target_valid'] or max(abs(action[:6]-state[:6]))>2.000001
                    or not np.isfinite(np.r_[state,action]).all()):
                raise ValueError('Teacher target violates common runtime contract')
            path=safe_image_path(episode, row['image'])
            if sha256(path)!=row['image_sha256']:
                raise ValueError('Teacher exposure changed')
            if abs(row['post_simulation_time']-row['simulation_time']-.05)>1e-7:
                raise ValueError('Action/physics cadence mismatch')
            states.append(state);actions.append(action)
            with Image.open(path) as original:
                image = np.asarray(original.convert('RGB'))
            if image.shape != (240, 320, 3):
                raise ValueError('Teacher image must match policy HWC 240x320x3')
            images.append(image)
            horizons.append([rows[min(index+j,len(rows)-1)]['applied_action'] for j in range(20)])
            pads.append([index+j>=len(rows) for j in range(20)])


def build(kind):
    if kind=='bc':
        return ReactiveBC()
    from lerobot.configs.types import PolicyFeature, FeatureType
    from lerobot.policies.act.configuration_act import ACTConfig
    from lerobot.policies.act.modeling_act import ACTPolicy
    return ACTPolicy(ACTConfig(input_features={
        'observation.state':PolicyFeature(type=FeatureType.STATE,shape=(9,)),
        'observation.images.wrist':PolicyFeature(type=FeatureType.VISUAL,shape=(3,240,320))},
        output_features={'action':PolicyFeature(type=FeatureType.ACTION,shape=(9,))},
        chunk_size=20,n_action_steps=1,use_vae=False,pretrained_backbone_weights=None,
        dim_model=128,n_heads=4,dim_feedforward=512,n_encoder_layers=2,n_decoder_layers=1,dropout=0.))


class MicroActor:
    trained=True
    returns_issued_commands=False

    def __init__(self,model,kind,stats,device,representation='absolute_normalized'):
        validate_stats([v.detach().cpu().numpy() for v in stats])
        self.model=model.eval();self.kind=kind;self.stats=stats;self.device=device
        self.last_exposure={}
        self.representation=representation

    def reset(self):
        self.last_exposure={}
        if self.kind=='act':
            self.model.reset()

    def __call__(self,state,rgb):
        if np.asarray(state).shape != (9,) or not np.isfinite(state).all():
            raise ValueError('Invalid policy state')
        if np.asarray(rgb).shape != (240,320,3) or np.asarray(rgb).dtype != np.uint8:
            raise ValueError('Policy expects uint8 HWC 240x320x3 RGB')
        mean,std,amean,astd=[v.to(self.device) for v in self.stats]
        observation=torch.tensor(np.asarray(state)[None],dtype=torch.float32,device=self.device)
        image=torch.from_numpy(rgb.copy()).permute(2,0,1).float()[None].to(self.device)/255.
        normalized=(observation-mean)/std
        with torch.no_grad():
            chunk=(self.model(normalized,image)[:,None] if self.kind=='bc' else
                self.model.predict_action_chunk({'observation.state':normalized,'observation.images.wrist':image}))
            physical=(decode_relative_bc(chunk[:,0],observation)[:,None]
                if self.representation=='relative_support' else chunk*astd+amean)
        self.last_exposure=dict(normalized_state=normalized.cpu().tolist(),
            full_normalized_chunk=chunk.cpu().tolist(),full_physical_chunk=physical.cpu().tolist(),
            task_clock=False,execution_horizon=1,normalizers=[v.cpu().tolist() for v in self.stats])
        self.last_exposure.update(action_representation=self.representation,
            network_output_units='dimensionless_logits' if self.representation=='relative_support' else 'normalized_absolute_action')
        if self.representation=='relative_support':
            self.last_exposure['full_network_chunk']=self.last_exposure.pop('full_normalized_chunk')
        return physical[0,0].cpu().numpy()


def train(data,kind,output,*,updates=600,wall_budget_s=240,objective='normalized_l1',representation='absolute_normalized'):
    if objective not in ('normalized_l1','physical_l1') or (kind!='bc' and objective!='normalized_l1'):
        raise ValueError('This controlled objective ablation supports BC only')
    if representation not in ('absolute_normalized','relative_support') or (kind!='bc' and representation!='absolute_normalized'):
        raise ValueError('Relative support is a one-step BC experiment only')
    output=Path(output);output.mkdir(parents=True,exist_ok=False)
    torch.manual_seed(20261020);np.random.seed(20261020);random.seed(20261020)
    torch.set_num_threads(1)
    device=torch.device('mps' if torch.backends.mps.is_available() else 'cpu')
    model=build(kind).to(device)
    stats=[data['state'].mean(0),data['state'].std(0).clamp_min(1.),
        data['action'].mean(0),data['action'].std(0).clamp_min(1.)]
    mean,std,amean,astd=stats
    optimizer=torch.optim.AdamW(model.parameters(),lr=.001 if kind=='bc' else .0001,weight_decay=.0001)
    protocol=dict(kind=kind,maximum_updates=updates,wall_budget_s=wall_budget_s,batch_size=16,
        seed=20261020,task_clock=False,action='absolute_servo_endpoint9',execution_horizon=1,
        chunk_size=1 if kind=='bc' else 20,device=str(device),source_sha256=data['sha256'],
        pretrained_weights=False,architecture_comparison_only=True,normalizers=[v.tolist() for v in stats])
    tolerances=torch.tensor([1.]*6+[.02,.02,.06],dtype=torch.float32)
    protocol.update(objective=objective,physical_error_scales=dict(
        arm_deg=[1.]*6,base_m_s=[.02,.02],yaw_rad_s=.06),
        loss_weights=(torch.ones(9) if objective=='normalized_l1' else astd/tolerances).tolist())
    protocol.update(action_representation=representation,
        raw_guard_unchanged=True,decoder_no_post_prediction_clip=True)
    write_json(output/'protocol.json',protocol)
    log=[];started=time.monotonic();model.train()
    initial={name:value.detach().cpu().clone() for name,value in model.state_dict().items()}
    torch.save(initial,output/'initial-weights.pt')
    for step in range(updates):
        if time.monotonic()-started>wall_budget_s:
            break
        index=torch.randint(len(data['state']),(16,))
        state=((data['state'][index]-mean)/std).to(device)
        image=data['images'][index].float().to(device)/255.
        optimizer.zero_grad(set_to_none=True)
        if kind=='bc':
            target=((data['action'][index]-amean)/astd).to(device)
            prediction=model(state,image)
            if representation=='relative_support':
                prediction=(decode_relative_bc(prediction,data['state'][index].to(device))-amean.to(device))/astd.to(device)
            if objective=='normalized_l1':
                loss=nn.functional.l1_loss(prediction,target)
            else:
                loss=((prediction-target).abs()*(astd/tolerances).to(device)).mean()
        else:
            target=((data['horizons'][index]-amean)/astd).to(device)
            loss,_=model({'observation.state':state,'observation.images.wrist':image,
                'action':target,'action_is_pad':data['pads'][index].to(device)})
        if not torch.isfinite(loss):
            raise ValueError('Non-finite micro training loss')
        loss.backward();nn.utils.clip_grad_norm_(model.parameters(),1.);optimizer.step()
        log.append(dict(update=step+1,loss=float(loss.detach().cpu()),elapsed_s=time.monotonic()-started,
            batch_index_sha256=hashlib.sha256(index.numpy().tobytes()).hexdigest()))
        if (step+1)%50==0:
            write_json(output/'progress.json',log[-1])
    if device.type=='mps':
        torch.mps.synchronize()
    final={name:value.detach().cpu() for name,value in model.state_dict().items()}
    changed=sum(not torch.equal(initial[name],value) for name,value in final.items())
    torch.save(dict(model=final,optimizer=optimizer.state_dict(),torch_rng=torch.get_rng_state(),
        numpy_rng=np.random.get_state(),python_rng=random.getstate(),
        mps_rng=torch.mps.get_rng_state() if device.type=='mps' else None,
        stats=stats,protocol=protocol,updates=len(log)),output/'checkpoint.pt')
    if kind=='act':
        model.config.save_pretrained(output/'act-config')
    write_json(output/'training.json',dict(updates=len(log),changed_tensors=changed,
        wall_time_s=time.monotonic()-started,log=log,checkpoint_sha256=sha256(output/'checkpoint.pt')))
    return MicroActor(model,kind,stats,device,representation)


def validate_training_source(source):
    """Large comparison and explicit tiny fit require the same verified gate."""
    source_protocol=json.loads((Path(source)/'protocol.json').read_text())
    if (source_protocol.get('schema') != SCHEMA or
            source_protocol.get('execution') != PROTOCOL['execution'] or
            source_protocol.get('evaluation') != PROTOCOL['evaluation']):
        raise ValueError('v2 teacher gate required; historical data are offline diagnostics only')
    source_manifest=json.loads((Path(source)/'source.json').read_text())['files']
    repo=Path(__file__).resolve().parents[1]
    required={'simulator/recovery_pilot.py','simulator/recovery_contracts.py',
        'simulator/room_policy.py','simulator/environment.py','src/mobile_policy_contract.py'}
    if not required.issubset(source_manifest):
        raise ValueError('Incomplete teacher executor provenance')
    for name,digest in source_manifest.items():
        target=(repo/name).resolve()
        if not target.is_relative_to(repo) or not target.is_file() or sha256(target)!=digest:
            raise ValueError('Teacher/runtime source mismatch: '+name)
    gate=json.loads((Path(source)/'state.json').read_text())
    if gate['state']!='teacher_gate_passed':
        raise ValueError('Physical teacher gate must pass before optimizer updates')


def run(source,output,baseline=None):
    if baseline is not None:
        raise ValueError('Baseline reuse disabled: rerun hold under the SAME v2 executor/evaluator')
    validate_training_source(source)
    output=Path(output);output.mkdir(parents=True,exist_ok=False)
    data=dataset(source)
    repo=Path(__file__).resolve().parents[1]
    source_hashes={}
    for name in ('simulator/recovery_micro_learning.py','simulator/recovery_pilot.py',
            'simulator/recovery_contracts.py','simulator/room_policy.py',
            'simulator/environment.py','simulator/room_validation.py','src/mobile_policy_contract.py'):
        target=output/'source'/name;target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(repo/name,target);source_hashes[name]=sha256(target)
    write_json(output/'source.json',source_hashes)
    write_json(output/'protocol.json',dict(teacher_source=str(source),**PROTOCOL,
        methods=['hold','bc_no_clock_h1','act_no_clock_h1'],updates_per_method=600,
        wall_budget_s_per_method=240,selection_scenes='training starts only',
        development_scenes='diagnostic only; not qualification holdout',
        registration_requires='two train and two development local grasps, no forbidden contacts'))
    methods={};champion=None
    for kind in ('hold','bc','act'):
        if kind=='hold' and baseline is not None:
            previous=json.loads((Path(baseline)/'state.json').read_text())['methods']['hold']
            if [row['seed'] for row in previous]!=PROTOCOL['training_seeds']+PROTOCOL['development_seeds']:
                raise ValueError('Baseline scenes differ')
            methods[kind]=previous
            write_json(output/'baseline-reuse.json',dict(source=str(baseline),
                state_sha256=sha256(Path(baseline)/'state.json'),reason='unchanged hold policy and physical contract'))
            write_json(output/'state.json',dict(state='comparing',methods=methods,selected=None))
        else:
            policy=(lambda state,rgb:state.copy()) if kind=='hold' else train(data,kind,output/kind)
            trials=[]
            seeds=PROTOCOL['training_seeds']+PROTOCOL['development_seeds']
            for index,seed in enumerate(seeds):
                result=collect_teacher(output/f'{kind}-{seed}',seed,PROTOCOL['positions_m'][index],
                    max_steps=400,wall_budget_s=180,policy=policy)
                trials.append(dict(seed=seed,success=result['success'],reason=result.get('reason'),
                    split='training' if index<2 else 'development',path=str(output/f'{kind}-{seed}')))
            methods[kind]=trials
            if kind!='hold' and all(row['success'] for row in trials):
                champion=kind
            write_json(output/'state.json',dict(state='comparing',methods=methods,selected=champion))
            if kind!='hold':
                del policy
                if torch.backends.mps.is_available():
                    torch.mps.empty_cache()
    report=dict(state='local_candidate' if champion else 'needs_targeted_correction',methods=methods,
        selected=champion,registered_room_skill=False,physical_ready=False,
        old_operator_pause_preserved=True,
        repeat_same_round=False,next_action='inspect saved student states, collect masked-prefix expert recovery; do not expand epochs blindly')
    write_json(output/'state.json',report)
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--teacher',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--baseline',type=Path)
    args=parser.parse_args()
    print(json.dumps(run(args.teacher,args.output,args.baseline),ensure_ascii=False))
