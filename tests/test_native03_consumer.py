"""Native03 data plane and common downstream: fake Source only, no optimizer."""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from explorer import job_runner,skill_workflow as flow
from explorer.mobile_dataset_evidence import inspect_mobile_import
from explorer.native_capture_approval import source_bytes
from explorer.native_capture_cohort import validate_current_approvals
from explorer_shared.contracts import ACTION_NAMES,canonical,digest
from explorer_shared.embodied_episode import ACTION_CONTRACT_ID
from explorer_shared.learning.rosmaster_skill_pipeline import CanonicalCorpus
from native03_consumer_helpers import native03_aggregate
from test_native_capture import setup,publication,sealed,outcomes
from test_native_capture_approval import approved
from explorer.store import Store


def test_direct_Nano_native03_approved_corpus_preserves_leader_raw_binding_full9_and_pause_segments(tmp_path,monkeypatch):
    path,store,evidence=native03_aggregate(tmp_path,monkeypatch);corpus=CanonicalCorpus(path)
    assert len(corpus.train)==3 and len(corpus.development)==1 and len(corpus.sealed_ids)==1
    assert {row['descriptor']['source_project_id'] for row in corpus.episodes}=={'03'}
    assert corpus.manifest['action_names']==list(ACTION_NAMES) and corpus.manifest['simultaneous_9d']
    for job in evidence['imports']:
        report=inspect_mobile_import(job,managed_root=store.root,include_manifest=True)
        assert report['admitted_for_training'] and report['source_approval']['verified']
        assert report['source_project_id']=='03' and report['input_mode']=='so101_leader'
        assert report['demonstration_input_provenance']['verified'] and report['source_approval']['required']
        assert '/native-retarget/' in job['result']['embodied_manifest']
        document=json.loads(Path(job['result']['embodied_manifest']).read_bytes());envelope=document['embodied_episode']
        bindings=[sample['source_intent_binding']['source']['source_evidence'] for sample in envelope['samples']]
        assert all(value['clock_domain']=='pi:SOFTWARE-Pi-boot' and value['calibration_sha256']=='b'*64 and len(value['positions'])==6 for value in bindings)
        assert all(sample['action']['stamp']['clock_domain']=='nano' for sample in envelope['samples'])
        assert all(sample['channels']['joint_feedback']['available'] is False for sample in envelope['samples'])
    assert all([f['recording_segment_id'] for f in row['frames']]==[0]*5+[1]*5 for row in corpus.episodes)
    assert all(method=='GET' and host=='software-nano.invalid' for method,host,_ in evidence['requests'])
    from explorer_shared.learning.rosmaster_skill_segments import chunk_slots
    assert chunk_slots(corpus.train[0]['frames'],4,3)==([4,4,4],[False,True,True])
    for name,expected in evidence['originals_sha256'].items():assert hashlib.sha256(Path(name).read_bytes()).hexdigest()==expected
    assert evidence['all_inputs_are_software_fixtures'] and evidence['real_demonstrations']==0 and evidence['optimizer_updates']==0
    training=flow.enqueue_training(store,corpus.skill_id,1,{'act_steps':4,'batch_size':1,'image_size':32,'chunk_size':2},project_id='03')
    assert training['kind']=='rosmaster_skill_train' and training['project_id']=='03'
    assert training['config']['skill_workflow']['aggregate_sha256']==corpus.sha256
    assert not (store.root/'models').exists()  # Queue metadata only, no optimizer/model.
    forged=deepcopy(evidence['imports'][0]);forged.update(project_id='02',kind='mobile_import')
    assert 'native_source_project_or_kind_differs_from_import' in inspect_mobile_import(forged,managed_root=store.root)['reasons']
    forged['project_id']='04'
    unknown=inspect_mobile_import(forged,managed_root=store.root)
    assert unknown['source_approval']['required'] and not unknown['admitted_for_training']


