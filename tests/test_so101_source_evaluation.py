"""Injected SOFTWARE Station/model/grant fixtures only; no actual motor authorization."""
from io import BytesIO
import json
from pathlib import Path
import uuid

import numpy as np
from PIL import Image
import pytest

from test_transfer_source import fixture
from explorer import so101_source_evaluation as evaluation
from explorer import transfer_source
from explorer_shared.source_archive import sha
from explorer_shared.so101_station import NAMES,UNITS


class Clock:
    def __init__(self):self.now=10_000_000_000
    def __call__(self):return self.now
    def sleep(self,seconds):self.now+=int(seconds*1e9)


class Station:
    execution_domain='SIMULATION'
    def __init__(self,clock,calibration,*,dispatch=True):
        self.clock,self.calibration=clock,calibration
        self.epoch='native-initial';self.mode='idle';self.count=0;self.frames={};self.commands=[];self.last=None;self.pending=None;self.dispatch=dispatch
    def status(self):
        if self.pending is not None and self.dispatch:
            body=self.pending;self.pending=None;self.clock.sleep(.001)
            self.last=dict(control_epoch=self.epoch,sequence=body['sequence'],requested=body['action'],dispatched=body['action'],
                monotonic_ns=self.clock(),clock_domain='native-source',source_observation=dict(frame_id=body['source_frame_id']))
        return dict(available=True,control_epoch=self.epoch,mode=self.mode,last_command=self.last,configuration_writes=0,action_names=list(NAMES),action_units=UNITS)
    def observation(self):
        self.clock.sleep(.01);self.count+=1;ident=uuid.uuid4().hex
        image=Image.new('RGB',(2,2),(self.count%255,20,30));buffer=BytesIO();image.save(buffer,format='JPEG');jpeg=buffer.getvalue()
        rgb=np.asarray(Image.open(BytesIO(jpeg)).convert('RGB'))
        observation=dict(observation_id=ident,camera_id='SOFTWARE-camera',frame_id='native-frame-'+str(self.count),frame_sha256=sha(jpeg),decoded_rgb_sha256=sha(rgb.tobytes()),
            width=2,height=2,follower_calibration_sha256=self.calibration,state_basis='measured_state',
            frame_clock_domain='native-source',state_clock_domain='native-source',source_cutoff_clock_domain='native-source',
            frame_boot_id='native-boot',state_boot_id='native-boot',source_cutoff_boot_id='native-boot',
            frame_monotonic_ns=self.clock()-2,state_monotonic_ns=self.clock()-1,source_cutoff_monotonic_ns=self.clock(),source_cutoff_basis='native_observation_packet_created')
        self.frames[ident]=(jpeg,observation)
        return dict(schema='explorer_so101_cached_observation_v1',observation=observation,measured_native6=[0.]*6,valid_mask=[True]*6,names=list(NAMES),units=UNITS)
    def frame(self,ident):
        jpeg,o=self.frames[ident]
        return jpeg,{'x-observation-id':ident,'x-frame-id':o['frame_id'],'x-boot-id':o['frame_boot_id'],'x-jpeg-sha256':sha(jpeg)}
    def command(self,operation,body):
        self.commands.append((operation,dict(body)))
        if operation=='policy/start':
            assert body['expected_control_epoch']==self.epoch
            self.epoch=uuid.uuid4().hex;self.mode='policy'
        elif operation=='action':
            assert body['control_epoch']==self.epoch
            self.pending=dict(body)
        elif operation=='stop':
            assert body.get('expected_control_epoch',self.epoch)==self.epoch
            self.epoch=uuid.uuid4().hex;self.mode='idle'
        return dict(available=True,control_epoch=self.epoch,mode=self.mode,last_command=self.last)


