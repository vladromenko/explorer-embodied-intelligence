"""Synthetic native files only. Real official dataset I/O, no optimizer or robot."""
import copy
import hashlib
import json
from pathlib import Path
import sys

import numpy as np
from PIL import Image
import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'shared'),str(ROOT/'01-autonomous-learning/src')]
from explorer_shared.so101_station import NAMES,UNITS,LOW,HIGH
from explorer_sim.so101_dataset_qualification import inspect_station_episode,replay_schedule,qualify_station_dataset,ACTION_CONTRACT_ID


def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def fixture(folder,ident,split='train',color=17,hold=False):
    folder.mkdir(parents=True);rows=[]
    for index in range(3):
        image=folder/(str(index)+'.png');Image.fromarray(np.full((16,24,3),color+index,np.uint8)).save(image)
        stamp=1000000000+index*100000000
        state=[float(0 if hold else index)]*6
        rows.append({'frame':{'file':image.name,'sha256':sha(image),'frame_id':ident+'-'+str(index),'monotonic_ns':stamp,'clock_domain':'pi-boot-'+ident},
            'state':state,'requested_action':[float(index+8)]*6,'action':[float(index+4)]*6,
            'state_monotonic_ns':stamp+1000000,'action_monotonic_ns':stamp+2000000,'clock_domain':'pi-boot-'+ident})
    source={'schema_version':1,'episode_id':ident,'source_type':'station_demonstration','source_kind':'software_fixture',
        'project_id':'personal','state':'complete','split':split,'outcome':'success','label_source':'software_fixture_not_physical_operator',
        'state_basis':'measured_state','action_basis':'dispatched_command','action_names':list(NAMES),'action_units':UNITS,
        'normalization':{'low':LOW,'high':HIGH},'fps':10,'clock_domain':'pi-boot-'+ident,'samples':rows,
        'calibration_metadata':{role:{'calibration_sha256':hashlib.sha256(role.encode()).hexdigest(),'verified_on_connected_hardware':False} for role in ('leader','follower')}}
    path=folder/'manifest.json';path.write_text(json.dumps(source));return path,source


def binding(path,source,*,version=1):
    return {'manifest_path':str(path),'manifest_sha256':sha(path),'accepted':True,'skill_id':'software-skill','skill_version':version,'split':source['split']}


def test_legacy_hold_keeps_measured_follower_and_missing_leader_acceptance_receipts_explicit(tmp_path):
    path,source=fixture(tmp_path/'old','legacy-hold',hold=True)
    original=path.read_bytes();report=inspect_station_episode(path,task_type='object_manipulation')
    assert report['technical_dataset_ready'] and report['legacy_hold_only']
    assert report['recorded_channels']['operator_requested']['complete']
    assert report['recorded_channels']['follower_measured']['complete']
    assert not report['recorded_channels']['leader_measured']['recorded']
    assert not report['recorded_channels']['follower_accepted']['recorded']
    assert not report['full_teaching_provenance_ready'] and not report['object_expert_eligible']
    assert 'camera_calibration_not_recorded' in report['missing_facts'] and 'explicit_source_boot_identity_not_recorded' in report['missing_facts']
    assert not report['replay_vs_rollout']['physical_io'] and path.read_bytes()==original


def test_recorded_replay_maps_official_SO101_keys_without_policy_or_robot_calls(tmp_path):
    path,source=fixture(tmp_path/'source','replay-source')
    plan=replay_schedule(path)
    assert plan['action_contract_id']==ACTION_CONTRACT_ID
    assert set(plan['actions'][0]['action'])=={name+'.pos' for name in NAMES}
    assert list(plan['actions'][0]['action'].values())==source['samples'][0]['action']
    assert list(plan['actions'][0]['action'].values())!=source['samples'][0]['requested_action']
    assert not plan['learned_policy_used'] and not plan['physical_io'] and not plan['robot_connect_called']
    compatibility=plan['official_api_compatibility']
    assert compatibility['required_normalized_software_mapping']=={'use_degrees':False}
    assert not compatibility['official_default_robot_coordinate_compatible'] and not compatibility['official_robot_connect_qualified']
    assert not compatibility['hardware_entrypoint_executed'] and len(compatibility['installed_primary_sources_sha256'])==4


def test_causal_clock_and_source_hash_fail_before_replay_planning(tmp_path):
    path,source=fixture(tmp_path/'clock','invalid-clock');source['samples'][0]['state_monotonic_ns']=0;path.write_text(json.dumps(source))
    with pytest.raises(ValueError,match='causality'):replay_schedule(path)
    path,source=fixture(tmp_path/'hash','invalid-hash');(path.parent/source['samples'][0]['frame']['file']).write_bytes(b'changed')
    with pytest.raises(ValueError,match='RGB SHA'):inspect_station_episode(path)


def four_sources(tmp_path):
    values=[]
    for ident,split,color in [('train-a','train',17),('train-b','train',37),('dev','development',57),('sealed','sealed_test',77)]:
        path,source=fixture(tmp_path/ident,ident,split,color);values.append((path,source,binding(path,source)))
    return values