def test_native03_current_reject_or_new_review_pin_blocks_delayed_common_worker(tmp_path,monkeypatch):
    path,store,evidence=native03_aggregate(tmp_path,monkeypatch);manifest=json.loads(path.read_bytes());job=evidence['imports'][0]
    descriptor=next(row for row in manifest['episodes'] if row['source_episode_id']==job['config']['episode_id'])
    source=json.loads(Path(job['result']['source_approval_path']).read_bytes());selected=evidence['selected'];session=evidence['sessions']['train']
    capture=flow.begin_capture(store,selected['spec']['skill_id'],1,descriptor['variant_id'],session['session_id'],stationary_chassis_confirmed=True,project_id='03')
    flow.capture_started(store,capture['capture_id'],job['config']['episode_id'])
    flow.capture_finished(store,capture['capture_id'],job['config']['episode_id'],{'outcome':'success','completed_stages':list(flow.MANIPULATION_STAGES),'full_task_confirmed':True,'object_and_goal_visible':True})
    flow.capture_reviewed(store,capture['capture_id'],job['config']['episode_id'],source,authenticated_actor='SOFTWARE-USER')
    assert validate_current_approvals(store,manifest)
    reject=deepcopy(source);reject['source_review'].update(decision='reject',review_id='d'*32,previous_review_sha256=source['review_sha256'])
    reject['review_sha256']=hashlib.sha256(source_bytes(reject['source_review'])).hexdigest();reject['transfer_ready']=False
    flow.capture_reviewed(store,capture['capture_id'],job['config']['episode_id'],reject,authenticated_actor='SOFTWARE-USER')
    with pytest.raises(ValueError,match='Exact Source review'):validate_current_approvals(store,manifest)
    current=flow.workflow(store,selected['spec']['skill_id'],1,project_id='03')
    assert not current['train_ready'] and any('source_approval_rejected_after_import' in row['reasons'] for row in current['assignments'])
    assert store.job(job['id'])['result']==job['result']


@pytest.mark.skipif(os.environ.get('EXPLORER_RUN_NATIVE03_DATASET_FIXTURE')!='1',reason='Explicit bounded dataset-only resource window; no model/optimizer')
def test_native03_common_official_LeRobotDataset_target9_roundtrip_no_optimizer(tmp_path,monkeypatch):
    from explorer_shared.learning.rosmaster_lerobot_dataset import export_official_dataset,OfficialFusedChunks
    from explorer_shared.learning.rosmaster_skill_pipeline import FusedChunks
    import torch
    torch.set_num_threads(1)
    path,store,evidence=native03_aggregate(tmp_path,monkeypatch);corpus=CanonicalCorpus(path)
    folder,index=export_official_dataset(corpus,tmp_path/'official',image_size=32,partition='train')
    actual=OfficialFusedChunks(corpus,corpus.train,3,32,folder,index);expected=FusedChunks(corpus,corpus.train,3,32)
    for sample in range(len(actual)):
        for key in actual[sample]:assert torch.equal(actual[sample][key],expected[sample][key]),key
    assert actual[4]['action_is_pad'].tolist()==[False,True,True]
    assert index['action_dimension']==9 and index['state_dimension']==106
    assert index['original_demo_count']==3 and index['segment_episode_count']==6 and not index['sealed_payloads_exported']
    actual.recheck();corpus.recheck()
    assert evidence['real_demonstrations']==0 and evidence['optimizer_updates']==0


