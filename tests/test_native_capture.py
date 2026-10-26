from copy import deepcopy
import hashlib
import json
import threading

import pytest

from explorer_shared.native_capture import NativeCaptureWriter,import_native_capture
from explorer_shared.native_stream03 import NativeDispatchTap
from explorer_shared.node_agent import CameraSource


def encoded(value):
    return json.dumps(value,sort_keys=True,separators=(',',':')).encode()


class Clock:
    value=1000
    def __call__(self):
        return self.value


def context(project='03',mode='so101_leader'):
    return {'project_id':project,'input_mode':mode,'skill_id':'skill','skill_version':1,
            'skill_spec_sha256':'a'*64,'variant_id':'variant','session_id':'session',
            'episode_scope':'whole_episode','phase_id':'coordinated_reach',
            'split':{'partition':'train','scene_id':'scene','session_id':'session',
                      'object_instance_id':'object','group_id':'group',
                      'group_by':['scene_id','session_id','episode_id','object_instance_id']}}


def setup(tmp_path,*,known_base=True,project='03',mode='so101_leader',**options):
    clock=Clock();tap=NativeDispatchTap(boot_id='boot',clock_domain='nano',clock=clock)
    tap.reference_header({'boot_id':'boot','measured':False,'servo_deg':[90]*6},'e'*64,native_guard_verified=True)
    if known_base:
        clock.value=1100
        tap.append('base_stop_host_request',{'velocity':[0.,0.,0.]},source_id='mock-original-stop',started_ns=1090)
    clock.value=1200
    tap.append('arm_host_publish',{'q_commanded_deg':[90]*6,'q_estimated_deg':[90.]*6},
               source_id='mock-original-arm-publisher',started_ns=1190)
    camera=CameraSource('nano-rgbd','mock-existing-owner')
    clock.value=2000
    writer=NativeCaptureWriter(tmp_path,camera,clock_domain='nano',boot_id='boot',clock=clock,**options)
    initial=encoded(tap.since(0));status=writer.start(context(project,mode),initial)
    return writer,tap,camera,clock,status['recording_id'],initial


def publication(tap,camera,clock,*,stamp=2250,mode='so101_leader'):
    jpeg=b'\xff\xd8original-native-camera-'+str(stamp).encode()+b'\xff\xd9'
    camera.publish(jpeg,4,3,monotonic_ns=stamp-150)
    before=tap.sequence;clock.value=stamp
    tap.append('arm_host_publish',{'q_commanded_deg':[92]*6,'q_estimated_deg':[91.]*6,
                                  'input_source':'keyboard','command_generation':8,'input_received_at':float(stamp)},
               source_id='mock-original-arm-publisher',started_ns=stamp-50)
    tap.bind_intent(8,float(stamp),{'source':{'input_adapter':{'id':mode,'version':'1','input_mode':mode},
        'source_evidence':{'state_source':'measured_leader','calibration_sha256':'b'*64,'sample_id':'MOCK-leader'}}})
    return encoded(tap.since(before)),jpeg


def outcomes(ident,phases=('coordinated_reach',)):
    outcome={'status':'success','method':'operator_observation','observer_id':'MOCK-authenticated-owner',
             'independent_of_action_commands':True,'metrics':{'object_placed':True}}
    return encoded({'recording_id':ident,'operator_outcome':'success',
                    'outcomes':{'end_to_end':deepcopy(outcome),'per_phase':{phase:deepcopy(outcome) for phase in phases}}})


def sealed(writer,ident,label=None):
    writer.finish(ident,label);assert writer.done.wait(3),writer.status()
    assert writer.status()['state']=='SEALED',writer.status()
    path=writer.folder/'manifest.json'
    return json.loads(path.read_bytes())


