"""SOFTWARE FIXTURES ONLY: canonical native02 through actual import and learning.

The producer/tap/camera cache are real classes with memory-only fabricated source
publications and generated JPEGs; no native transport, device or motor exists.
Only explicitly opted-in tests may allocate official LeRobot/Torch models.
"""
from copy import deepcopy
import hashlib
from io import BytesIO
import json
import os
from pathlib import Path
import subprocess
import sys
import threading

import httpx
import numpy as np
from PIL import Image
import pytest

from explorer import job_runner
from explorer.store import Store
from explorer.skill_workflow import build_aggregate,create_session
from explorer_shared.contracts import canonical
from explorer_shared.native_capture import NativeCaptureWriter
from explorer_shared.native_capture_service import NativeCaptureService
from explorer_shared.native_stream03 import NativeDispatchTap
from explorer_shared.node_agent import CameraSource
from explorer_shared.learning.rosmaster_skill_pipeline import CanonicalCorpus
from test_native_capture import Clock,outcomes
from test_native_capture_approval import approved
from test_skill_workflow import skill,assign


def native_aggregate(tmp_path,monkeypatch,*,store=None,selected=None,session_for_variant=None,prepare_context=None):
    store=store if store is not None else Store(tmp_path/'nx')
    selected=selected if selected is not None else skill(store);phases=[row['id'] for row in selected['spec']['stages']]
    sessions={split:create_session(store,selected['spec']['skill_id'],1,{'split':split,'scene':'software-scene-'+split})
        for split in ('train','development','sealed_test')} if session_for_variant is None else {}
    requests=[];originals={};client_type=httpx.Client
    for episode_index,variant in enumerate(selected['spec']['coverage_plan']['variants']):
        clock=Clock();clock.value=1000;tap=NativeDispatchTap(boot_id='software-boot',clock_domain='nano',clock=clock)
        tap.reference_header({'boot_id':'software-boot','measured':False,'servo_deg':[90]*6},'e'*64,native_guard_verified=True)
        clock.value=1100;tap.append('base_stop_host_request',{'velocity':[0.,0.,0.]},source_id='SOFTWARE-original-base0',started_ns=1090)
        clock.value=1200;tap.append('arm_host_publish',{'q_commanded_deg':[90]*6,'q_estimated_deg':[90.]*6},source_id='SOFTWARE-original-arm',started_ns=1190)
        camera=CameraSource('nano-rgbd','SOFTWARE-existing-cache');clock.value=2000
        writer=NativeCaptureWriter(tmp_path/'source',camera,clock_domain='nano',boot_id='software-boot',clock=clock)
        journal=NativeCaptureService(writer,None,clock=clock)
        consumed=threading.Event();original_consume=writer._consume
        def consume(*args,original_consume=original_consume):
            result=original_consume(*args);consumed.set();return result
        writer._consume=consume
        session=session_for_variant(episode_index,deepcopy(variant)) if session_for_variant else sessions[variant['split']]
        context={'project_id':'02','input_mode':'keyboard','skill_id':selected['spec']['skill_id'],'skill_version':1,
            'skill_spec_sha256':selected['spec_sha256'],'variant_id':variant['variant_id'],'session_id':session['session_id'],
            'episode_scope':'whole_episode','phase_id':phases[0],'operator_input_trace_required':True,
            'split':{'partition':variant['split'],'scene_id':'software-scene-'+variant['split'],'session_id':session['session_id'],
                'object_instance_id':'software-object-'+str(episode_index),'group_id':'software-series-'+str(episode_index),
                'group_by':['scene_id','session_id','episode_id','object_instance_id']}}
        if prepare_context is not None:context=prepare_context(episode_index,deepcopy(variant),deepcopy(session),deepcopy(context))
        identity=writer.start(context,canonical(tap.since(0)))['recording_id']
        journal.active_id=identity  # Passive callback only; fetch_enabled staysFalse.
        try:
            for index,phase in enumerate(phases):
                tick=3000+index*1000
                if index==5:
                    clock.value=tick-600;writer.pause(identity)
                    clock.value=tick-500;writer.resume(identity)
                clock.value=tick-300
                if index:writer.stage(identity,phase)
                rgb=np.zeros((32,32,3),np.uint8);rgb[...,0]=index*20;rgb[...,1]=episode_index*35+10;rgb[...,2]=np.arange(32)[:,None]*7
                stream=BytesIO();Image.fromarray(rgb).save(stream,format='JPEG',quality=95)
                camera.publish(stream.getvalue(),32,32,monotonic_ns=tick-150)
                clock.value=tick-75
                journal.observe_operator_input({'source':'keyboard','keys':['KeyI'],'observing':True},
                    {'state':'ACTIVE','arm_mode':'cartesian'},200,tick-100,{'input_origin':'SOFTWARE_FIXTURE_NO_HARDWARE'})
                before=tap.sequence;clock.value=tick
                tap.append('arm_host_publish',{'q_commanded_deg':[90+index]*6,'q_estimated_deg':[90.+index]*6,
                    'input_source':'keyboard','command_generation':index+1,'input_received_at':float(tick)},
                    source_id='SOFTWARE-original-arm',started_ns=tick-50)
                consumed.clear();assert writer.submit(identity,canonical(tap.since(before)))
                assert consumed.wait(3),writer.status()
            writer.finish(identity,outcomes(identity,phases));assert writer.done.wait(5),writer.status()
            source_manifest=writer.folder/'manifest.json';document=json.loads(source_manifest.read_bytes())
            assert document['admission']['admitted_for_dataset'],document['admission']['missing_facts']
            assert document['admission']['eligible_as_successful_bc_expert']==(variant['split']=='train')
            assert len(document['embodied_episode']['operator_input_trace'])==10
            assert [row['recording_segment_id'] for row in document['embodied_episode']['samples']]==[0]*5+[1]*5
            archive=writer.archive(identity);raw=archive.read_bytes();checksum=hashlib.sha256(raw).hexdigest()
            review=(writer.review(identity,decision='approve',actor_id='SOFTWARE-FIXTURE-OWNER',expected_archive_sha256=checksum)
                if hasattr(writer,'review') else approved(identity,checksum,actor='SOFTWARE-FIXTURE-OWNER'))
            def handler(request,raw=raw,review=review,checksum=checksum,identity=identity):
                requests.append((request.method,request.url.path));assert request.method=='GET'
                assert request.headers['Authorization']=='Bearer software-fixture-only'
                if request.url.path=='/native-capture/recordings/'+identity+'/review':return httpx.Response(200,json=review)
                assert request.url.path=='/native-capture/recordings/'+identity+'/download'
                return httpx.Response(200,content=raw,headers={'X-Content-SHA256':checksum,'ETag':'"'+checksum+'"'})
            job=store.create_job('mobile_import','02',{'episode_id':identity,'source_format':'native_capture_v1','archive_sha256':checksum,'source_review_sha256':review['review_sha256']})
            with monkeypatch.context() as patch:
                patch.setattr(job_runner.httpx,'Client',lambda **kwargs:client_type(**kwargs,transport=httpx.MockTransport(handler)))
                result=job_runner.station_import(store,job,{'nodes':[{'id':'nano','host':'software.invalid','port':8092,'token':'software-fixture-only'}]})
            assert Path(result['embodied_manifest']).read_bytes()==source_manifest.read_bytes()
            store.complete_job(job['id'],result);job=store.job(job['id']);assign(store,selected,job,variant['variant_id'],session)
            originals[str(source_manifest)]=hashlib.sha256(source_manifest.read_bytes()).hexdigest()
            originals[str(archive)]=checksum
        finally:journal.close()
    result=build_aggregate(store,selected['spec']['skill_id'],1);path=Path(result['dataset_manifest']);corpus=CanonicalCorpus(path)
    return path,store,{'all_sources_are_software_fixtures':True,'physical_validation':False,'request_methods':[method for method,_ in requests],
        'originals_sha256':originals,'native_source_original_ids':[row['descriptor']['source_episode_id'] for row in corpus.episodes],
        'missing_real_demonstrations':True}


