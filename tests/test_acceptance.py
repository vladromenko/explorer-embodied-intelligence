import copy,json
from pathlib import Path
import pytest
from so101_lab import preflight as p

def environment(monkeypatch):
    resources={'disk_free_bytes':100*1024**3,'memory_available_bytes':10*1024**3,'memory_total_bytes':16*1024**3,'compute_conflicts':[],'hardware_owners':[]}
    devices={name:{'path':name,'present':True,'accessible':True,'state':'AVAILABLE','owners':[]} for name in ('top','wrist','leader','follower')}
    monkeypatch.setattr(p,'snapshot',lambda:resources);monkeypatch.setattr(p,'device_inventory',lambda:devices);monkeypatch.setattr(p,'calibration_snapshot',lambda:{'files':{}});monkeypatch.setattr(p,'_health',lambda u:{'status':'PASS','http_status':200})
    return resources,devices

def test_preflight_gpu_busy_does_not_block_raw_recording(monkeypatch):
    r,d=environment(monkeypatch);r['compute_conflicts']=[{'pid':1,'name':'other'}]
    report=p.run_preflight();assert report['ready_to_record'];assert not report['ready_to_annotate_train'];assert not report['torque_commands_sent']

def test_preflight_missing_busy_and_disk_refuse(monkeypatch):
    r,d=environment(monkeypatch);d['wrist']['owners']=[{'pid':42,'name':'foreign'}];d['wrist']['state']='BUSY'
    assert p.run_preflight()['record_status']=='WAITING_FOR_HARDWARE'
    d['wrist']['owners']=[];d['wrist']['state']='MISSING';d['wrist']['present']=False
    assert not p.run_preflight()['ready_to_record']
    d['wrist'].update(present=True,state='AVAILABLE');r['disk_free_bytes']=1
    assert not p.run_preflight()['ready_to_record']

def test_preflight_bad_snapshot_refuses_no_hardware(monkeypatch):
    environment(monkeypatch)
    def bad():raise ValueError('hash mismatch')
    monkeypatch.setattr(p,'calibration_snapshot',bad)
    report=p.run_preflight();assert not report['ready_to_record'];assert report['calibration']['status']=='FAIL'

def test_integrity_rejects_processor_tamper_before_model_load(tmp_path):
    from so101_lab.inference import load_checkpoint
    from so101_lab.features import FeatureContract,sha256_file
    root=tmp_path/'step';folder=root/'pretrained_model';folder.mkdir(parents=True)
    (folder/'object_adapter.json').write_text(json.dumps(FeatureContract().to_dict()));(folder/'model.safetensors').write_bytes(b'fixture bytes')
    (folder/'policy_preprocessor.json').write_text('{}')
    manifest={'status':'complete','step':1,'technical_test':True,'feature_sha256':FeatureContract().digest,'model_sha256':sha256_file(folder/'model.safetensors'),'files_sha256':{'policy_preprocessor.json':sha256_file(folder/'policy_preprocessor.json')}}
    (root/'checkpoint.json').write_text(json.dumps(manifest));(folder/'policy_preprocessor.json').write_text('{"changed":true}')
    with pytest.raises(ValueError,match='checksum'):load_checkpoint(root,allow_technical=True)

def test_ambiguity_invalidates_numeric_slots(tmp_path):
    from test_training import fixture_dataset
    from so101_lab.features import FeatureContract,object_state
    from so101_lab.annotations import load_frame_annotations
    path=fixture_dataset(tmp_path/'data');ep=path/'episodes/ep000';frame=json.loads((ep/'frames.jsonl').read_text().splitlines()[0]);ann=load_frame_annotations(ep,frame,allow_test=True)
    for camera in ann['cameras'].values():
        for obj in camera['objects']:obj['category_ambiguous']=True
    vector=object_state(frame,ann,FeatureContract(),allow_test=True);assert vector[6:30]==[0.]*24

