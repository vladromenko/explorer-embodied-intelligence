"""Actual official networks, generated weights: selective reuse, no optimizer/IO."""
from copy import deepcopy
import json

import pytest

from explorer_shared.contracts import canonical,digest
from explorer_shared.learning.artifacts import sha,state_digest
from explorer_shared.learning.act_backend import make_act_policy
from explorer_shared.learning.rosmaster_skill_features import PipelineBlocked
from explorer_shared.learning.rosmaster_skill_pipeline import CanonicalCorpus
from explorer_shared.learning.transfer_target_corpus import TransferTargetCorpus
from explorer_shared.learning.transfer_target_training import TransferTrainingContext
from test_transfer_target_corpus import generated_target_corpus


def policies(tmp_path):
    pytest.importorskip('lerobot')
    import torch
    torch.set_num_threads(1)
    settings=dict(seed=41,act_steps=1,batch_size=2,image_size=32,chunk_size=2,device='cpu')
    source=make_act_policy(settings,6,6);folder=tmp_path/'GENERATED-untrained-source6'
    source.save_pretrained(folder)
    (folder/'explorer_preprocessing.json').write_bytes(canonical(dict(
        image_key='observation.images.overview',rgb_layout='NCHW',rgb_scale='uint8 / 255',
        mean=[.485,.456,.406],std=[.229,.224,.225])))
    target=make_act_policy(dict(settings,seed=43),106,9)
    hashes={path.name:sha(path) for path in folder.iterdir() if path.is_file()}
    path,binding,_=generated_target_corpus(tmp_path,train_count=5)
    binding['checkpoint_content_sha256']=digest(hashes)
    return source,target,folder,hashes,path,binding


def test_official_ACT6_backbone_reused_without_copying_any_target_head_or_RNG(tmp_path):
    import torch
    source,target,folder,hashes,path,binding=policies(tmp_path)
    context=TransferTrainingContext(source_model_binding=binding,target_version=2,
        target_domain='simulation',mode='vision_adapt',source_checkpoint=folder,source_checkpoint_sha256=hashes)
    before={name:value.clone() for name,value in target.state_dict().items()}
    rng=torch.get_rng_state().clone();receipt=context.initialize(target)
    assert torch.equal(rng,torch.get_rng_state())
    assert receipt['transferred_tensor_names'] and receipt['visual_backbone_only']
    assert not receipt['source_action_head_copied'] and not receipt['source_optimizer_copied']
    for name,value in target.state_dict().items():
        if name.startswith('model.backbone.'):
            assert torch.equal(value,source.state_dict()[name])
        else:
            assert torch.equal(value,before[name]),name
    assert any(not torch.equal(target.state_dict()[name],before[name]) for name in receipt['transferred_tensor_names'])
    assert all(sha(folder/name)==expected for name,expected in hashes.items())
    assert target.model.action_head.out_features==9 and source.model.action_head.out_features==6


def test_target_only_is_exact_noop_and_requires_bound_selection_before_trainer(tmp_path):
    source,target,folder,hashes,path,binding=policies(tmp_path)
    context=TransferTrainingContext(source_model_binding=binding,target_version=2,
        target_domain='simulation',mode='target_only')
    before=state_digest(target);receipt=context.initialize(target)
    assert state_digest(target)==before and not receipt['transferred_tensor_names']
    with pytest.raises(PipelineBlocked,match='SERVER_BOUND_TARGET_SELECTION'):
        context.validate_corpus(CanonicalCorpus(path))
    selected=TransferTargetCorpus(path,source_model_binding=binding,target_version=2,
        target_domain='simulation').save_selection(5)
    assert context.validate_corpus(CanonicalCorpus(selected['dataset_manifest']))['target_action_dimension']==9
    changed=json.loads(open(selected['dataset_manifest']).read());changed['transfer_target_selection']['source_model_binding']['checkpoint_content_sha256']='0'*64
    open(selected['dataset_manifest'],'wb').write(canonical(changed))
    with pytest.raises(PipelineBlocked,match='SERVER_BOUND_TARGET_SELECTION'):
        context.validate_corpus(CanonicalCorpus(selected['dataset_manifest']))


def test_changed_source_and_incompatible_preprocessing_fail_before_any_target_tensor_copy(tmp_path):
    source,target,folder,hashes,path,binding=policies(tmp_path)
    context=TransferTrainingContext(source_model_binding=binding,target_version=2,
        target_domain='simulation',mode='vision_adapt',source_checkpoint=folder,source_checkpoint_sha256=hashes)
    before=state_digest(target)
    (folder/'explorer_preprocessing.json').write_bytes(b'{}')
    with pytest.raises(PipelineBlocked,match='SOURCE_CHECKPOINT_CHANGED'):context.initialize(target)
    assert state_digest(target)==before
    hashes['explorer_preprocessing.json']=sha(folder/'explorer_preprocessing.json')
    binding['checkpoint_content_sha256']=digest(hashes)
    context=TransferTrainingContext(source_model_binding=binding,target_version=2,
        target_domain='simulation',mode='vision_adapt',source_checkpoint=folder,source_checkpoint_sha256=hashes)
    with pytest.raises(PipelineBlocked,match='VISUAL_PREPROCESSING_MISMATCH'):context.initialize(target)
    assert state_digest(target)==before