class Permission:
    def __init__(self,station,binding):self.station,self.binding=station,binding;self.id=uuid.uuid4().hex;self.revoked=False;self.new_owner=False
    def check(self,ident,binding,epoch):
        if self.revoked or ident!=self.id or binding!=self.binding or epoch!=self.station.epoch:raise ValueError('Observed grant revoked or wrong epoch')
        return dict(grant_id=self.id,binding=binding,control_epoch=epoch,execution_permission=True,software_fixture_only=True,
            follower_calibration_sha256=self.station.calibration,displayed_frame={'camera_id':'SOFTWARE-camera'},boot_id='native-boot',clock_domain='native-source')
    def command(self,ident,binding,operation,body):
        if ident!=self.id or binding!=self.binding or self.new_owner:raise ValueError('Wrong old grant or newer owner')
        if operation!='stop':self.check(ident,binding,body.get('expected_control_epoch',body.get('control_epoch')))
        status=self.station.command(operation,body)
        if operation=='stop':self.revoked=True
        return dict(status=status,grant={},native_command_called=True)
    def revoke(self,reason,expected_grant_id=None,expected_control_epoch=None,expected_binding=None):
        if expected_grant_id==self.id:self.revoked=True


class AutoInbox(evaluation.SourceOutcomeInbox):
    def __init__(self,*args,outcome='success',**kwargs):super().__init__(*args,**kwargs);self.outcome=outcome
    def request(self,*args,**kwargs):
        row=super().request(*args,**kwargs)
        if self.outcome=='success':self.confirm(row['challenge_id'],{'affirmed':True,'observing':True},authenticated_actor='SOFTWARE-operator')
        else:self.record_status(row['challenge_id'],self.outcome,authenticated_actor='SOFTWARE-operator',observing=True)
        return row


def prepared(tmp_path,backend='act',*,dispatch=True,outcome='success',early_grant=True):
    store,model,admission,outer,view=fixture(tmp_path,backend);clock=Clock()
    settings=evaluation.request_settings(store,model['id'],dict(duration_s=.9,review_timeout_s=.5,object_instance_id='software-object',scene_id='software-scene',goal_instance_id='software-bin'),admission_verifier=admission)
    calibration=outer['source_calibration_metadata']['calibration_metadata']['follower']['calibration_sha256']
    station=Station(clock,calibration,dispatch=dispatch);permission=Permission(station,settings['binding'])
    if early_grant:settings.update(grant_id=permission.id,expected_control_epoch=station.epoch)
    job=store.create_job('so101_skill_eval','04',settings);store.update_job(job['id'],status='running')
    inbox=AutoInbox(store.root/'jobs'/job['id']/'outcomes',clock=clock,clock_domain='SOFTWARE-host',outcome=outcome)
    class Policy:
        def predict_history(self,images,states,**kwargs):return np.tile(np.asarray([0.,0.,0.,0.,0.,50.]),(1 if backend=='act' else 4,1))
    def phase():
        sent=sum(op=='action' for op,body in station.commands);name=settings['source_phases'][min(sent,len(settings['source_phases'])-1)]
        return dict(phase_id=name,reference=settings['phase_reference'][name])
    return store,store.job(job['id']),admission,station,permission,inbox,clock,phase,Policy,model


def run(parts,**kwargs):
    store,job,admission,station,permission,inbox,clock,phase,Policy,model=parts
    return evaluation.run_so101_skill_eval(store,job,station=station,permission=permission,phase_provider=phase,inbox=inbox,
        loader=lambda *args:Policy(),admission_verifier=admission,clock=clock,sleep=clock.sleep,**kwargs)


def test_request_construction_emits_zero_commands_and_has_no_permission_boolean_bypass(tmp_path):
    parts=prepared(tmp_path);store,job,admission,station,permission,*_=parts
    assert station.commands==[] and job['config']['hardware_permission_is_not_granted_by_this_request']
    with pytest.raises(evaluation.EvaluationBlocked,match='BOUND_COMMON'):
        evaluation.run_so101_skill_eval(store,job)
    assert station.commands==[]