def test_saved_native03_model_metadata_and_DEV_job_preserve_project_no_optimizer_or_dispatch(tmp_path,monkeypatch):
    from explorer.rosmaster_learning import model_versions,enqueue_evaluation,enqueue_shadow
    from explorer.rosmaster_eval_job import run_rosmaster_evaluation
    path,store,evidence=native03_aggregate(tmp_path,monkeypatch);corpus=CanonicalCorpus(path)
    folder=store.root/'models'/'SOFTWARE-METADATA-NOT-TRAINED';checkpoint=folder/'checkpoint';checkpoint.mkdir(parents=True)
    (checkpoint/'DUMMY-NOT-ACT-WEIGHTS').write_bytes(b'SOFTWARE_METADATA_FIXTURE_NO_TRAINING')
    weights={'DUMMY-NOT-ACT-WEIGHTS':hashlib.sha256((checkpoint/'DUMMY-NOT-ACT-WEIGHTS').read_bytes()).hexdigest()}
    lineage={'skill_id':corpus.skill_id,'skill_version':1,'spec_sha256':corpus.manifest['skill_spec_sha256'],'aggregate_sha256':corpus.sha256}
    model={'schema':'explorer_rosmaster_skill_model_v1','action_contract_id':ACTION_CONTRACT_ID,'action_names':list(ACTION_NAMES),
        'skill_id':corpus.skill_id,'skill_version':1,'skill_spec_sha256':lineage['spec_sha256'],'ACT9':{'state_dimension':106,'output_dimension':9},
        'weights_sha256':weights,'aggregate_sha256':corpus.sha256,'source_kind':['physical'],
        'fixture_notice':'SOFTWARE METADATA ONLY: no trained model or physical evidence',
        'preprocessing':{'image_size':32,'normalizer':corpus.normalizer.document()},
        'train_original_episode_ids':[row['descriptor']['source_episode_id'] for row in corpus.train],
        'development_original_episode_ids':[row['descriptor']['source_episode_id'] for row in corpus.development]}
    manifest=folder/'model_manifest.json';manifest.write_bytes(canonical(model))
    trained=store.create_job('rosmaster_skill_train','03',{'skill_workflow':lineage})
    store.complete_job(trained['id'],{'trained':True,'weights_changed':True,'save_reload_passed':True,'optimizer_steps':4,'device':'cpu',
        'feature_dimension':106,'output_action_dimension':9,'model_manifest':str(manifest),'model_manifest_sha256':hashlib.sha256(manifest.read_bytes()).hexdigest(),
        'checkpoint_dir':str(checkpoint),'checkpoint_sha256':weights,'source_manifest_path':str(path),'source_manifest_sha256':corpus.sha256,
        'fixture_notice':'Entire result is metadata fixture, not actual compute'})
    versions=model_versions(store,corpus.skill_id,1)
    assert len(versions['models'])==1 and versions['models'][0]['compatible_native9_106']
    evaluation=enqueue_evaluation(store,corpus.skill_id,1,trained['id']);assert evaluation['project_id']=='03'
    def evaluator(policy,loaded,chunks,guard):
        guard();assert {row['descriptor']['split'] for row in chunks.episodes}=={'development'}
        return {'sealed_test_used':False,'learned_policy_rollout_executed':False,'software_evaluator_fixture':True}
    report=run_rosmaster_evaluation(store,evaluation,loader=lambda _:SimpleNamespace(policy=SimpleNamespace(config=SimpleNamespace(chunk_size=2))),evaluator=evaluator)
    assert report['project_id']=='03' and report['optimizer_steps_executed']==0 and report['weights_unchanged']
    assert report['source_network_requests_sent']==report['physical_control_requests_sent']==0 and not report['dispatch_performed']
    assert not report['independent_full_task_success'] and report['evaluation']['software_evaluator_fixture']
    shadow=enqueue_shadow(store,corpus.skill_id,1,trained['id'])
    assert shadow['project_id']=='03' and shadow['config']['execution_scope']=='shadow_without_dispatch'


@pytest.mark.parametrize('condition',['foreign_project','missing_leader_binding','unknown_format'])
def test_native03_source_identity_or_role_never_promoted_by_registry_metadata(tmp_path,monkeypatch,condition):
    project='02' if condition=='foreign_project' else '03'
    writer,tap,camera,clock,ident,_=setup(tmp_path/'source',project=project)
    try:
        packet,_=publication(tap,camera,clock,mode='keyboard' if condition=='missing_leader_binding' else 'so101_leader')
        assert writer.submit(ident,packet);sealed(writer,ident,outcomes(ident))
        raw=writer.archive(ident).read_bytes();checksum=hashlib.sha256(raw).hexdigest();wrapper=approved(ident,checksum);calls=[]
        def transport(request):
            calls.append(request.url.path)
            if request.url.path.endswith('/review'):return httpx.Response(200,json=wrapper)
            return httpx.Response(200,content=raw,headers={'X-Content-SHA256':checksum})
        client_type=httpx.Client;monkeypatch.setattr(job_runner.httpx,'Client',lambda **kwargs:client_type(transport=httpx.MockTransport(transport),**kwargs))
        store=Store(tmp_path/'nx');job=store.create_job('retarget_import','03',{'episode_id':ident,'archive_sha256':checksum,
            'source_format':'wrong-format' if condition=='unknown_format' else 'native_capture_v1','source_review_sha256':wrapper['review_sha256']})
        config={'nodes':[{'id':'nano','host':'memory-source','port':8092,'token':'memory-token'}]}
        if condition in ('foreign_project','unknown_format'):
            with pytest.raises(ValueError,match='project|format'):job_runner.retarget_import(store,job,config)
            assert not (store.root/'episodes'/'native-retarget'/ident).exists()
            if condition=='unknown_format':assert calls==[]
        else:
            result=job_runner.retarget_import(store,job,config);store.complete_job(job['id'],result)
            report=inspect_mobile_import(store.job(job['id']),managed_root=store.root)
            assert report['mobile_embodied']['admitted_for_training'] and not report['admitted_for_training']
            assert 'native03_measured_leader_input_adapter_not_verified' in report['reasons']
    finally:writer.close()


