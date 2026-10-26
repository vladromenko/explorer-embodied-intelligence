"""Real temporary Store/queue and source HTTP bytes, dummy CPU models only."""
from copy import deepcopy
import hashlib
import json
import sys

import httpx
import pytest

from explorer_shared.contracts import canonical,digest,ACTION_NAMES
from explorer_shared.embodied_episode import ACTION_CONTRACT_ID
from explorer.rosmaster_shadow_job import run_rosmaster_shadow
from explorer.so101_shadow_job import run_so101_shadow
from test_cooperative_service import fixture
from test_rosmaster_observation import fixture as nano_source
from test_so101_live_shadow_transport import rig as pi_source


def configured(tmp_path):
    service,store,calls,active,contexts,client=fixture(tmp_path);choices=service.native_choices();models={}
    for role,choice in ((role,choices[role][0]) for role in ('giver','receiver')):
        ref=choice['reference'];folder=store.root/'models'/role;folder.mkdir(parents=True)
        checkpoint=folder/'student_act';checkpoint.mkdir();weight=checkpoint/'dummy-weight'
        weight.write_bytes(b'EXPLICIT_SOFTWARE_CPU_FIXTURE_NOT_A_TRAINED_MODEL:'+role.encode())
        weights={'dummy-weight':hashlib.sha256(weight.read_bytes()).hexdigest()}
        source=folder/'source.json';source.write_bytes(canonical({'schema':'software_cohort_fixture','no_real_training_claim':True}))
        lineage={'skill_id':ref['skill_id'],'skill_version':ref['skill_version'],'spec_sha256':ref['spec_sha256']}
        job=store.create_job('rosmaster_skill_train' if role=='giver' else 'so101_skill_train','02' if role=='giver' else '04',
            {'skill_workflow':lineage} if role=='giver' else dict(lineage,backend='act'))
        result=dict(trained=True,weights_changed=True,save_reload_passed=True,optimizer_steps=8,device='cpu',backend='act',
            checkpoint_dir=str(checkpoint),checkpoint_sha256=weights,source_manifest_path=str(source),
            source_manifest_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),source_kind='SO101_RECORDED_DEMONSTRATIONS',
            software_model_fixture=True)
        if role=='giver':
            model={'schema':'explorer_rosmaster_skill_model_v1','action_contract_id':ACTION_CONTRACT_ID,'action_names':list(ACTION_NAMES),
                'ACT9':{'state_dimension':106,'output_dimension':9},'source_kind':['physical'],
                'skill_id':ref['skill_id'],'skill_version':ref['skill_version'],'skill_spec_sha256':ref['spec_sha256'],'weights_sha256':weights}
            path=folder/'model_manifest.json';path.write_bytes(canonical(model))
            result.update(model_manifest=str(path),model_manifest_sha256=hashlib.sha256(path.read_bytes()).hexdigest())
        store.complete_job(job['id'],result);models[role]=job['id']
    proposed=service.command('propose_skill',{'name':'two original heads software fixture','object_description':'block',
        'receiver_goal_description':'box','outcome_criteria':['independent placement'],
        **{role+'_choice_id':choices[role][0]['choice_id'] for role in ('giver','receiver')},
        **{role+'_model_id':value for role,value in models.items()}},authenticated_actor='owner')['result']
    spec=proposed['spec'];service.command('confirm_skill',{'skill_id':spec['skill_id'],'version':1,'spec_sha256':spec['spec_sha256']},authenticated_actor='owner')
    variants={role:next(row['variant_id'] for row in choices[role][0]['native_variants'] if row['split']=='train') for role in ('giver','receiver')}
    session=service.command('create_session',{'skill_id':spec['skill_id'],'version':1,'spec_sha256':spec['spec_sha256'],
        'scene_id':'scene','object_instance_id':'real-object-label-not-an-outcome','split':'train','native_variants':variants,
        'giver_input_mode':'keyboard','receiver_input_mode':'leader','stationary_chassis_confirmed':True},authenticated_actor='owner')['result']
    return service,store,calls,client,spec,session,models


def command(service,session,**extra):
    return service.command('shadow',{'session_id':session['session_id'],'expected_epoch':session['epoch'],**extra},authenticated_actor='owner')['result']


def test_actual_queue_uses_two_native_contracts_and_shared_cpu_budgets_no_cc_model_import(tmp_path):
    before=set(sys.modules);service,store,calls,client,spec,session,models=configured(tmp_path)
    try:
        first=command(service,session);second=command(service,session)
        assert first['jobs']==second['jobs'] and first['complete_native_reservation']
        jobs=[store.job(item['job_id']) for item in first['jobs'].values()]
        assert {job['kind'] for job in jobs}=={'rosmaster_skill_shadow','so101_skill_shadow'}
        assert {job['project_id'] for job in jobs}=={'02','04'}
        assert [first['native_heads'][role]['action_contract']['dimension'] for role in ('giver','receiver')]==[9,6]
        for job in jobs:
            schedule=store.schedule_status(job['id'])['metadata']
            assert schedule['resources']['gpu_slots']==0 and schedule['resources']['cpu_cores']==2
            assert schedule['resources']['memory_bytes']==2*1024**3 and not schedule['preemptible']
        assert not first['persistent_warm_actor_ready'] and not first['reusable_for_native_dispatch']
        assert not first['both_native_inferences_verified'] and not calls and 'torch' not in set(sys.modules)-before
        assert len(command(service,session,request_id='explicit-new-observation')['jobs'])==2
        assert len([job for job in store.jobs() if job['kind'].endswith('_shadow')])==4
        service.command('stop',{'session_id':session['session_id'],'expected_epoch':session['epoch']},authenticated_actor='owner')
        assert all(not row['current_session_epoch'] for row in service.snapshot(session['session_id'])['queued_native_shadows'])
    finally:service.close();client.close()