@pytest.mark.parametrize('backend',['act','diffusion'])
def test_actual_software_loop_retains_distinct_prediction_accept_dispatch_and_independent_outcomes(tmp_path,backend):
    parts=prepared(tmp_path,backend);store,job,admission,station,permission,inbox,clock,phase,Policy,model=parts
    result=run(parts)
    assert not result['independent_full_task_success'] and result['source_domain']=='SIMULATION'
    assert result['execution_kind']=='software_injected_predictor_diagnostic' and not result['qualified_saved_model_runtime_executed']
    assert not result['physical_io'] and not result['physical_success_claimed']
    assert result['native_stop_confirmed'] and station.mode=='idle' and permission.revoked
    assert sum(op=='action' for op,body in station.commands)==result['steps']
    folder=store.root/'jobs'/job['id']
    assert len(list((folder/'accepted').glob('*.json')))==result['steps']
    assert len(list((folder/'steps').glob('*.json')))==result['steps']
    assert json.loads((folder/'accepted/000000.json').read_bytes())['level']=='accepted_not_dispatch_evidence'
    store.update_job(job['id'],status='completed',result=result)
    selected=transfer_source.inspect_source_model(store,model['id'],admission_verifier=admission,owner_capabilities={'implemented':True,'software_fixture_only':True})
    assert not selected['source_object_rollout_qualified'] and not selected['qualified_trace_choices']
    assert result['source_trace_manifest'] is None
    assert not result['target_physical_execution_authorized'] and not result['target_model_trained']


def test_HTTP_accept_without_native_bus_write_never_becomes_qualified(tmp_path):
    parts=prepared(tmp_path,dispatch=False);result=run(parts)
    assert result['steps']==0 and not result['independent_full_task_success']
    assert 'bus-write' in result['error']['reason'] and result['source_trace_manifest'] is None
    assert parts[3].mode=='idle'
    assert list((parts[0].root/'jobs'/parts[1]['id']/'accepted').glob('*.json'))


def test_explicit_failure_review_retained_separately_and_no_qualified_bundle(tmp_path):
    parts=prepared(tmp_path,outcome='failure');result=run(parts)
    assert not result['independent_full_task_success'] and result['source_trace_manifest'] is None
    assert {value['status'] for value in result['per_phase'].values()}=={'failure'}
    assert result['end_to_end']['status']=='failure'


def test_inference_revocation_prevents_action_and_scoped_stop_does_not_touch_new_owner(tmp_path):
    parts=prepared(tmp_path);store,job,admission,station,permission,inbox,clock,phase,Policy,model=parts
    class Revoking(Policy):
        def predict_history(self,*args,**kwargs):
            permission.revoked=True;permission.new_owner=True;station.epoch='NEW-USER';station.mode='teleoperation'
            return super().predict_history(*args,**kwargs)
    result=evaluation.run_so101_skill_eval(store,job,station=station,permission=permission,phase_provider=phase,inbox=inbox,
        loader=lambda *args:Revoking(),admission_verifier=admission,clock=clock,sleep=clock.sleep)
    assert not any(op in ('action','stop') for op,body in station.commands)
    assert station.epoch=='NEW-USER' and station.mode=='teleoperation'
    assert not result['independent_full_task_success'] and result['error'] is not None


def test_outcome_inbox_actor_spoof_expiry_cancel_and_single_consumption(tmp_path):
    clock=Clock();inbox=evaluation.SourceOutcomeInbox(tmp_path/'evidence',clock=clock,clock_domain='test')
    row=inbox.request('run-one','object-one','grasp','Observed grasp?','command-one',timeout_s=1)
    with pytest.raises(ValueError,match='only affirmative'):
        inbox.confirm(row['challenge_id'],{'affirmed':True,'observing':True,'actor':'forged'},authenticated_actor='real')
    inbox.confirm(row['challenge_id'],{'affirmed':True,'observing':True},authenticated_actor='real')
    assert inbox.consume(row['challenge_id'],run_id='run-one',phase_id='grasp',after_command_id='command-one')['authenticated_actor']=='real'
    with pytest.raises(evaluation.EvaluationBlocked):inbox.consume(row['challenge_id'],run_id='run-one',phase_id='grasp',after_command_id='command-one')
    row=inbox.request('run-two','object-one','release','Observed release?','command-two',timeout_s=1)
    clock.sleep(1.1)
    with pytest.raises(ValueError):inbox.confirm(row['challenge_id'],{'affirmed':True,'observing':True},authenticated_actor='real')
    row=inbox.request('run-three','object-one','release','Observed release?','command-three',timeout_s=1);inbox.cancel('run-three')
    with pytest.raises(ValueError):inbox.confirm(row['challenge_id'],{'affirmed':True,'observing':True},authenticated_actor='real')


