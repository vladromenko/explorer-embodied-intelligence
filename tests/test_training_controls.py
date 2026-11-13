import json,copy
from pathlib import Path
import numpy as np
import pytest
from so101_lab.training_options import normalize_options,plan_split,preview
from so101_lab.training import RawEpisodeDataset,train
from so101_lab.features import FeatureContract
from test_training import fixture_dataset

def episodes(n=10):return [{'id':f'e{i}','session_id':f's{i}','frames':i+1} for i in range(n)]

def test_split_three_way_deterministic_whole_sessions():
    o=normalize_options({'validation_fraction':.2,'test_fraction':.2,'seed':91})
    p=plan_split(episodes(),o);assert p==plan_split(list(reversed(episodes())),o)
    assert len(p['train'])==6 and len(p['validation'])==2 and len(p['test'])==2
    for a,b in [('train','validation'),('train','test'),('validation','test')]:assert not set(p[a])&set(p[b])
    assert p['unit']=='session'

def test_final_fit_uses_every_selected_episode_without_holdout():
    o=normalize_options({'validation_fraction':0,'test_fraction':0,'seed':42})
    p=plan_split(episodes(),o)
    assert p['train']==[f'e{i}' for i in range(10)]
    assert p['validation']==[] and p['test']==[]
    assert p['train_samples']==sum(range(1,11))

def test_manual_split_rejects_leakage_empty_unknown():
    o={'split_mode':'manual','train_episodes':['e0'],'validation_episodes':['e1'],'test_episodes':['e2']}
    p=plan_split(episodes(),o);assert len(p['excluded'])==7
    with pytest.raises(ValueError,match='разные'):plan_split(episodes(),o|{'test_episodes':['e0']})
    with pytest.raises(ValueError,match='неизвестные'):plan_split(episodes(),o|{'test_episodes':['missing']})
    data=episodes();data[1]['session_id']='s0'
    with pytest.raises(ValueError,match='сессия'):plan_split(data,o|{'split_unit':'session'})
    with pytest.raises(ValueError):plan_split(episodes(2),{'test_fraction':.2})

@pytest.mark.parametrize('bad',[{'policy_type':'magic'},{'batch_size':0},{'batch_size':1.5},{'lr':float('nan')},{'test_fraction':.8,'validation_fraction':.2},{'image_size':33},{'warmup_steps':1000},{'device':'cpu','use_amp':True},{'unknown':1},{'tiny_model':True}])
def test_bad_public_options_rejected_before_worker(bad):
    with pytest.raises(ValueError):normalize_options(bad,public=True)

def test_preview_equals_real_partition_and_test_not_stats(tmp_path):
    path=fixture_dataset(tmp_path/'data',episodes=5)
    options={'tiny_model':True,'allow_test_data':True,'device':'cpu','validation_fraction':.2,'test_fraction':.2,'split_unit':'session','mode':'rgb_state'}
    view=preview(path,options,public=False)
    ds=RawEpisodeDataset(path,FeatureContract(mode='rgb_state'),allow_test_data=True)
    indices,plan=ds.partitions(view['options']);assert view['split']==plan
    assert set(plan['test']).isdisjoint(plan['train'])
    mean=ds.stats(indices['train'])['observation.state']['mean'].clone()
    for ei,ep in enumerate(ds.episodes):
        if ep['path'].name in plan['test']+plan['validation']:
            for row in ep['rows']:row['observation.state']=[9999.]*6
    assert np.allclose(ds.stats(indices['train'])['observation.state']['mean'],mean)