@pytest.mark.parametrize('project,mode',[('02','keyboard'),('02','browser_gamepad'),('03','so101_leader')])
def test_same_target_producer_full9_actual_mock_native_commands_original_bytes_and_masked_feedback(tmp_path,project,mode):
    writer,tap,camera,clock,ident,initial=setup(tmp_path,project=project,mode=mode)
    try:
        packet,jpeg=publication(tap,camera,clock,mode=mode)
        assert writer.submit(ident,packet)
        label=outcomes(ident)
        result=sealed(writer,ident,label)
        admission=result['admission']
        assert admission['admitted_for_dataset'],admission['missing_facts']
        assert admission['eligible_as_successful_bc_expert']  # MOCK source labels only
        assert result['source_episode_metadata']['id']==ident
        assert result['embodied_episode']['episode_id']==ident
        assert result['embodied_episode']['samples'][0]['action']['values']==[92.]*6+[0.]*3
        assert result['embodied_episode']['samples'][0]['channels']['preceding_command9']['value']['values']==[91.]*6+[0.]*3
        assert not admission['channels']['joint_feedback']['mask']
        assert not admission['measured_attainment_or_contact_claimed'] and not admission['physical_qualification']
        assert admission['input_adapter_provenance']['input_mode']==mode
        assert (writer.folder/'sources/initial-native-page.json').read_bytes()==initial
        assert (writer.folder/'sources/native-page-000001.json').read_bytes()==packet
        assert (writer.folder/'sources/outcome.json').read_bytes()==label
        assert (writer.folder/('frames/'+hashlib.sha256(jpeg).hexdigest()+'.jpg')).read_bytes()==jpeg
        assert all(hashlib.sha256((writer.folder/name).read_bytes()).hexdigest()==digest
                   for name,digest in result['embodied_source_files_sha256'].items())
        assert result['motor_commands']==0 and not result['physical_success_inferred']
        descriptor=writer.ready_recordings()[0]
        assert descriptor['recording_id']==ident and descriptor['project_id']==project
        assert descriptor['input_mode']==mode and len(descriptor['archive_sha256'])==64
    finally:
        writer.close()


def test_chassis_ban_or_rgb_does_not_invent_missing_base_command_context(tmp_path):
    writer,tap,camera,clock,ident,_=setup(tmp_path,known_base=False)
    try:
        packet,jpeg=publication(tap,camera,clock);writer.submit(ident,packet)
        result=sealed(writer,ident,outcomes(ident))
        assert result['analysis_only']
        action=result['embodied_episode']['samples'][0]['action']
        assert action['values']==[92.]*6+[None]*3 and action['mask']==[True]*6+[False]*3
        assert not result['admission']['action9_verified']
    finally:
        writer.close()


def test_missing_prior_camera_or_independent_outcome_stays_analysis_only(tmp_path):
    writer,tap,camera,clock,ident,_=setup(tmp_path)
    try:
        packet,jpeg=publication(tap,camera,clock)
        camera._history.clear()  # MO CK loss of old cache; no newer-image fallback
        writer.submit(ident,packet)
        result=sealed(writer,ident)
        assert result['analysis_only'] and not result['admission']['rgb_verified']
        assert not result['admission']['operator_success'] and not result['admission']['eligible_as_successful_bc_expert']
        assert result['embodied_episode']['samples'][0]['channels']['camera_rgb']['value'] is None
    finally:
        writer.close()


def test_finish_waits_accepted_packet_and_does_not_label_late_or_next_attempt(tmp_path):
    writer,tap,camera,clock,ident,_=setup(tmp_path)
    entered,release=threading.Event(),threading.Event();original=writer._consume
    def blocked(*args):
        entered.set();assert release.wait(2);return original(*args)
    writer._consume=blocked
    try:
        packet,jpeg=publication(tap,camera,clock);writer.submit(ident,packet)
        assert entered.wait(1)
        writer.finish(ident,outcomes(ident))
        assert not writer.submit(ident,packet)
        with pytest.raises(RuntimeError):
            writer.start(context(),encoded(tap.since(0)))
        assert not writer.done.is_set()
        release.set();assert writer.done.wait(3),writer.status()
        result=json.loads((writer.folder/'manifest.json').read_bytes())
        assert result['recording_id']==ident and len(result['embodied_episode']['samples'])==1
    finally:
        release.set();writer.close()


def test_phase_received_on_nano_uses_native_time_even_if_packets_are_delayed(tmp_path):
    writer,tap,camera,clock,ident,_=setup(tmp_path)
    try:
        old_packet,_=publication(tap,camera,clock,stamp=2250)
        clock.value=3000
        event=writer.stage(ident,'grasp_confirm')
        assert event['stamp']['clock_domain']=='nano'
        writer.submit(ident,old_packet)
        new_packet,_=publication(tap,camera,clock,stamp=3250);writer.submit(ident,new_packet)
        result=sealed(writer,ident,outcomes(ident,('coordinated_reach','grasp_confirm')))
        phases=result['embodied_episode']['phases']
        assert [phase['id'] for phase in phases]==['coordinated_reach','grasp_confirm']
        assert [phase['start_sample'] for phase in phases]==[0,1]
    finally:
        writer.close()


def test_budget_defers_admission_without_losing_originals_and_restart_marks_incomplete(tmp_path):
    writer,tap,camera,clock,ident,_=setup(tmp_path,admission_byte_budget=1)
    try:
        packet,jpeg=publication(tap,camera,clock);writer.submit(ident,packet)
        result=sealed(writer,ident,outcomes(ident))
        assert result['analysis_only']
        assert 'OFFLINE_ADMISSION_REQUIRED_PAYLOAD_BYTE_BUDGET' in result['admission']['missing_facts']
        assert (writer.folder/'sources/native-page-000001.json').read_bytes()==packet
    finally:
        writer.close()
    interrupted=tmp_path/'interrupted';interrupted.mkdir()
    original=b'{"state":"RECORDING","recording_id":"interrupted"}'
    (interrupted/'manifest.json').write_bytes(original)
    again=NativeCaptureWriter(tmp_path,camera,clock_domain='nano',boot_id='boot')
    try:
        assert (interrupted/'manifest.interrupted-original.json').read_bytes()==original
        assert json.loads((interrupted/'manifest.json').read_bytes())['state']=='interrupted'
    finally:
        again.close()


