import hashlib
import json
from pathlib import Path

import pytest
from explorer.store import Store
from explorer.station_runtime import validate_source
from explorer_shared.so101_station import NAMES,UNITS,LOW,HIGH


def fixture(tmp_path):
    store=Store(tmp_path/'state')
    profiles={role:{'calibration_sha256':role+'-original-calibration'} for role in ('leader','follower')}
    source=tmp_path/'manifest.json'
    source.write_text(json.dumps({'source_calibration_metadata':{'calibration_metadata':profiles}}))
    training=store.create_job('train_act','personal',{})
    checkpoint=store.root/'jobs'/training['id']/'student_act'
    checkpoint.mkdir(parents=True)
    (checkpoint/'explorer_preprocessing.json').write_text(json.dumps({'state_low':LOW,'state_high':HIGH}))
    (checkpoint/'model.safetensors').write_bytes(b'checksum-only unit fixture; never executed as a model')
    store.update_job(training['id'],status='completed',result={'trained':True,'source_manifest_path':str(source),
        'source_manifest_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),
        'checkpoint_sha256':{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in checkpoint.iterdir()}})
    card=dict(id='station-policy',task='contract test',source_type='test',status='trained_offline_only',
        action_contract={'names':list(NAMES),'units':UNITS,'state_basis':'measured_state'},
        lineage={'job_id':training['id']},evaluation={},limitations=['No physical evidence'])
    store.add_skill(card)
    return store,{'roles':profiles},checkpoint


def test_exact_recorded_calibration_contract_is_required_before_policy_load(tmp_path):
    store,status,checkpoint=fixture(tmp_path)
    assert validate_source(store,'station-policy',status)[1]==checkpoint
    status['roles']['follower']['calibration_sha256']='another-arm'
    with pytest.raises(ValueError,match='calibration'):
        validate_source(store,'station-policy',status)


def test_changed_checkpoint_is_rejected_before_any_inference_or_hardware_call(tmp_path):
    store,status,checkpoint=fixture(tmp_path)
    (checkpoint/'model.safetensors').write_bytes(b'changed')
    with pytest.raises(ValueError,match='checksum'):
        validate_source(store,'station-policy',status)


def test_nine_axis_robot_skill_is_not_a_six_axis_station_policy(tmp_path):
    store,status,_=fixture(tmp_path)
    card=store.skills()[0]
    card['id']='nine-axis'
    card['action_contract']['names']=list(NAMES)+['vx','vy','wz']
    store.add_skill(card)
    with pytest.raises(ValueError,match='six-axis'):
        validate_source(store,'nine-axis',status)