def test_actual_act_update_reload_test_and_resume(tmp_path):
    import torch
    from PIL import Image
    from so101_lab.inference import load_checkpoint
    path=fixture_dataset(tmp_path/'data',episodes=3)
    opts={'policy_type':'act','tiny_model':True,'allow_test_data':True,'device':'cpu','mode':'sam_objects','steps':2,'chunk_size':2,'batch_size':1,'image_size':32,'eval_every':1,'test_fraction':.3,'validation_fraction':.3,'seed':17,'gradient_accumulation':2}
    r=train(path,tmp_path/'act',opts)
    assert np.isfinite(r['loss']) and np.isfinite(r['test_loss']);assert r['state_projection_update_max_abs']>0
    ck=Path(r['checkpoint']);cfg=json.loads((ck/'pretrained_model/config.json').read_text());assert cfg['type']=='act' and cfg['n_action_steps']==2
    first=load_checkpoint(ck,allow_technical=True);second=load_checkpoint(ck,allow_technical=True)
    ep=path/'episodes/ep000';row=json.loads((ep/'frames.jsonl').read_text().splitlines()[0])
    from so101_lab.annotations import load_frame_annotations
    ann=load_frame_annotations(ep,row,allow_test=True)
    images={c:np.asarray(Image.open(ep/x['path'])) for c,x in row['cameras'].items()}
    a=first.predict(row,images,ann,allow_test=True);b=second.predict(row,images,ann,allow_test=True)
    assert np.allclose(a,b,atol=1e-7)
    prepared=first.prepare_observation(row,images,ann,'',allow_test=True)
    changed=copy.deepcopy(ann);changed['cameras']['top']['objects'][0]['center_xy'][0]+=5
    altered=first.prepare_observation(row,images,changed,'',allow_test=True)
    assert not torch.allclose(first.policy.model.encoder_robot_state_input_proj(prepared['observation.state']),first.policy.model.encoder_robot_state_input_proj(altered['observation.state']))
    del first,second
    resumed=train(path,tmp_path/'resumed',opts|{'steps':3,'resume_from':str(ck)})
    assert resumed['step']==3 and resumed['policy_type']=='act'

def test_data_loader_worker_uses_only_selected_training_frames(tmp_path):
    from so101_lab.training import training_batches
    path=fixture_dataset(tmp_path/'data',episodes=3)
    ds=RawEpisodeDataset(path,FeatureContract(mode='rgb_state'),allow_test_data=True)
    iterator=training_batches(ds,[0,1],1,42,0,1,{'num_workers':1,'gradient_accumulation':1})
    batch=next(iterator)
    assert batch['observation.state'].shape==(1,6)
    assert float(batch['observation.state'][0,0])<.5
    del iterator

def test_excluded_episode_missing_annotation_does_not_block_selection(tmp_path):
    path=fixture_dataset(tmp_path/'data',episodes=3)
    (path/'episodes/ep002/annotations/sam3-v1/frames.jsonl').unlink()
    opts={'tiny_model':True,'allow_test_data':True,'mode':'sam_objects','episodes':['ep000','ep001']}
    view=preview(path,opts,public=False)
    assert set(view['split']['train']+view['split']['validation'])=={'ep000','ep001'}

def test_resume_wrong_policy_rejected_by_preview_before_model_load(tmp_path):
    path=fixture_dataset(tmp_path/'data',episodes=3)
    checkpoint=tmp_path/'checkpoint';folder=checkpoint/'pretrained_model';folder.mkdir(parents=True)
    (checkpoint/'checkpoint.json').write_text(json.dumps({'status':'complete','step':1,'technical_test':True}))
    (folder/'config.json').write_text(json.dumps({'type':'act','chunk_size':2}))
    with pytest.raises(ValueError,match='Тип модели'):
        preview(path,{'tiny_model':True,'allow_test_data':True,'device':'cpu','steps':2,'resume_from':str(checkpoint),'policy_type':'smolvla'},public=False)

def test_action_execution_horizon_defaults_to_stock_full_chunk():
    options=normalize_options({'policy_type':'act','chunk_size':50})
    assert options['chunk_size']==50 and options['n_action_steps']==50
    assert options['temporal_ensemble_coeff'] is None
    with pytest.raises(ValueError,match='сглаживания ACT'):
        normalize_options({'policy_type':'act','chunk_size':50,'n_action_steps':10,'temporal_ensemble_coeff':.01})
    with pytest.raises(ValueError,match='не больше'):
        normalize_options({'policy_type':'act','chunk_size':10,'n_action_steps':11,'temporal_ensemble_coeff':None})