def test_revoke_during_model_load_retains_negative_attempt_without_native_start(tmp_path):
    parts=prepared(tmp_path);store,job,admission,station,permission,inbox,clock,phase,Policy,model=parts
    def loading(*args):permission.revoked=True;return Policy()
    result=evaluation.run_so101_skill_eval(store,job,station=station,permission=permission,phase_provider=phase,inbox=inbox,
        loader=loading,admission_verifier=admission,clock=clock,sleep=clock.sleep)
    assert station.commands==[] and result['steps']==0
    assert not result['independent_full_task_success'] and result['error'] is not None
    assert (store.root/'jobs'/job['id']/'result.json').is_file()


@pytest.mark.parametrize('changed',['calibration','camera','boot','clock'])
def test_connected_grant_source_mismatch_is_rejected_before_any_native_start(tmp_path,changed):
    parts=prepared(tmp_path);permission=parts[4];original=permission.check
    def different_source(*args):
        grant=original(*args)
        if changed=='calibration':grant['follower_calibration_sha256']='f'*64
        elif changed=='camera':grant['displayed_frame']={'camera_id':'OTHER-camera'}
        elif changed=='boot':grant['boot_id']='OTHER-boot'
        else:grant['clock_domain']='OTHER-clock'
        return grant
    permission.check=different_source
    result=run(parts)
    assert parts[3].commands==[] and result['steps']==0
    assert 'before native start' in result['error']['reason']
    assert not result['independent_full_task_success']


def test_cancel_during_inference_blocks_subsequent_action_before_native_boundary(tmp_path):
    parts=prepared(tmp_path);store,job,admission,station,permission,inbox,clock,phase,Policy,model=parts
    class Cancelling(Policy):
        def predict_history(self,*args,**kwargs):
            store.update_job(job['id'],status='cancel_requested')
            return super().predict_history(*args,**kwargs)
    result=evaluation.run_so101_skill_eval(store,job,station=station,permission=permission,phase_provider=phase,inbox=inbox,
        loader=lambda *args:Cancelling(),admission_verifier=admission,clock=clock,sleep=clock.sleep)
    assert not any(operation=='action' for operation,body in station.commands)
    assert station.mode=='idle' and result['steps']==0 and not result['independent_full_task_success']


def test_source_evaluation_module_import_remains_torch_and_robot_free():
    import os,subprocess,sys
    code="import sys;import explorer.so101_source_evaluation;assert 'torch' not in sys.modules;assert not any(n.startswith('lerobot.robots') for n in sys.modules)"
    root=Path(__file__).resolve().parents[1]
    done=subprocess.run([sys.executable,'-c',code],capture_output=True,text=True,timeout=15,
        env=dict(os.environ,PYTHONPATH=str(root/'00-control-center/src')+':'+str(root/'shared')))
    assert done.returncode==0,done.stderr


def test_phase_annotation_is_revision_fenced_metadata_without_actuator_side_effect(tmp_path):
    parts=prepared(tmp_path);store,job,admission,station,permission,inbox,clock,phase,Policy,model=parts
    initial=evaluation.current_phase(store,job['id']);assert initial['revision']=='initial'
    store.update_job(job['id'],result=dict(phase='observed_native_source_policy',control_epoch='owned',source_owner_session_id=inbox.session_id,
        current_source_phase=initial['phase_id'],last_native_command_id='native-command'))
    next_phase=job['config']['source_phases'][1]
    advanced=evaluation.record_phase(store,job['id'],dict(phase_id=next_phase,expected_revision='initial',expected_control_epoch='owned'),authenticated_actor='SOFTWARE-user')
    assert advanced['metadata_only'] and evaluation.current_phase(store,job['id'])['phase_id']==next_phase
    assert station.commands==[]
    with pytest.raises(evaluation.EvaluationBlocked,match='revision'):
        evaluation.record_phase(store,job['id'],dict(phase_id=next_phase,expected_revision='initial',expected_control_epoch='owned'),authenticated_actor='SOFTWARE-user')
    with pytest.raises(evaluation.EvaluationBlocked,match='no recorded'):
        evaluation.record_phase(store,job['id'],dict(phase_id=job['config']['source_phases'][2],expected_revision=advanced['revision'],expected_control_epoch='owned'),authenticated_actor='SOFTWARE-user')
    store.update_job(job['id'],status='cancel_requested')
    with pytest.raises(evaluation.EvaluationBlocked,match='current running'):
        evaluation.record_phase(store,job['id'],dict(phase_id=next_phase,expected_revision=advanced['revision'],expected_control_epoch='owned'),authenticated_actor='SOFTWARE-user')
    assert station.commands==[]