def test_both_heads_preflight_before_first_insert_and_original_context_source_tamper_blocks(tmp_path):
    service,store,calls,client,spec,session,models=configured(tmp_path)
    try:
        train=store.job(models['receiver']);source=train['result']['source_manifest_path']
        from pathlib import Path
        Path(source).write_bytes(b'changed original cohort')
        with pytest.raises(ValueError,match='original cohort'):command(service,session)
        assert not [job for job in store.jobs() if job['kind'].endswith('_shadow')] and not calls
    finally:service.close();client.close()


def test_commit_then_lost_queue_return_recovers_exact_jobs_without_duplicate_on_explicit_retry(tmp_path,monkeypatch):
    service,store,calls,client,spec,session,models=configured(tmp_path);original=store.create_job;lost=[False]
    def create(kind,*args,**kwargs):
        job=original(kind,*args,**kwargs)
        if kind=='rosmaster_skill_shadow' and not lost[0]:lost[0]=True;raise OSError('Return lost after SQL commit')
        return job
    monkeypatch.setattr(store,'create_job',create)
    try:
        first=command(service,session);assert not first['complete_native_reservation'] and set(first['jobs'])=={'receiver'}
        second=command(service,session);assert second['complete_native_reservation'] and not second['errors']
        assert len([job for job in store.jobs() if job['kind'].endswith('_shadow')])==2 and not calls
    finally:service.close();client.close()


def test_actual_isolated_get_only_workers_preserve_and_verify_both_original_inputs_without_merging_heads(tmp_path):
    service,store,calls,client,spec,session,models=configured(tmp_path)
    try:
        receipt=command(service,session);nano,_,_,_,_=nano_source();_,_,pi,_=pi_source();requests=[]
        def handler(request):
            assert request.method=='GET';requests.append(request.url.path)
            if request.url.path=='/native-policy/observation':return httpx.Response(200,json=nano.create())
            if request.url.path.startswith('/native-policy/observations/'):
                raw,meta=nano.frame(request.url.path.split('/')[3])
                return httpx.Response(200,content=raw,headers={'X-Observation-ID':meta['observation_id'],'X-Frame-ID':meta['frame_id'],
                    'X-JPEG-SHA256':meta['jpeg_sha256'],'X-Boot-ID':meta['boot_id'],'X-Clock-Domain':meta['clock_domain']})
            if request.url.path=='/station/observation':return httpx.Response(200,json=pi.create())
            raw,meta=pi.frame(request.url.path.split('/')[3])
            return httpx.Response(200,content=raw,headers={'X-Observation-ID':meta['observation_id'],'X-Frame-ID':meta['frame_id'],
                'X-JPEG-SHA256':meta['frame_sha256'],'X-Boot-ID':meta['frame_boot_id']})
        def factory(**kwargs):return httpx.Client(**kwargs,transport=httpx.MockTransport(handler))
        class Native9:
            def predict(self,observation,read,verify_observation):
                assert verify_observation(observation,None)['preceding_command9']['admitted']
                return {'action9':[90]*6+[0]*3,'hardware_io':False,'software_model_fixture':True}
        native_job=store.job(receipt['jobs']['giver']['job_id'])
        result=run_rosmaster_shadow(store,native_job,service.config,client_factory=factory,loader=lambda path:Native9())
        store.complete_job(native_job['id'],result)
        ref=spec['subskills']['receiver']
        def predict(model,rgb,state,**kwargs):
            return {'predicted_native6':[0]*5+[50],'hardware_commands_dispatched':False,'physical_execution_authorized':False,
                'independent_full_task_success':False,'software_model_fixture':True,
                'lineage':{'skill_id':ref['skill_id'],'skill_version':ref['skill_version'],'spec_sha256':ref['spec_sha256'],
                    'checkpoint_sha256':ref['checkpoint_sha256']}}
        source_job=store.job(receipt['jobs']['receiver']['job_id'])
        result=run_so101_shadow(store,source_job,service.config,client_factory=factory,loader=lambda *a,**k:object(),predictor=predict)
        store.complete_job(source_job['id'],result)
        inspected=service.snapshot(session['session_id'])['queued_native_shadows'][0]
        assert inspected['both_native_inferences_verified'] and not inspected['observations_synchronized_between_robots']
        assert not inspected['reusable_for_native_dispatch'] and not inspected['persistent_warm_actor_ready'] and not calls
        path=store.root/'jobs'/source_job['id']/'frame.jpg';path.write_bytes(b'corrupt-after-worker')
        changed=service.snapshot(session['session_id'])['queued_native_shadows'][0]
        assert not changed['both_native_inferences_verified'] and 'evidence_error' in changed['native_heads']['receiver']
        assert len(requests)==5  # Nano packet+frame; Pi prewarm packet + NEW packet+frame.
    finally:service.close();client.close()
