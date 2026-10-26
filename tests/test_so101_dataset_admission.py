"""Admission fixtures use actual workflow+Dataset, never model/robot allocation."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from explorer import lab_skill_workflow as flow
from explorer.so101_dataset_admission import verify_so101_aggregate
from explorer_shared.contracts import digest
from explorer_shared.experiments import experiment_id
from explorer_shared.so101_station import NAMES,UNITS


def config_for(aggregate,spec):
    return dict(aggregate_manifest=aggregate['aggregate_manifest'],aggregate_manifest_sha256=aggregate['aggregate_manifest_sha256'],
        skill_id=spec['skill_id'],skill_version=spec['version'],spec_sha256=digest(spec))


def test_admission_module_imports_no_torch_or_robot_api():
    root=Path(__file__).parents[1];environment=dict(os.environ,PYTHONPATH=os.pathsep.join(str(root/name) for name in ('shared','00-control-center/src','01-autonomous-learning/src')))
    result=subprocess.run([sys.executable,'-c',"import sys;import explorer.so101_dataset_admission;assert 'torch' not in sys.modules;assert not any(name.startswith('lerobot.robots') for name in sys.modules)"],capture_output=True,text=True,env=environment)
    assert result.returncode==0,result.stderr


@pytest.mark.parametrize('backend',['act','diffusion'])
def test_both_consumers_reject_forged_self_admitted_manifest_before_policy_factory(tmp_path,monkeypatch,backend):
    from explorer.store import Store
    from explorer.so101_diffusion_training import run_so101_diffusion_train
    from explorer_sim.so101_skill_training import run_so101_skill_train
    from explorer_shared.learning.diffusion_backend import PROFILE_ID
    store=Store(tmp_path/'state');spec={'skill_id':'forged','version':1,'action_contract':{'id':'so101_follower_calibrated_position6_v1'}}
    document=dict(schema='explorer_so101_skill_dataset_v1',action_contract_id='so101_follower_calibrated_position6_v1',action_names=list(NAMES),action_units=UNITS,
        skill_spec=spec,skill_id='forged',skill_version=1,spec_sha256=digest(spec),source_files_sha256={},receipt=dict(full_teaching_provenance_ready=True,object_expert_eligible=True))
    path=store.root/'forged.json';path.write_text(json.dumps(document))
    config=dict(backend=backend,profile_id=PROFILE_ID,device='cpu',aggregate_manifest=str(path),aggregate_manifest_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),skill_id='forged',skill_version=1,spec_sha256=digest(spec))
    def forbidden(*args,**kwargs):raise AssertionError('No model may be allocated before aggregate admission')
    monkeypatch.setattr('explorer.so101_diffusion_training.make_compact_diffusion_policy',forbidden)
    monkeypatch.setattr('explorer_sim.so101_skill_training.make_act_policy',forbidden)
    run=run_so101_skill_train if backend=='act' else run_so101_diffusion_train
    with pytest.raises(ValueError,match='Server-bound'):run(store,dict(id='forged-job',kind='so101_skill_train',project_id=experiment_id('so101_source_learning'),config=config))


def test_real_accepted_aggregate_admission_then_event_review_and_retained_file_tamper_are_rejected(tmp_path):
    from test_lab_skill_workflow import full_cohort
    store,chosen,sources=full_cohort(tmp_path);spec=chosen['spec'];aggregate=flow.build_aggregate(store,spec['skill_id'],spec['version']);config=config_for(aggregate,spec)
    path,outer,view_path,view=verify_so101_aggregate(store,config)
    assert len(view['episodes'])==4 and len(outer['receipt']['sealed_original_episode_ids'])==1
    original_path=Path(sources[0][1]);original=original_path.read_bytes()
    sidecar=path.parent/'source-reviews.json';raw=sidecar.read_bytes();sidecar.write_bytes(raw+b' ')
    with pytest.raises(ValueError,match='changed SHA'):verify_so101_aggregate(store,config)
    sidecar.write_bytes(raw)
    with store.connect() as db:
        row=db.execute("SELECT event_id,body FROM events WHERE event_id LIKE 'lab.skill.aggregate:%'").fetchone();event=json.loads(row['body']);event['payload']['object_expert_eligible']=False
        db.execute('UPDATE events SET body=? WHERE event_id=?',(json.dumps(event),row['event_id']))
    with pytest.raises(ValueError,match='event SHA'):verify_so101_aggregate(store,config)
    with store.connect() as db:db.execute('UPDATE events SET body=? WHERE event_id=?',(row['body'],row['event_id']))
    review=sources[0][4];binding=sources[0][3]
    flow.review_episode(store,spec['skill_id'],spec['version'],dict(original_episode_id=sources[0][2]['episode_id'],binding_sha256=binding['receipt_sha256'],
        expected_review_sha256=review['receipt_sha256'],outcome='failure',reviewed_by='software-fixture-revised-review'))
    with pytest.raises(ValueError,match='admission changed'):verify_so101_aggregate(store,config)
    assert original_path.read_bytes()==original


def test_distinct_original_JPEG_bytes_with_identical_decoded_RGB_cannot_leak_across_splits(tmp_path):
    from explorer.store import Store
    from test_lab_skill_workflow import proposal,assigned
    store=Store(tmp_path/'state');chosen=proposal(store);spec=chosen['spec']
    for index,variant in enumerate(spec['demo_plan']['variants']):
        assigned(store,chosen,variant,index,first_image_offset=0 if index==3 else None,jpeg_suffix=b'development-only-metadata' if index==3 else None)
    # Encoded SHA differs, so the historical producer/export accepts this fixture.
    aggregate=flow.build_aggregate(store,spec['skill_id'],spec['version'])
    with pytest.raises(ValueError,match='Decoded original RGB content crosses'):verify_so101_aggregate(store,config_for(aggregate,spec))
