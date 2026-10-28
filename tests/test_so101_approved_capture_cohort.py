"""Approved pause-aware source04 cohort, generated inputs and labels ONLY.

No robot/network/optimizer. approved_cohort(tmp_path) is reusable by the existing
queued ACT fixture: return (store, chosen, sources), where each source is the
legacy tuple (completed_import_job, raw_manifest_Path, raw_dict, binding, review).
Raw8 -> selected7 per original; synthetic success/phase claims are not physical.
"""
from collections import Counter
from copy import deepcopy
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
from types import SimpleNamespace

import numpy as np
from PIL import Image
import pytest

from explorer.store import Store
from explorer import lab_skill_workflow as flow
from explorer import source_demo_workflow as source_reviews
from explorer_so101.capture_adapter import StationCaptureAdapter
from explorer_so101.capture_archive import import_source_capture
from test_so101_capture_adapter import FakeSource,Clock

PIN_KEYS={'capture_manifest_sha256','source_review_sha256','source_archive_sha256','import_provenance_sha256'}
SELECTED=[0,1,2,4,5,6,7]


def _sha(path):
    with Path(path).open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()


def _sample(source,ident,variant_index,step,stamp):
    """Feed an already measured fake cycle into SAME original _sample/writer."""
    station=source.station
    state=[float(step+joint)+variant_index*.25 for joint in range(5)]+[float(10+step)+variant_index*.25]
    action=[value+5 for value in state];requested=[value+.5 for value in action]
    leader=[value+1 for value in state]
    station.measurements={'leader':leader,'follower':state,'monotonic_ns':stamp,'clock_domain':station.clock_domain}
    station.last_command={'requested':requested,'dispatched':action,'monotonic_ns':stamp+10,
        'clock_domain':station.clock_domain,'control_epoch':station.control_epoch,'sequence':step}
    pixels=np.zeros((24,32,3),np.uint8)
    pixels[:]=[15+variant_index*40+step*2,30+variant_index*30,45+step*3]
    memory=io.BytesIO();Image.fromarray(pixels).save(memory,format='JPEG')
    frame=SimpleNamespace(jpeg=memory.getvalue(),frame_id=ident+':generated-rgb:'+str(step),monotonic_ns=stamp-5,
        width=32,height=24,timestamp_basis='SOFTWARE_FIXTURE_HOST_RECEIVE_NOT_CAMERA_EXPOSURE')
    station._sample(frame,state,requested,action,stamp,stamp+10)
    station.record_queue.join()


def _complete_import(store,artifact,recording_id,wrapper):
    checksum=_sha(artifact)
    job=source_reviews.queue_source_review(store,recording_id,checksum,wrapper)
    assert job['kind']=='station_import' and job['project_id']=='04'
    assert job['config']['source_format']=='so101_source_capture_v1'
    assert job['config']['source_review_sha256']==wrapper['review_sha256']
    result=import_source_capture(artifact,checksum,store.root/'episodes/station-approved'/wrapper['review_sha256'],
        recording_id,wrapper)
    # Exact approved_source_capture_import result aliases, without pretending a
    # network worker/per-import converter ran in this local factory.
    result.update(source_approval=deepcopy(wrapper),source_approval_path=result['source_review_path'],
        source_approval_file_sha256=result['source_review_bytes_sha256'],
        result_scope='SOFTWARE_FIXTURE_LOCAL_CONSUMER_NO_NETWORK_OR_WORKER_DISPATCH')
    store.complete_job(job['id'],result)
    return store.job(job['id'])


def _video_review(store,chosen,raw,binding,*,previous=None):
    phases=chosen['spec']['phase_ids'];assert len(phases)<=8
    body={'original_episode_id':raw['episode_id'],'binding_sha256':binding['receipt_sha256'],
        'outcome':'success','reviewed_by':'SOFTWARE_FIXTURE_SYNTHETIC_REVIEW_NOT_PHYSICAL',
        'observed_recording':True,'object_and_goal_visible':True,'full_task_confirmed':True,
        'phase_outcomes':{phase:'success' for phase in phases},
        'phase_intervals':{phase:[index,index if index<len(phases)-1 else 7] for index,phase in enumerate(phases)}}
    if previous is not None:body['expected_review_sha256']=previous['receipt_sha256']
    return flow.review_episode(store,chosen['spec']['skill_id'],1,body)