def test_real_sam_fractional_out_of_image_box_clipped_for_policy_only(tmp_path):
    from test_training import fixture_dataset
    from so101_lab.annotations import load_frame_annotations
    from so101_lab.features import object_state,FeatureContract
    path=fixture_dataset(tmp_path/'data');ep=path/'episodes/ep000';raw=json.loads((ep/'frames.jsonl').read_text().splitlines()[0]);ann=load_frame_annotations(ep,raw,allow_test=True)
    obj=ann['cameras']['top']['objects'][0];obj['bbox_xyxy']=[-.43,-.7,34.3,33.1]
    state=object_state(raw,ann,FeatureContract(),allow_test=True)
    assert state[6]==1. and state[9:11]==[1.,1.]
    assert obj['bbox_xyxy']==[-.43,-.7,34.3,33.1]

def test_operational_finalization_does_not_invalidate_training_lineage(tmp_path):
    from test_training import fixture_dataset
    from so101_lab.training import RawEpisodeDataset,_lineage
    from so101_lab.features import FeatureContract
    path=fixture_dataset(tmp_path/'data');contract=FeatureContract(mode='rgb_state');ds=RawEpisodeDataset(path,contract,allow_test_data=True);_,_,split=ds.split()
    first=_lineage(ds,contract,split,{'tiny_model':True},None,None)
    meta=json.loads((path/'dataset.json').read_text());meta['finalization']={'checked_at':'new time','status':'valid'};(path/'dataset.json').write_text(json.dumps(meta));ds2=RawEpisodeDataset(path,contract,allow_test_data=True)
    second=_lineage(ds2,contract,split,{'tiny_model':True},None,None)
    assert first['dataset_sha256']==second['dataset_sha256'];assert first['dataset_metadata_file_sha256']!=second['dataset_metadata_file_sha256']

def test_policy_finite_targets_are_saturated_and_invalid_values_rejected():
    from so101_lab.inference import validate_policy_target
    from so101_lab.hardware import JOINTS
    cal={'values':{'follower':{name:{'range_min':0,'range_max':4095} for name in JOINTS}}}
    assert validate_policy_target([0,0,0,0,0,50],cal)==[0,0,0,0,0,50]
    bounded=validate_policy_target([-180,0,0,0,0,101],cal)
    assert bounded==[-175,0,0,0,0,100]
    assert validate_policy_target([10000,0,0,0,0,-1],cal)==[175,0,0,0,0,0]
    with pytest.raises(ValueError,match='невалидные'):validate_policy_target([float('nan'),0,0,0,0,50],cal)

def test_loaded_policy_keeps_temporal_action_queue_until_explicit_reset(monkeypatch,tmp_path):
    import torch
    from so101_lab.inference import LoadedPolicy
    from so101_lab.features import FeatureContract
    class Policy:
        name='smolvla'
        def __init__(self):self.resets=0;self.calls=0
        def reset(self):self.resets+=1
        def select_action(self,prepared,noise=None):self.calls+=1;return torch.zeros((1,6))
    policy=Policy()
    loaded=LoadedPolicy(policy,lambda value:value,lambda value:value,FeatureContract(mode='rgb_state'),tmp_path,False,'cpu')
    monkeypatch.setattr(loaded,'prepare_observation',lambda *args,**kwargs:{})
    loaded.predict({}, {}, task='task')
    loaded.predict({}, {}, task='task')
    assert policy.calls==2 and policy.resets==0
    loaded.predict({}, {}, task='task',reset_queue=True)
    assert policy.resets==1

def test_recording_profile_uses_actual_training_rate_and_typical_episode(tmp_path):
    import json
    from so101_lab.inference import _recording_profile
    for episode,frames in [('one',262),('two',647)]:
        path=tmp_path/'episodes'/episode;path.mkdir(parents=True)
        (path/'episode.json').write_text(json.dumps({'fps_requested':30,'frames':frames}))
    assert _recording_profile({'dataset_path':str(tmp_path),'split':{'train':['one','two']}})==(30.,454)

def test_physical_command_uses_the_retained_lelab_smolvla_rtc_preset(tmp_path):
    from so101_lab.inference import official_lerobot_command
    command=official_lerobot_command(tmp_path,'put blue cube in the box',30.,20.,'cuda')
    joined=' '.join(command)
    assert '-m lerobot.scripts.lerobot_rollout' in joined
    assert '--inference.type=rtc' in command
    assert '--inference.rtc.execution_horizon=10' in command
    assert '--inference.rtc.max_guidance_weight=10.0' in command
    assert '--return_to_initial_position=true' in command
    assert '--fps=30' in command
    camera_arg=next(item for item in command if item.startswith('--robot.cameras='))
    assert '"fps":30' in camera_arg and '"fps":30.0' not in camera_arg
    assert not any('max_relative_target' in item for item in command)

