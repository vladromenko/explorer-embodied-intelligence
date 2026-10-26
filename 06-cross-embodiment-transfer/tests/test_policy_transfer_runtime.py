from pathlib import Path
import hashlib
import json
import os

import numpy as np
import pytest

from explorer_shared.kinematics import URDFChain
from explorer_sim.policy_transfer import JointMapping, OfficialACTSource, SO_NAMES, run_policy_transfer, sha, _files

ROOT = Path(__file__).resolve().parents[2]


def chain():
    return URDFChain.from_urdf(ROOT/'04-cross-embodiment-transfer/configs/so101/so101_new_calib.urdf','base_link','gripper_frame_link')


def profile(names=None,units=None):
    c=chain()
    return {'basis':'virtual_declared','source_urdf_sha256':c.source_sha256,'joint_names':list(names or SO_NAMES),
        'joint_units':units or ['radians']*5+['normalized_0_100'],
        'joints':{name:{'scale_rad_per_native_unit':1,'offset_rad':0} for name in c.names}}


def test_mapping_has_no_implicit_percent_to_radian_conversion():
    c=chain();units=['calibrated_normalized_-100_100']*5+['normalized_0_100']
    issues=JointMapping({}).problems(c,SO_NAMES,units,True,'real-calibration')
    assert all(name+':missing_verified_sign_scale_zero' in issues for name in c.names)
    b=profile(units=units)
    assert 'physical_source_requires_measured_radian_reference_pairs' in JointMapping(b).problems(c,SO_NAMES,units,True,'real-calibration')


def test_mapping_requires_bound_reference_measurements_for_every_joint(tmp_path):
    c=chain();b=profile();b.update(basis='measured_reference_pairs',source_calibration_sha256='cal')
    for name,item in b['joints'].items():
        evidence=tmp_path/(name+'.json')
        evidence.write_text(json.dumps({'kind':'joint_radian_reference_measurements','joint_name':name,
            'source_calibration_sha256':'cal','source_urdf_sha256':c.source_sha256,
            'measurement_basis':'fixture_reference_pose','observer':'unit-test-fixture',
            'reference_pairs':[[-.5,-.5],[.5,.5]],'uncertainty_rad':.001}))
        item.update(reference_pairs=[[-.5,-.5],[.5,.5]],reference_uncertainty_rad=.001,
            reference_evidence={'path':str(evidence),'sha256':sha(evidence)})
    assert not JointMapping(b).problems(c,SO_NAMES,b['joint_units'],True,'cal')
    b['joints']['wrist_roll']['offset_rad']=.1
    assert 'wrist_roll:missing_or_inconsistent_reference_measurements' in JointMapping(b).problems(c,SO_NAMES,b['joint_units'],True,'cal')


def test_declared_virtual_mapping_preserves_names_and_rejects_bounds():
    c=chain();b=profile();m=JointMapping(b)
    assert not m.problems(c,SO_NAMES,b['joint_units'],False)
    assert np.allclose(m.radians([0,-.6,.9,-.3,0,50],SO_NAMES,c),[0,-.6,.9,-.3,0])
    with pytest.raises(ValueError):m.radians([100]*6,SO_NAMES,c)
    b['joint_names'][0]='wrong'
    assert 'source_named_native_contract_mismatch' in m.problems(c,SO_NAMES,['radians']*5+['normalized_0_100'],False)


class Store:
    def __init__(self,path):self.root=Path(path)
    def update_job(self,*a,**kw):pass