def approved_cohort(tmp_path,*,store=None,chosen=None,session_for_variant=None,prepare_context=None):
    """One confirmed SkillSpec/five variants; all inputs visibly software-only.

    Only the original recorder file writer is started; never Station._work or
    Station.close (which calls local STOP). Every original writer is joined before
    returning, so this factory can be reused in an isolated queue proof.
    """
    tmp_path=Path(tmp_path);tmp_path.mkdir(parents=True,exist_ok=True)
    store=store if store is not None else Store(tmp_path/'state')
    if chosen is None:
        chosen=flow.propose(store,{'template_id':'pick_object_to_container'})
        chosen=flow.confirm(store,chosen['spec']['skill_id'],1,{'spec_sha256':chosen['spec_sha256']})
    variants=chosen['spec']['demo_plan']['variants']
    assert Counter(row['split'] for row in variants)=={'train':3,'development':1,'sealed_test':1}
    sources=[]
    for index,variant in enumerate(variants):
        source_root=tmp_path/('producer-'+str(index));source_root.mkdir()
        source=FakeSource(source_root);clock=Clock();clock.value=1_000_000_000+index*2_000_000_000
        source.station.control_epoch='SOFTWARE_FIXTURE_NATIVE_EPOCH_'+str(index)
        adapter=StationCaptureAdapter(tmp_path/('capture-adapter-'+str(index)),status=source.status,
            record_start=source.start,record_finish=source.finish,read_manifest=source.manifest,
            archive_original=source.archive,clock_domain=source.station.clock_domain,boot_id=source.station.boot,clock=clock)
        try:
            session=(session_for_variant(index,deepcopy(variant)) if session_for_variant else
                flow.create_session(store,chosen['spec']['skill_id'],1,{'scene_id':'GENERATED_SCENE_'+str(index),'split':variant['split']}))
            body=flow.capture_context(store,chosen['spec']['skill_id'],1,{'variant_id':variant['variant_id'],
                'session_id':session['session_id'],'object_instance_id':'GENERATED_OBJECT_'+str(index)})['record_start_body']
            if prepare_context is not None:body=prepare_context(index,deepcopy(variant),deepcopy(session),deepcopy(body))
            started=clock();ident=adapter.start(body,'SOFTWARE_FIXTURE_SOURCE_ACTOR')['recording_id']
            for step in range(8):
                if step==3:clock.value=started+350_000_000;adapter.pause(ident)
                if step==4:clock.value=started+450_000_000;adapter.resume(ident)
                stamp=started+(step+1)*100_000_000;clock.value=stamp+10
                _sample(source,ident,index,step,stamp)
            clock.value=started+850_000_000;sealed=adapter.finish(ident,'success')
            assert [row['source_sample_index'] for row in sealed['sample_selection'] if row['included_after_pause_filter']]==SELECTED
            assert len(sealed['sample_selection'])==8 and sealed['source_kind']=='software_fixture'
            outer=adapter.archive(ident);checksum=_sha(outer)
            artifact=store.root/'artifacts'/checksum[:2]/(checksum+'.tar');artifact.parent.mkdir(parents=True,exist_ok=True)
            with outer.open('rb') as src,artifact.open('xb') as dst:
                shutil.copyfileobj(src,dst,length=64*1024);dst.flush();os.fsync(dst.fileno())
            clock.value+=1_000_000;wrapper=adapter.review(ident,'approve','SOFTWARE_FIXTURE_SOURCE_ACTOR',checksum)
            job=_complete_import(store,artifact,ident,wrapper);path=Path(job['result']['source_manifest_path'])
            raw=json.loads(path.read_text());assert raw['source_kind']=='software_fixture' and len(raw['samples'])==8
            binding=flow.bind_episode(store,chosen['spec']['skill_id'],1,{'import_job_id':job['id'],'variant_id':variant['variant_id'],
                'session_id':session['session_id']})
            review=_video_review(store,chosen,raw,binding);sources.append((job,path,raw,binding,review))
        finally:
            adapter.close();source.close_writer()
    report={'schema':'explorer_approved_source04_cohort_fixture_v1','source_kind':'software_fixture','variants':5,
        'raw_per_original':8,'selected_per_original':7,'paused_raw_index':3,'physical_data_or_success':False,
        'optimizer_updates':0,'worker_or_network_dispatch_executed':False,
        'source_ids':[row[2]['episode_id'] for row in sources]}
    (tmp_path/'approved-capture-cohort-fixture.json').write_text(json.dumps(report,indent=2))
    return store,chosen,sources


def _pins(job):
    result=job['result']
    return {'capture_manifest_sha256':result['capture_manifest_sha256'],'source_review_sha256':result['source_review_sha256'],
        'source_archive_sha256':result['source_archive_sha256'],'import_provenance_sha256':result['provenance_sha256']}