def test_intent_ack_and_unbound_leader_metadata_cannot_create_an_expert_native_episode(tmp_path):
    writer,tap,camera,clock,ident,_=setup(tmp_path)
    try:
        packet,jpeg=publication(tap,camera,clock)
        page=json.loads(packet)
        page['receipts'][0]['native_host_publication_returned']=False
        page['receipts'][0]['source_intent_binding']=None
        writer.submit(ident,encoded(page))
        result=sealed(writer,ident,outcomes(ident))
        assert result['analysis_only']
        blockers=result['embodied_episode']['recording']['capture_blockers']
        assert 'NATIVE_HOST_PUBLICATION_NOT_QUALIFIED' in blockers
        assert 'MEASURED_LEADER_SOURCE_BINDING_MISSING' in blockers
        assert not result['admission']['eligible_as_successful_bc_expert']
    finally:
        writer.close()


def test_archive_nx_import_preserves_exact_originals_and_rechecks_existing_collision(tmp_path):
    writer,tap,camera,clock,ident,_=setup(tmp_path/'nano')
    try:
        packet,jpeg=publication(tap,camera,clock);writer.submit(ident,packet)
        result=sealed(writer,ident,outcomes(ident))
        archive=writer.archive(ident);digest=hashlib.sha256(archive.read_bytes()).hexdigest()
        imported=import_native_capture(archive,digest,tmp_path/'nx')
        assert imported['admission']['admitted_for_dataset'],imported['admission']['missing_facts']
        assert imported['recording_id']==ident and imported['dataset_manifest'] is None
        folder=tmp_path/'nx'/ident
        assert (folder/'manifest.json').read_bytes()==(writer.folder/'manifest.json').read_bytes()
        assert (folder/'original-source-transport.tar').read_bytes()==archive.read_bytes()
        assert (folder/'sources/native-page-000001.json').read_bytes()==packet
        assert import_native_capture(archive,digest,tmp_path/'nx')['embodied_manifest']==imported['embodied_manifest']
        (folder/'sources/native-page-000001.json').write_bytes(b'tampered-original')
        with pytest.raises(ValueError,match='Existing imported original'):
            import_native_capture(archive,digest,tmp_path/'nx')
        with pytest.raises(ValueError,match='archive SHA'):
            import_native_capture(archive,'0'*64,tmp_path/'other')
    finally:
        writer.close()


def test_available_causal_optional_sensor_cache_is_retained_with_original_bytes_and_partial_masks(tmp_path):
    raw=b'{"actual_sensor_cache":"MOCK raw bytes with original clock"}'
    def provider(row,frame):
        stamp={'clock_domain':'nano','boot_id':'boot','monotonic_ns':row['call_started_monotonic_ns']-10}
        return {'source_payloads':{'sensors/'+hashlib.sha256(raw).hexdigest()+'.json':raw},'channels':{
            'base_velocity':{'present':True,'available':True,'value':{'values':[0.,0.,0.],
                'units':['m/s','m/s','rad/s'],'state_source':'measured'},'mask':True,
                'stamp':stamp,'source_id':'MOCK-original-wheel-odometry'},
            'camera_calibration':{'present':True,'available':True,'value':{'intrinsics':[1,0,1,0,1,1,0,0,1],
                'distortion':[0,0,0,0,0],'extrinsics':None},'mask':{'intrinsics':True,'distortion':True,'extrinsics':False},
                'stamp':stamp,'source_id':'MOCK-original-camera-info'}}}
    writer,tap,camera,clock,ident,_=setup(tmp_path,observation_provider=provider)
    try:
        packet,_=publication(tap,camera,clock);writer.submit(ident,packet)
        result=sealed(writer,ident,outcomes(ident));report=result['admission']
        assert report['admitted_for_dataset'],report['missing_facts']
        assert report['channels']['base_velocity']['recorded'] and report['channels']['base_velocity']['admitted']
        assert not report['channels']['base_velocity']['model_used']
        assert report['channels']['camera_calibration']['available'] and not report['channels']['camera_calibration']['admitted']
        assert (writer.folder/('sensors/'+hashlib.sha256(raw).hexdigest()+'.json')).read_bytes()==raw
    finally:
        writer.close()