def make_real_official_fixture(folder, steps=12):
    """Actual optimizer steps + saved non-identity official processors; no IO."""
    import torch
    from lerobot.configs import NormalizationMode
    from lerobot.policies.factory import make_pre_post_processors
    from explorer_sim.student import make_act_policy, IMAGE_KEY
    torch.set_num_threads(2)
    torch.manual_seed(431)
    source=Path(folder);dataset=source/'dataset';dataset.mkdir(parents=True,exist_ok=True)
    native=np.array([0,-.6,.9,-.3,0,50],np.float32)
    rgb=np.stack([np.full((64,64,3),70+i,np.uint8) for i in range(6)])
    np.savez_compressed(dataset/'episode.npz',rgb=rgb,state_native=np.tile(native,(6,1)))
    manifest={'format':'explorer_robot_episode_v1','action_names':list(SO_NAMES),'action_units':['radians']*5+['normalized_0_100'],
        'state_basis':'simulated_state','contains_physical_recordings':False,
        'task':'Synthetic technical hold fixture; no object skill or source hardware execution',
        'episodes':[{'id':'fixture','file':'episode.npz','sha256':sha(dataset/'episode.npz'),'split':'development'}]}
    (dataset/'manifest.json').write_text(json.dumps(manifest))
    settings={'seed':431,'device':'cpu','image_size':64,'chunk_size':4}
    policy=make_act_policy(settings,state_dim=6,action_dim=6)
    policy.config.normalization_mapping={name:NormalizationMode.MEAN_STD for name in ['VISUAL','STATE','ACTION']}
    stats={IMAGE_KEY:{'mean':torch.tensor([.485,.456,.406]).view(3,1,1),'std':torch.tensor([.229,.224,.225]).view(3,1,1)},
        'observation.state':{'mean':torch.tensor(native),'std':torch.ones(6)},
        'action':{'mean':torch.tensor(native),'std':torch.tensor([.005]*5+[.1])}}
    pre,post=make_pre_post_processors(policy.config,dataset_stats=stats)
    batch=pre({IMAGE_KEY:torch.from_numpy(rgb[0]).permute(2,0,1).float()/255,'observation.state':torch.from_numpy(native)})
    batch.update(action=torch.zeros(1,4,6),action_is_pad=torch.zeros(1,4,dtype=torch.bool))
    optimizer=torch.optim.Adam(policy.parameters(),lr=3e-4)
    before=next(policy.parameters()).detach().clone();losses=[]
    policy.train()
    for _ in range(steps):
        optimizer.zero_grad();loss,_=policy(batch);loss.backward();optimizer.step();losses.append(float(loss.detach()))
    assert not torch.equal(before,next(policy.parameters()))
    checkpoint=source/'checkpoint';policy.eval();policy.save_pretrained(checkpoint,push_to_hub=False)
    pre.save_pretrained(checkpoint,push_to_hub=False,config_filename='policy_preprocessor.json')
    post.save_pretrained(checkpoint,push_to_hub=False,config_filename='policy_postprocessor.json')
    mapping_path=source/'mapping.json';mapping_path.write_text(json.dumps(profile()))
    target=URDFChain.from_urdf(ROOT/'01-autonomous-learning/src/legacy/config/explorer.urdf','base_footprint','Gripping')
    options={'dataset_manifest':str(dataset/'manifest.json'),'source_episode_id':'fixture','checkpoint_path':str(checkpoint),
        'checkpoint_sha256':_files(checkpoint),'mapping_profile':str(mapping_path),'max_observations':4,
        'registration':{'provenance':'virtual_declared','source_base_from_object':chain().forward(native[:5]).tolist(),
            'target_base_from_object':target.forward([0,-.6,1,-.4,0]).tolist()}}
    contract=source/'checkpoint-contract.json'
    contract.write_text(json.dumps({'source_manifest_sha256':sha(dataset/'manifest.json'),
        'checkpoint_sha256':options['checkpoint_sha256'],'action_names':manifest['action_names'],
        'action_units':manifest['action_units'],'action_representation':'joint_position','state_basis':'simulated_state'}))
    options.update(checkpoint_contract_path=str(contract),checkpoint_contract_sha256=sha(contract))
    (source/'training-proof.json').write_text(json.dumps({'optimizer_steps':steps,'weights_changed':True,'losses':losses,'seed':431}))
    return options


@pytest.mark.skipif(os.environ.get('EXPLORER_RUN_POLICY_TRANSFER_INTEGRATION')!='1',reason='Explicit real ACT optimizer/MuJoCo integration')
def test_real_policy_processors_and_independent_target_dynamics(tmp_path):
    options=make_real_official_fixture(tmp_path/'source')
    original=dict(options['checkpoint_sha256'])
    result=run_policy_transfer(Store(tmp_path/'store'),{'id':'real-policy-chain','config':options},{'root':str(ROOT)})
    assert result['source_policy_executed'] and result['matching_official_processors_executed']
    assert result['status']=='virtual_evaluated' and result['independent_virtual_attempts']==4
    assert result['processor_origin']=='persisted_official_processor_pair' and not result['explorer_adapter_applied']
    assert _files(Path(options['checkpoint_path']))==original
    rows=json.loads((tmp_path/'store/jobs/real-policy-chain/policy-transfer-trace.json').read_text())
    assert all(row['independent_target_verification']['physics_steps']==400 for row in rows)
    assert all(row['independent_target_verification']['object_grasp_or_transfer_not_tested'] for row in rows)
    assert not result['physical_execution'] and not result['learned_object_skill_transfer_claimed']


def test_checkpoint_checksum_is_checked_before_load(tmp_path):
    (tmp_path/'config.json').write_text('{}')
    with pytest.raises(ValueError,match='SHA256'):OfficialACTSource(tmp_path,{'config.json':'wrong'},tmp_path/'out')