def _assert_capture_pins(store,chosen,sources):
    state=flow.state_for_skill(store,chosen['spec']['skill_id'],1)
    assert state['data_ready'] and state['counts']['accepted_originals']==5
    by_id={row['binding']['original_episode_id']:row for row in state['assignments']}
    for job,path,raw,binding,review in sources:
        expected=_pins(job)
        assert set(binding['source_capture_pins'])==PIN_KEYS
        assert binding['source_capture_pins']==expected
        assignment=by_id[raw['episode_id']]
        assert assignment['binding']['source_capture_pins']==expected
        evidence=assignment['evidence']['source_capture']
        assert {key:evidence[key] for key in PIN_KEYS}==expected
        assert evidence['capture_manifest_path']==job['result']['capture_manifest_path']
    assert not state['physical_execution_authorized']
    return state


def _snapshot(paths):
    return {str(path):_sha(path) for root in paths for path in ([root] if root.is_file() else root.rglob('*')) if path.is_file()}


def _change_source_review(tmp_path,index,raw,decision,clock_value):
    def prohibited(*args,**kwargs):raise AssertionError('Review cannot call source recording/control/network')
    clock=Clock();clock.value=clock_value
    adapter=StationCaptureAdapter(Path(tmp_path)/('capture-adapter-'+str(index)),status=prohibited,record_start=prohibited,
        record_finish=prohibited,read_manifest=prohibited,archive_original=prohibited,
        clock_domain=raw['clock_domain'],boot_id=raw['boot_id'],clock=clock)
    try:
        artifact=adapter.archive(raw['episode_id'])
        return adapter.review(raw['episode_id'],decision,'SOFTWARE_FIXTURE_SOURCE_ACTOR',_sha(artifact))
    finally:adapter.close()


def test_approved_capture_four_identity_pins_reach_binding_and_current_cohort(tmp_path):
    store,chosen,sources=approved_cohort(tmp_path)
    _assert_capture_pins(store,chosen,sources)