def test_actual_native_capture_to_approved_import_and_aggregate_keeps_raw_bytes_and_null_feedback(tmp_path,monkeypatch):
    path,store,evidence=native_aggregate(tmp_path,monkeypatch);corpus=CanonicalCorpus(path)
    assert len(corpus.train)==3 and len(corpus.development)==1 and len(corpus.sealed_ids)==1
    assert set(evidence['request_methods'])=={'GET'} and evidence['missing_real_demonstrations']
    assert all(hashlib.sha256(Path(path).read_bytes()).hexdigest()==expected for path,expected in evidence['originals_sha256'].items())
    assert all(row['descriptor']['source_format']=='native_capture_v1' for row in corpus.episodes)
    for episode in corpus.episodes:
        assert len(episode['frames'])==10
        for frame in episode['frames']:
            assert len(frame['action'])==9 and frame['sample']['channels']['joint_feedback']['value'] is None
            assert not frame['masks'][9:15].any() and frame['masks'][:9].all()


@pytest.mark.skipif(os.environ.get('EXPLORER_RUN_NATIVE02_CPU_QUALIFICATION')!='1',reason='Explicit bounded CPU2/4GiB qualification window required')
def test_official_lerobot_native9_roundtrip_and_pause_segments_actual_no_optimizer(tmp_path):
    from explorer_shared.learning.rosmaster_lerobot_dataset import export_official_dataset,OfficialFusedChunks
    from explorer_shared.learning.rosmaster_skill_pipeline import FusedChunks
    from test_rosmaster_skill_pipeline import fixture_aggregate
    path,store=fixture_aggregate(tmp_path,native02=True,paused=True);corpus=CanonicalCorpus(path)
    original=dict(corpus.source_files);folder,index=export_official_dataset(corpus,tmp_path/'official',image_size=32,partition='train')
    assert index['original_demo_count']==3 and index['segment_episode_count']==6
    official=OfficialFusedChunks(corpus,corpus.train,3,32,folder,index);baseline=FusedChunks(corpus,corpus.train,3,32)
    for i in range(len(official)):
        actual=official[i];expected=baseline[i]
        for key in expected:assert actual[key].equal(expected[key]),key
    assert official[4]['action_is_pad'].tolist()==[False,True,True]
    assert export_official_dataset(corpus,folder,image_size=32,partition='train')[1]==index
    official.recheck();corpus.recheck()
    assert all(hashlib.sha256(Path(name).read_bytes()).hexdigest()==checksum for name,checksum in original.items())