def test_physical_act_command_uses_official_sync_with_saved_letterbox_plugin(tmp_path):
    from so101_lab.inference import official_lerobot_command
    command=official_lerobot_command(tmp_path,'put blue cube in the box',30.,20.,'cuda',policy_type='act')
    assert '--inference.type=sync' in command
    assert '--discover_packages_path=so101_lab.lerobot_plugin' in command
    assert not any('inference.rtc' in item for item in command)
    assert not any('max_relative_target' in item for item in command)

def test_inference_dispatch_wires_start_stop_and_learned_action_without_io(monkeypatch,tmp_path):
    import time,numpy as np
    from types import SimpleNamespace
    from so101_lab import inference,hardware
    calls=[]
    class Source:
        def __init__(self,settings,teleoperate):
            assert teleoperate is False
            self.calibration={'values':{'follower':{n:{'range_min':0,'range_max':4095} for n in hardware.JOINTS}}}
        def start(self,*,explicit_user_start=False,stop=None):assert explicit_user_start;calls.append('start')
        def observe(self):
            t=time.monotonic_ns();return {'timestamp_ns':t,'state_timestamp_ns':t,'observation.state':[0,0,0,0,0,50],'images':{c:{'image':np.zeros((16,16,3),np.uint8),'timestamp_ns':t} for c in ('top','wrist')}}
        def send(self,action,fresh):calls.append(tuple(action));return {'action':action}
        def close(self):calls.append('close');return []
    monkeypatch.setattr(hardware,'HardwareSource',Source)
    learned=[0,0,0,0,0,50]
    loaded=SimpleNamespace(contract=SimpleNamespace(mode='rgb_state',cameras=('top','wrist')),policy=SimpleNamespace(name='act'),checkpoint=tmp_path,device='cpu',predict=lambda *a:learned)
    result=inference._run_policy_loaded(loaded,{'explicit_user_start':True,'max_steps':1},None,lambda:False)
    assert result['steps']==1 and calls==['start',tuple(learned),'close']
    calls.clear();result=inference._run_policy_loaded(loaded,{'explicit_user_start':True},None,lambda:True);assert result['steps']==0 and not calls

def test_inference_discards_changed_state_and_reobserves_before_send(monkeypatch,tmp_path):
    import time,numpy as np
    from types import SimpleNamespace
    from so101_lab import inference,hardware
    calls=[]
    class Source:
        def __init__(self,settings,teleoperate):
            self.calibration={'values':{'follower':{n:{'range_min':0,'range_max':4095} for n in hardware.JOINTS}}};self.reads=0
        def start(self,**kwargs):calls.append('start')
        def observe(self):
            self.reads+=1;t=time.monotonic_ns();state=[0,0,0,0,0,50] if self.reads==1 else [1,0,0,0,0,50]
            return {'timestamp_ns':t,'state_timestamp_ns':t,'observation.state':state,'images':{c:{'image':np.zeros((16,16,3),np.uint8),'timestamp_ns':t} for c in ('top','wrist')}}
        def send(self,action,fresh):calls.append(('send',list(action),fresh['observation.state']));return {'action':action}
        def close(self):calls.append('close');return []
    monkeypatch.setattr(hardware,'HardwareSource',Source)
    progress=[];loaded=SimpleNamespace(contract=SimpleNamespace(mode='rgb_state',cameras=('top','wrist')),policy=SimpleNamespace(name='act'),checkpoint=tmp_path,device='cpu',predict=lambda *a:[0,0,0,0,0,50])
    result=inference._run_policy_loaded(loaded,{'explicit_user_start':True,'max_steps':1},progress.append,lambda:False)
    assert result['steps']==1
    assert [p['state'] for p in progress]==['reobserving','inference']
    assert len([c for c in calls if isinstance(c,tuple) and c[0]=='send'])==1
