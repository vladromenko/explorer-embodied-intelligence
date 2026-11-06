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

def test_policy_outside_absolute_envelope_rejected_not_clipped():
    from so101_lab.inference import validate_policy_target
    from so101_lab.hardware import JOINTS
    cal={'values':{'follower':{name:{'range_min':0,'range_max':4095} for name in JOINTS}}}
    assert validate_policy_target([0,0,0,0,0,50],cal)
    with pytest.raises(ValueError,match='отклонена'):validate_policy_target([10000,0,0,0,0,50],cal)
    with pytest.raises(ValueError,match='отклонена'):validate_policy_target([0,0,0,0,0,-1],cal)

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
    loaded=SimpleNamespace(contract=SimpleNamespace(mode='rgb_state'),policy=SimpleNamespace(name='act'),checkpoint=tmp_path,device='cpu',predict=lambda *a:learned)
    result=inference._run_policy_loaded(loaded,{'explicit_user_start':True,'max_steps':1},None,lambda:False)
    assert result['steps']==1 and calls==['start',tuple(learned),'close']
    calls.clear();result=inference._run_policy_loaded(loaded,{'explicit_user_start':True},None,lambda:True);assert result['steps']==0 and not calls