def test_model_preparation_before_current_permission_attach_emits_zero_native_calls(tmp_path):
    parts=prepared(tmp_path,early_grant=False);store,job,admission,station,permission,inbox,clock,phase,Policy,model=parts
    loaded=[];attached=[]
    def loading(*args):
        assert station.commands==[];loaded.append(True);clock.sleep(3)
        return Policy()
    def grant():
        assert loaded and station.commands==[]
        state=store.job(job['id'])['result']
        assert state['phase']=='awaiting_source_observed_grant' and state['model_ready']
        evaluation.attach_permission(store,job['id'],permission.id,station.epoch,inbox.session_id,permission=permission)
        attached.append(True)
        return evaluation.current_permission(store,job['id'],inbox.session_id)
    result=evaluation.run_so101_skill_eval(store,job,station=station,permission=permission,phase_provider=phase,inbox=inbox,
        loader=loading,grant_provider=grant,admission_verifier=admission,clock=clock,sleep=clock.sleep)
    assert attached and result['steps']>0 and result['error'] is None
    assert result['execution_kind']=='software_injected_predictor_diagnostic' and not result['independent_full_task_success']


def test_no_attached_grant_after_loading_never_starts_or_revokes_some_other_user(tmp_path):
    parts=prepared(tmp_path,early_grant=False);store,job,admission,station,permission,inbox,clock,phase,Policy,model=parts
    result=evaluation.run_so101_skill_eval(store,job,station=station,permission=permission,phase_provider=phase,inbox=inbox,
        loader=lambda *args:Policy(),grant_provider=lambda:None,admission_verifier=admission,clock=clock,sleep=clock.sleep)
    assert station.commands==[] and permission.revoked is False
    assert result['steps']==0 and not result['independent_full_task_success']
    assert 'attached' in result['error']['reason']