def test_native03_failed_transfer_retry_reuses_exact_bounded_part_and_current_review(tmp_path,monkeypatch):
    from explorer.native_capture_transfer import CHUNK_BYTES
    writer,tap,camera,clock,ident,_=setup(tmp_path/'source')
    try:
        packet,_=publication(tap,camera,clock);assert writer.submit(ident,packet);sealed(writer,ident,outcomes(ident))
        original=writer.archive(ident).read_bytes();raw=original+b'\0'*(CHUNK_BYTES*4);checksum=hashlib.sha256(raw).hexdigest();wrapper=approved(ident,checksum)
        store=Store(tmp_path/'nx');job=store.create_job('retarget_import','03',{'episode_id':ident,'archive_sha256':checksum,'source_format':'native_capture_v1','source_review_sha256':wrapper['review_sha256']})
        calls=[];attempt=[0]
        class Broken(httpx.SyncByteStream):
            def __iter__(self):
                yield raw[:CHUNK_BYTES]
                raise httpx.ReadError('SOFTWARE interrupted native03 archive')
        def transport(request):
            calls.append((request.method,request.url.path,request.headers.get('Range')))
            if request.url.path.endswith('/review'):return httpx.Response(200,json=wrapper)
            attempt[0]+=1
            headers={'X-Content-SHA256':checksum,'ETag':'"'+checksum+'"','Content-Length':str(len(raw))}
            if attempt[0]==1:return httpx.Response(200,stream=Broken(),headers=headers)
            offset=CHUNK_BYTES;assert request.headers['Range']==f'bytes={offset}-'
            headers.update({'Content-Length':str(len(raw)-offset),'Content-Range':f'bytes {offset}-{len(raw)-1}/{len(raw)}'})
            return httpx.Response(206,content=raw[offset:],headers=headers)
        client_type=httpx.Client;monkeypatch.setattr(job_runner.httpx,'Client',lambda **kwargs:client_type(transport=httpx.MockTransport(transport),**kwargs))
        config={'nodes':[{'id':'nano','host':'memory-source','port':8092,'token':'memory-token'}]}
        with pytest.raises(httpx.ReadError):job_runner.retarget_import(store,job,config)
        part=store.root/'artifacts'/checksum[:2]/(checksum+'.part');assert part.stat().st_size==CHUNK_BYTES
        store.update_job(job['id'],status='failed');retry=store.retry_import_job(job['id'])
        assert retry['config']['retry_of_job_id']==job['id'] and retry['config']['source_review_sha256']==wrapper['review_sha256']
        result=job_runner.retarget_import(store,retry,config)
        assert result['transfer_evidence']['resumed_bytes']==CHUNK_BYTES and result['source_approval']==wrapper
        assert Path(result['artifact']).read_bytes()==raw and writer.archive(ident).read_bytes()==original
        assert all(method=='GET' for method,_,_ in calls) and not part.exists()
    finally:writer.close()


@pytest.mark.parametrize('condition',['cancelled_during_copy','rejected_after_copy'])
def test_native03_late_cancel_or_source_reject_keeps_raw_part_without_admission(tmp_path,monkeypatch,condition):
    writer,tap,camera,clock,ident,_=setup(tmp_path/'source')
    try:
        packet,_=publication(tap,camera,clock);assert writer.submit(ident,packet);sealed(writer,ident,outcomes(ident))
        raw=writer.archive(ident).read_bytes();checksum=hashlib.sha256(raw).hexdigest();wrapper=approved(ident,checksum)
        store=Store(tmp_path/'nx');job=store.create_job('retarget_import','03',{'episode_id':ident,'archive_sha256':checksum,'source_format':'native_capture_v1','source_review_sha256':wrapper['review_sha256']})
        reviews=[0]
        def transport(request):
            if request.url.path.endswith('/review'):
                reviews[0]+=1;current=deepcopy(wrapper)
                if condition=='rejected_after_copy' and reviews[0]>1:
                    current['source_review']['decision']='reject';current['transfer_ready']=False
                    current['review_sha256']=hashlib.sha256(source_bytes(current['source_review'])).hexdigest()
                return httpx.Response(200,json=current)
            if condition=='cancelled_during_copy':store.update_job(job['id'],status='cancel_requested')
            return httpx.Response(200,content=raw,headers={'X-Content-SHA256':checksum})
        client_type=httpx.Client;monkeypatch.setattr(job_runner.httpx,'Client',lambda **kwargs:client_type(transport=httpx.MockTransport(transport),**kwargs))
        with pytest.raises((ValueError,RuntimeError),match='review|cancelled'):
            job_runner.retarget_import(store,job,{'nodes':[{'id':'nano','host':'memory-source','port':8092,'token':'memory-token'}]})
        assert not (store.root/'episodes'/'native-retarget'/ident).exists()
        part=store.root/'artifacts'/checksum[:2]/(checksum+'.part');assert part.is_file()
        assert writer.archive(ident).read_bytes()==raw and store.job(job['id'])['result'] is None
    finally:writer.close()