def test_skill_version_duplicate_frame_session_calibration_and_sealed_reassignment_rejected(tmp_path):
    values=four_sources(tmp_path);bindings=[v[2] for v in values];target=tmp_path/'aggregate'
    bad=copy.deepcopy(bindings);bad[0]['skill_version']=2
    with pytest.raises(ValueError,match='different skill/version'):qualify_station_dataset(bad,target,skill_id='software-skill',skill_version=1)
    bad=copy.deepcopy(bindings);bad[-1]['split']='train'
    with pytest.raises(ValueError,match='split cannot be reassigned'):qualify_station_dataset(bad,target,skill_id='software-skill',skill_version=1)
    # Different IDs still cannot move exactly the same original RGB bytes across splits.
    dev,source,_=values[2];train=values[0][0]
    (dev.parent/'0.png').write_bytes((train.parent/'0.png').read_bytes());source['samples'][0]['frame']['sha256']=sha(dev.parent/'0.png');dev.write_text(json.dumps(source));bindings[2]=binding(dev,source)
    with pytest.raises(ValueError,match='RGB content crosses'):qualify_station_dataset(bindings,target,skill_id='software-skill',skill_version=1)
    assert not target.exists()


def test_object_task_never_accepts_legacy_hold_as_expert(tmp_path):
    values=four_sources(tmp_path)
    with pytest.raises(ValueError,match='not object experts'):qualify_station_dataset([v[2] for v in values],tmp_path/'forbidden',skill_id='software-skill',skill_version=1,task_type='object_manipulation')


def test_real_official_061_writer_finalize_and_reader_preserve_all_accepted_train_dev_originals(tmp_path):
    values=four_sources(tmp_path);originals={str(path):path.read_bytes() for path in tmp_path.rglob('*') if path.is_file()}
    receipt=qualify_station_dataset([v[2] for v in values],tmp_path/'aggregate',skill_id='software-skill',skill_version=1)
    assert receipt['official_dataset_readback_verified'] and receipt['originals_unchanged']
    assert receipt['accepted_train_original_episode_ids']==['train-a','train-b']
    assert receipt['accepted_development_original_episode_ids']==['dev'] and receipt['sealed_original_episode_ids']==['sealed']
    assert receipt['official_dataset']['frames']==9 and receipt['official_dataset']['episodes']==3
    assert not receipt['physical_io'] and not receipt['replay_executed'] and not receipt['learned_rollout_executed']
    assert not receipt['object_expert_eligible'] and not receipt['full_teaching_provenance_ready'] and not receipt['uploaded_to_hub']
    manifest=json.loads(Path(receipt['aggregate_manifest_path']).read_text())
    assert manifest['sealed_sources_excluded'][0]['payload_excluded']
    assert {item['source_episode_id'] for item in manifest['episodes']}=={'train-a','train-b','dev'}
    for record in manifest['source_records']:
        saved=tmp_path/'aggregate'/record['saved_original_manifest']
        assert saved.read_bytes()==originals[next(name for name in originals if Path(name).parent.name==record['original_episode_id'] and name.endswith('manifest.json'))]
    assert all(Path(name).read_bytes()==raw for name,raw in originals.items())
    with pytest.raises(ValueError,match='already exists'):qualify_station_dataset([v[2] for v in values],tmp_path/'aggregate',skill_id='software-skill',skill_version=1)


@pytest.mark.parametrize('fault',['session','scene','calibration','duplicate','changed_source','bound_skill'])
def test_original_source_group_identity_and_immutable_binding_guards(tmp_path,fault):
    values=four_sources(tmp_path);bindings=[v[2] for v in values]
    if fault=='duplicate':bindings[1]=copy.deepcopy(bindings[0])
    elif fault in ('session','scene'):
        for selected in (0,2):
            path,source,_=values[selected];source['session_id' if fault=='session' else 'scene_id']='same-original-group';path.write_text(json.dumps(source));bindings[selected]=binding(path,source)
    elif fault=='calibration':
        path,source,_=values[2];source['calibration_metadata']['follower']['calibration_sha256']='f'*64;path.write_text(json.dumps(source));bindings[2]=binding(path,source)
    elif fault=='bound_skill':
        path,source,_=values[0];source['source_context']={'skill_id':'another-recorded-skill','skill_version':1};path.write_text(json.dumps(source));bindings[0]=binding(path,source)
    else:
        path,source,_=values[0];source['name']='changed after immutable assignment';path.write_text(json.dumps(source))
    messages={'session':'session crosses','scene':'scene crosses','bound_skill':'Recorded source skill/version differs','calibration':'calibration identities differ','duplicate':'Duplicate original','changed_source':'source SHA changed'}
    with pytest.raises(ValueError,match=messages[fault]):qualify_station_dataset(bindings,tmp_path/'rejected',skill_id='software-skill',skill_version=1)
    assert not (tmp_path/'rejected').exists()


def test_source_context_is_resolved_without_inventing_legacy_identity_and_conflicts_rejected(tmp_path):
    path,source=fixture(tmp_path/'context','context-source')
    assert inspect_station_episode(path)['source_skill_id'] is None
    source['source_context']={'skill_id':'software-skill','skill_version':2,'session_id':'declared-session','scene_id':'scene','split':'train','phase_id':'reach'}
    path.write_text(json.dumps(source));report=inspect_station_episode(path)
    assert report['source_skill_id']=='software-skill' and report['source_skill_version']==2 and report['source_session_id']=='declared-session'
    source['session_id']='contradictory-session';path.write_text(json.dumps(source))
    report=inspect_station_episode(path)
    assert not report['technical_dataset_ready'] and 'source_context_conflict:session_id' in report['reasons']