@pytest.mark.skipif(os.environ.get('EXPLORER_RUN_NATIVE02_CPU_QUALIFICATION')!='1',reason='Explicit bounded CPU2/4GiB qualification window required')
def test_actual_native02_ACT_pause_new_process_resume_reload_DEV_and_GET_only_shadow(tmp_path,monkeypatch):
    from explorer.skill_workflow import enqueue_training
    from explorer.rosmaster_learning import model_versions,enqueue_evaluation,enqueue_shadow
    from explorer.rosmaster_eval_job import run_rosmaster_evaluation
    from explorer.rosmaster_shadow_job import run_rosmaster_shadow
    from explorer_shared.learning.checkpoint_control import clear_pause_request,read_json
    from test_rosmaster_observation import fixture as source_fixture
    path,store,evidence=native_aggregate(tmp_path,monkeypatch);corpus=CanonicalCorpus(path)
    settings={'act_steps':8,'batch_size':2,'image_size':32,'chunk_size':2}
    job=enqueue_training(store,corpus.skill_id,1,settings,project_id='02')
    worker=Path(__file__).parent/'fixtures'/'native02_learning_worker.py';processes=[]
    def launch(identity,phase):
        run=subprocess.run([sys.executable,str(worker),str(store.root),identity,phase],text=True,capture_output=True,timeout=180)
        (tmp_path/(identity+'-'+phase+'.stdout')).write_text(run.stdout)
        (tmp_path/(identity+'-'+phase+'.stderr')).write_text(run.stderr)
        worker_identity=read_json(store.root/'jobs'/identity/('qualification-worker-'+phase+'.json'))
        processes.append({'phase':phase,'returncode':run.returncode,'job_id':identity,'worker':worker_identity})
        return run
    paused=launch(job['id'],'pause4');assert paused.returncode==75,paused.stderr[-6000:]
    folder=store.root/'jobs'/job['id'];receipt=read_json(folder/'optimizer-paused.json')
    assert receipt['completed_steps']==4 and receipt['optimizer_RNG_sampler_durably_saved']
    clear_pause_request(folder)
    resumed=launch(job['id'],'resume');assert resumed.returncode==0,resumed.stderr[-6000:]
    completed=store.job(job['id']);assert completed['status']=='completed';result=completed['result']
    assert processes[0]['worker']==receipt['worker'] and processes[1]['worker']['pid']!=receipt['worker']['pid']
    assert result['training_resume']['resumed_from_step']==4 and result['training_resume']['optimizer_steps_executed_this_process']==4
    assert result['optimizer_steps']==8 and result['weights_changed'] and result['save_reload_passed']
    assert result['official_dataset']['target_dimension']==9 and result['official_dataset']['original_train_demo_count']==3
    # New process using the same immutable cohort, an independent private output.
    reference=store.create_job('rosmaster_skill_train','02',deepcopy(job['config']))
    uninterrupted=launch(reference['id'],'uninterrupted');assert uninterrupted.returncode==0,uninterrupted.stderr[-6000:]
    original=store.job(reference['id'])['result']
    assert result['checkpoint_sha256']['model.safetensors']==original['checkpoint_sha256']['model.safetensors']
    partial_losses=json.loads((folder/'act_training.json').read_bytes())['losses']
    full_losses=json.loads((store.root/'jobs'/reference['id']/'act_training.json').read_bytes())['losses']
    assert partial_losses==full_losses
    assert model_versions(store,corpus.skill_id,1)['models'][0]['compatible_native9_106']
    evaluation_job=enqueue_evaluation(store,corpus.skill_id,1,job['id']);evaluation=run_rosmaster_evaluation(store,evaluation_job)
    store.complete_job(evaluation_job['id'],evaluation)
    assert evaluation['weights_unchanged'] and not evaluation['dispatch_performed']
    assert evaluation['evaluation']['whole_task_success']=='NOT_EVALUATED' and not evaluation['evaluation']['sealed_test_used']
    source,native,sensors,jpeg,tick=source_fixture();source.sensors=None;calls=[]
    def source_http(request):
        calls.append((request.method,request.url.path));assert request.method=='GET'
        assert request.headers['Authorization']=='Bearer software-fixture-only'
        if request.url.path=='/native-policy/observation':return httpx.Response(200,json=source.create())
        identity=request.url.path.split('/')[3];raw,metadata=source.frame(identity)
        return httpx.Response(200,content=raw,headers={'X-Observation-ID':identity,'X-Frame-ID':metadata['frame_id'],
            'X-Boot-ID':metadata['boot_id'],'X-JPEG-SHA256':metadata['jpeg_sha256'],'X-Clock-Domain':metadata['clock_domain']})
    client_type=httpx.Client
    def client(**kwargs):return client_type(**kwargs,transport=httpx.MockTransport(source_http))
    shadow_job=enqueue_shadow(store,corpus.skill_id,1,job['id'])
    shadow=run_rosmaster_shadow(store,shadow_job,{'nodes':[{'id':'nano','host':'software.invalid','port':8092,'token':'software-fixture-only'}]},client_factory=client)
    store.complete_job(shadow_job['id'],shadow)
    assert not shadow['dispatch_performed'] and len(shadow['action9'])==9 and shadow['physical_control_requests_sent']==0
    assert all(hashlib.sha256(Path(path).read_bytes()).hexdigest()==expected for path,expected in evidence['originals_sha256'].items())
    report={'schema':'explorer_native02_software_learning_qualification_v1','all_sources_are_software_fixtures':True,
        'real_demonstrations_produced':0,'production_state_used':False,'physical_requests_sent':0,'independent_full_task_success':False,
        'pipeline':['native_capture','approved_authenticated_import','canonical_aggregate','official_LeRobotDataset_native_target9','ACT8',
            'pause4_exit75_new_process_resume4','saved_model_version','saved_DEV_diagnostics','fresh_source_fixture_GET_only_shadow'],
        'processes':processes,'resumed_model_safetensors_sha256':result['checkpoint_sha256']['model.safetensors'],
        'uninterrupted_weights_and_losses_equal':True,'source_originals':evidence,'evaluation':evaluation,'shadow':shadow,
        'missing_real_demo_gate_retained':True,'live_native_reference_qualification_claimed':False}
    (tmp_path/'qualification.json').write_bytes(canonical(report))
