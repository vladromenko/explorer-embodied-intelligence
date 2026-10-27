"""SOFTWARE FIXTURES: actual producer/worker with generated JPEG and fake Tap.

No devices, control transport, trained weights or real demonstration is used.
"""
from copy import deepcopy
import hashlib
from io import BytesIO
import json
from pathlib import Path
import threading

import httpx
import numpy as np
from PIL import Image

from explorer import job_runner,skill_workflow as flow
from explorer.store import Store
from explorer_shared.contracts import canonical
from explorer_shared.native_capture import NativeCaptureWriter
from explorer_shared.native_stream03 import NativeDispatchTap
from explorer_shared.node_agent import CameraSource
from test_native_capture import Clock,outcomes
from test_skill_workflow import skill,assign


def native03_aggregate(tmp_path,monkeypatch):
    store=Store(tmp_path/'nx');selected=skill(store);phases=[row['id'] for row in selected['spec']['stages']]
    sessions={split:flow.create_session(store,selected['spec']['skill_id'],1,{'split':split,'scene':'SOFTWARE03-'+split,'project_id':'03'})
        for split in ('train','development','sealed_test')}
    originals={};imports=[];requests=[];client_type=httpx.Client
    for episode_index,variant in enumerate(selected['spec']['coverage_plan']['variants']):
        clock=Clock();tap=NativeDispatchTap(boot_id='SOFTWARE-Nano-boot',clock_domain='nano',clock=clock)
        tap.reference_header({'boot_id':'SOFTWARE-Nano-boot','measured':False,'servo_deg':[90]*6},'e'*64,native_guard_verified=True)
        clock.value=1100;tap.append('base_stop_host_request',{'velocity':[0.,0.,0.]},source_id='SOFTWARE-original-base0',started_ns=1090)
        clock.value=1200;tap.append('arm_host_publish',{'q_commanded_deg':[90]*6,'q_estimated_deg':[90.]*6},source_id='SOFTWARE-original-publisher',started_ns=1190)
        camera=CameraSource('nano-rgbd','SOFTWARE-generated-RGB');clock.value=2000
        writer=NativeCaptureWriter(tmp_path/'source',camera,clock_domain='nano',boot_id='SOFTWARE-Nano-boot',clock=clock)
        consumed=threading.Event();consume=writer._consume
        def completed(*args,consume=consume):
            result=consume(*args);consumed.set();return result
        writer._consume=completed;session=sessions[variant['split']]
        context={'project_id':'03','input_mode':'so101_leader','skill_id':selected['spec']['skill_id'],'skill_version':1,
            'skill_spec_sha256':selected['spec_sha256'],'variant_id':variant['variant_id'],'session_id':session['session_id'],
            'episode_scope':'whole_episode','phase_id':phases[0],
            'split':{'partition':variant['split'],'scene_id':'SOFTWARE03-'+variant['split'],'session_id':session['session_id'],
                'object_instance_id':'SOFTWARE03-object-'+str(episode_index),'group_id':'SOFTWARE03-series-'+str(episode_index),
                'group_by':['scene_id','session_id','episode_id','object_instance_id']}}
        ident=writer.start(context,canonical(tap.since(0)))['recording_id']
        try:
            for index,phase in enumerate(phases):
                tick=3000+index*1000
                if index==5:
                    clock.value=tick-600;writer.pause(ident);clock.value=tick-500;writer.resume(ident)
                clock.value=tick-300
                if index:writer.stage(ident,phase)
                rgb=np.zeros((32,32,3),np.uint8);rgb[...,0]=index*20;rgb[...,1]=episode_index*35+10;rgb[...,2]=np.arange(32)[:,None]*7
                output=BytesIO();Image.fromarray(rgb).save(output,format='JPEG',quality=95)
                camera.publish(output.getvalue(),32,32,monotonic_ns=tick-150)
                before=tap.sequence;clock.value=tick
                tap.append('arm_host_publish',{'q_commanded_deg':[90+index]*6,'q_estimated_deg':[90.+index]*6,
                    'input_source':'keyboard','command_generation':8,'input_received_at':float(tick)},source_id='SOFTWARE-original-publisher',started_ns=tick-50)
                tap.bind_intent(8,float(tick),{'source':{'input_adapter':{'id':'so101_leader','version':'1','input_mode':'so101_leader'},
                    'source_evidence':{'positions':[float(index)]*6,'state_source':'measured_leader','calibration_sha256':'b'*64,
                        'clock_domain':'pi:SOFTWARE-Pi-boot','boot_id':'SOFTWARE-Pi-boot','monotonic_ns':800000+index*1000,
                        'station_control_epoch':'SOFTWARE-leader-cache-epoch','sample_id':'SOFTWARE-leader-'+str(index),
                        'cross_device_clock_mapping_assumed':False}}})
                consumed.clear();assert writer.submit(ident,canonical(tap.since(before)));assert consumed.wait(3),writer.status()
            writer.finish(ident,outcomes(ident,phases));assert writer.done.wait(5),writer.status()
            manifest=writer.folder/'manifest.json';document=json.loads(manifest.read_bytes())
            assert document['admission']['admitted_for_dataset'],document['admission']['missing_facts']
            archive=writer.archive(ident);raw=archive.read_bytes();checksum=hashlib.sha256(raw).hexdigest()
            wrapper=writer.review(ident,decision='approve',actor_id='SOFTWARE-USER',expected_archive_sha256=checksum)
            def transport(request,raw=raw,checksum=checksum,wrapper=wrapper,ident=ident):
                requests.append((request.method,request.url.host,request.url.path));assert request.method=='GET'
                assert request.url.host=='SOFTWARE-Nano.invalid'.lower() and request.headers['Authorization']=='Bearer SOFTWARE-Nano-token'
                if request.url.path=='/native-capture/recordings/'+ident+'/review':return httpx.Response(200,json=wrapper)
                assert request.url.path=='/native-capture/recordings/'+ident+'/download'
                return httpx.Response(200,content=raw,headers={'X-Content-SHA256':checksum,'ETag':'"'+checksum+'"'})
            job=store.create_job('retarget_import','03',{'episode_id':ident,'source_format':'native_capture_v1',
                'archive_sha256':checksum,'source_review_sha256':wrapper['review_sha256']})
            with monkeypatch.context() as patch:
                patch.setattr(job_runner.httpx,'Client',lambda **kwargs:client_type(**kwargs,transport=httpx.MockTransport(transport)))
                result=job_runner.retarget_import(store,job,{'nodes':[{'id':'nano','host':'SOFTWARE-Nano.invalid','port':8092,'token':'SOFTWARE-Nano-token'},
                    {'id':'pi','host':'FORBIDDEN-Pi.invalid','port':8092,'token':'FORBIDDEN'}]})
            assert Path(result['embodied_manifest']).read_bytes()==manifest.read_bytes()
            store.complete_job(job['id'],result);job=store.job(job['id'])
            assign(store,selected,job,variant['variant_id'],session,project_id='03');imports.append(job)
            originals[str(manifest)]=hashlib.sha256(manifest.read_bytes()).hexdigest();originals[str(archive)]=checksum
        finally:writer.close()
    aggregate=flow.build_aggregate(store,selected['spec']['skill_id'],1)
    return Path(aggregate['dataset_manifest']),store,{'all_inputs_are_software_fixtures':True,'real_demonstrations':0,
        'physical_control_requests':0,'optimizer_updates':0,'requests':requests,'originals_sha256':originals,'imports':imports,'selected':selected,'sessions':sessions}