def test_real_COMMON_provider_expiry_after_load_blocks_any_original_station_command(tmp_path):
    """Actual permission implementation, fake Station/camera; no real authorization."""
    import threading
    from types import SimpleNamespace
    from explorer_shared.station_observed_activation import StationObservedPermission
    from explorer_shared.learning.act_policy import NativeACTPolicy
    store,model,admission,outer,view=fixture(tmp_path,source_kind='SO101_RECORDED_DEMONSTRATIONS');clock=Clock()
    settings=evaluation.request_settings(store,model['id'],dict(duration_s=.5,review_timeout_s=.5,object_instance_id='software-object',scene_id='software-scene',goal_instance_id='software-bin'),admission_verifier=admission)
    calibration=outer['source_calibration_metadata']['calibration_metadata']['follower']['calibration_sha256']
    class Native(Station):
        execution_domain='PHYSICAL';boot='native-boot';clock_domain='native-source'
        def __init__(self):super().__init__(clock,calibration);self.lock=threading.RLock()
        @property
        def control_epoch(self):return self.epoch
        def status(self):
            value=super().status();value['roles']={'follower':dict(verified_on_connected_hardware=True,calibration_sha256=calibration)}
            return value
    station=Native()
    class Camera:
        camera_id='SOFTWARE-camera';stale_s=2.
        def __init__(self):
            self.current=SimpleNamespace(frame_id=uuid.uuid4().hex,jpeg=b'\xff\xd8SOFTWARE_PERMISSION_FIXTURE\xff\xd9',monotonic_ns=clock(),timestamp_basis='software_fixture_not_exposure')
        def latest(self):return self.current
        def frame_by_id(self,ident):return self.current if self.current.frame_id==ident else None
    permission=StationObservedPermission(station,Camera(),clock=clock)
    challenge=permission.challenge(settings['binding'])
    grant=permission.authorize(dict(binding=settings['binding'],challenge_id=challenge['challenge_id'],displayed_jpeg_sha256=challenge['frame']['jpeg_sha256'],
        expected_control_epoch=station.epoch,observing=True,robot_workspace_checked=True,user_observed_test_confirmed=True,actor_id='SOFTWARE_TEST_OPERATOR'))
    assert station.commands==[]
    settings.update(grant_id=grant['grant_id'],expected_control_epoch=station.epoch)
    job=store.create_job('so101_skill_eval','04',settings);store.update_job(job['id'],status='running');job=store.job(job['id'])
    inbox=AutoInbox(store.root/'jobs'/job['id']/'outcomes',clock=clock,clock_domain='SOFTWARE-host')
    def nonexecuting_typed_sentinel(*args):
        # No constructor/weights/inference. The typed object is used only so the
        # REAL native grant expiry is the next guard; predict is never reached.
        sentinel=object.__new__(NativeACTPolicy);sentinel.checkpoint_sha256=model['result']['checkpoint_sha256']
        clock.sleep(2.1)
        return sentinel
    result=evaluation.run_so101_skill_eval(store,job,station=station,permission=permission,phase_provider=lambda:{},inbox=inbox,
        loader=nonexecuting_typed_sentinel,admission_verifier=admission,clock=clock,sleep=clock.sleep)
    assert station.commands==[] and result['steps']==0 and not result['independent_full_task_success']
    assert not permission.status()['execution_permission']


def test_qualified_bundle_serializer_roundtrip_is_structural_fixture_not_a_real_policy_trial(tmp_path):
    """Exercise producer/consumer schema with explicit trusted-branch fixture data.

    The actual mocked loop above remains diagnostic/unqualified. This direct
    PRIVATE serializer fixture is never an admitted model or public run receipt.
    """
    import shutil
    parts=prepared(tmp_path);store,original,admission,station,permission,inbox,clock,phase,Policy,model_job=parts
    diagnostic=run(parts)
    assert diagnostic['execution_kind']=='software_injected_predictor_diagnostic'
    source_folder=store.root/'jobs'/original['id']
    rows=[json.loads(path.read_bytes()) for path in sorted((source_folder/'steps').glob('*.json'))]
    copied=store.create_job('so101_skill_eval','04',original['config']);store.update_job(copied['id'],status='running');copied=store.job(copied['id'])
    folder=store.root/'jobs'/copied['id'];shutil.copytree(source_folder,folder)
    for name in ('result.json','evaluation-receipt.json','outcome-challenges.json'):(folder/name).unlink()
    shutil.rmtree(folder/'outcomes')
    proof=json.loads((folder/'implementation-snapshot/manifest.json').read_bytes())
    archive=dict(manifest_path=str(folder/'implementation-snapshot/manifest.json'),manifest_sha256=sha((folder/'implementation-snapshot/manifest.json').read_bytes()))
    metadata=transfer_source._metadata(store,model_job,admission_verifier=admission)
    result=evaluation._finish(store,copied,copied['config'],metadata,folder,rows,None,json.loads((folder/'stop-receipt.json').read_bytes()),
        AutoInbox(folder/'outcomes',clock=clock,clock_domain='SOFTWARE-serializer-fixture'),clock,clock.sleep,admission_verifier=admission,
        runtime_qualified=True,implementation_snapshot=archive)
    assert result['source_trace_manifest'] and result['source_domain']=='SIMULATION' and not result['physical_io']
    store.update_job(copied['id'],status='completed',result=result)
    chosen=transfer_source.inspect_source_model(store,model_job['id'],admission_verifier=admission,owner_capabilities={'implemented':True})
    assert any(row['evaluation_job_id']==copied['id'] for row in chosen['qualified_trace_choices'])
    assert station.mode=='idle'  # serializer added no control operation