def test_full_approved_cohort_official_dataset_late_reject_and_reapproval_preserve_originals(tmp_path):
    store,chosen,sources=approved_cohort(tmp_path)
    state=_assert_capture_pins(store,chosen,sources)  # fails before optional Dataset/Torch imports on old baseline
    initial_fingerprint=state['cohort_fingerprint'];identity=chosen['spec']['skill_id']
    aggregate=flow.build_aggregate(store,identity,1);root=Path(aggregate['aggregate_manifest']).parent
    view=json.loads((root/'native-data/manifest.json').read_text())
    qualified=json.loads((root/'native-data/qualification.json').read_text())
    expected_pins={raw['episode_id']:_pins(job) for job,path,raw,binding,review in sources}
    assert aggregate['source_capture_bindings']==expected_pins
    assert qualified['source_capture_bindings']==expected_pins
    source_sidecars=json.loads((root/'source-reviews.json').read_text())
    assert {entry['binding']['original_episode_id']:entry['binding']['source_capture_pins'] for entry in source_sidecars}==expected_pins
    assert aggregate['cohort_fingerprint']==initial_fingerprint and aggregate['source_kind']=='SIMULATION'
    assert aggregate['official_dataset_readback_verified'] and not aggregate['physical_success_inferred']
    assert len(view['episodes'])==8 and sum(row['frames'] for row in view['episodes'])==28
    by_id={raw['episode_id']:(job,path,raw,binding,review) for job,path,raw,binding,review in sources}
    retained={};episode_indices={}
    for episode in view['episodes']:
        assert episode['split']!='sealed_test'
        ident=episode['source_episode_id'];raw=by_id[ident][2]
        with np.load(root/'native-data'/episode['file'],allow_pickle=False) as data:
            indices=data['source_sample_index'].tolist();episode_indices.setdefault(ident,[]).append(indices)
            assert 3 not in indices
            np.testing.assert_array_equal(data['state_native'],np.asarray([raw['samples'][index]['state'] for index in indices],np.float32))
            np.testing.assert_array_equal(data['action_native'],np.asarray([raw['samples'][index]['action'] for index in indices],np.float32))
    for record in view['source_records']:
        ident=record['original_episode_id'];saved=root/'native-data'/record['saved_original_manifest']
        assert saved.read_bytes()==by_id[ident][1].read_bytes()
        with np.load(saved.with_name('native_samples.npz'),allow_pickle=False) as raw_data:assert len(raw_data['state_native'])==8
        assert episode_indices[ident]==[[0,1,2],[4,5,6,7]]
    sealed_id=sources[-1][2]['episode_id'];assert sealed_id not in episode_indices
    sealed_jpeg_hashes={row['frame']['sha256'] for row in sources[-1][2]['samples']}
    assert not sealed_jpeg_hashes.intersection(_snapshot([root]).values())
    from lerobot.datasets.lerobot_dataset import LeRobotDataset
    from explorer_sim.dataset_export import IMAGE_KEY
    official=root/'native-data/lerobot_v3';provenance=json.loads((official/'explorer_provenance.json').read_text())
    dataset=LeRobotDataset(provenance['repo_id'],root=official,download_videos=False,video_backend='pyav')
    assert len(dataset)==28 and dataset.meta.total_episodes==8
    counts=Counter()
    for index in range(len(dataset)):
        row=dataset[index];episode=provenance['episodes'][int(row['episode_index'])];ident=episode['source_episode_id']
        source_index=int(row['provenance.source_sample_index']);assert source_index!=3 and ident!=sealed_id
        raw=by_id[ident][2]['samples'][source_index];counts[ident]+=1
        np.testing.assert_array_equal(row['observation.state'].numpy(),np.asarray(raw['state'],np.float32))
        np.testing.assert_array_equal(row['action'].numpy(),np.asarray(raw['action'],np.float32))
        rgb=(row[IMAGE_KEY].permute(1,2,0).numpy()*255).round().astype(np.uint8)
        with Image.open(by_id[ident][1].parent/raw['frame']['file']) as image:np.testing.assert_array_equal(rgb,np.asarray(image.convert('RGB')))
    assert sorted(counts.values())==[7,7,7,7]
    assert flow.build_aggregate(store,identity,1)==aggregate
    old_roots=[root]+[Path(job['result']['provenance_path']).parent for job,path,raw,binding,review in sources]
    old_roots += [Path(job['result']['source_archive_path']) for job,path,raw,binding,review in sources]
    retained=_snapshot(old_roots)
    old_job,old_path,raw,old_binding,old_review=sources[0]
    rejected=_change_source_review(tmp_path,0,raw,'reject',20_000_000_000)
    source_reviews.observe_source_review(store,raw['episode_id'],old_job['config']['archive_sha256'],rejected)
    assert not flow.state_for_skill(store,identity,1)['data_ready']
    assert not flow.model_readiness(store,identity,1)['demos']['ready']
    with pytest.raises(flow.WorkflowBlocked):flow.build_aggregate(store,identity,1)
    approved=_change_source_review(tmp_path,0,raw,'approve',20_000_000_001)
    new_job=_complete_import(store,Path(old_job['result']['source_archive_path']),raw['episode_id'],approved)
    assert new_job['id']!=old_job['id'] and new_job['config']['review_revision_of_job_id']==old_job['id']
    new_path=Path(new_job['result']['source_manifest_path']);assert new_path!=old_path and new_path.read_bytes()==old_path.read_bytes()
    new_binding=flow.bind_episode(store,identity,1,{'import_job_id':new_job['id'],'variant_id':old_binding['variant_id'],
        'session_id':old_binding['session_id'],'expected_binding_sha256':old_binding['receipt_sha256']})
    new_review=_video_review(store,chosen,raw,new_binding,previous=old_review)
    sources[0]=(new_job,new_path,raw,new_binding,new_review)
    renewed=_assert_capture_pins(store,chosen,sources)
    assert renewed['cohort_fingerprint']!=initial_fingerprint
    new_aggregate=flow.build_aggregate(store,identity,1)
    assert new_aggregate['aggregate_manifest']!=aggregate['aggregate_manifest']
    assert new_aggregate['source_capture_bindings'][raw['episode_id']]==_pins(new_job)
    assert all(_sha(Path(path))==checksum for path,checksum in retained.items())
    report={'schema':'explorer_approved_source04_cohort_dataset_fixture_v1','source_kind':'SIMULATION',
        'originals':5,'raw_samples_each':8,'selected_train_dev_samples_each':7,'official_frames':28,'official_episodes':8,
        'sealed_original_id':sealed_id,'sealed_payload_excluded':True,'initial_fingerprint':initial_fingerprint,
        'renewed_fingerprint':renewed['cohort_fingerprint'],'source_capture_bindings':expected_pins,
        'late_reject_closed_cached_aggregate':True,'reapproval_preserved_old_artifacts':True,
        'optimizer_updates':0,'network_or_robot_commands':0,'physical_success_claimed':False}
    (tmp_path/'approved-capture-cohort-dataset.json').write_text(json.dumps(report,indent=2))
