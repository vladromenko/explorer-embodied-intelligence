"""Real durable inbox and fake original Source; no devices or control HTTP."""
from copy import deepcopy
import json
import threading
import time

import pytest

from explorer_shared import handover_adapters as module
from explorer_shared.contracts import digest
from explorer_shared.handover_adapters import OperatorEvidenceInbox


ACTOR='SOFTWARE-TEST-OPERATOR'
DOMAIN='nx:test-boot'
STATEMENTS=('receiver_holds_object','giver_stationary','receiver_stationary')


def source():
    return {'native_command_called':False,'execution_authority':False,'challenge':{
        'schema':'explorer_native_parked_receiver_hold_challenge_v1','challenge_id':'source-challenge',
        'release_request_id':'release-request','park_request_id':'park-request','run_id':'run',
        'object_id':'object','actor_id':ACTOR,'after_receiver_command_id':'actual-receiver-command',
        'issued_native_ns':999_000_000_000,'expires_native_ns':1_001_000_000_000,
        'native_clock_domain':'nano:other-boot','source_context':{'label':'только исходный контекст'}}}


def setup(tmp_path):
    tick=[1_000_000_000]
    inbox=OperatorEvidenceInbox(tmp_path,clock=lambda:tick[0],clock_domain=DOMAIN)
    kwargs=dict(run_id='run',object_id='object',after_giver_command_id='actual-giver-command',
        source_response=source(),authenticated_actor=ACTOR,request_started_ns=800_000_000,response_received_ns=900_000_000)
    return inbox,tick,kwargs


def affirmative():
    return {'observing':True,'affirmations':{key:True for key in STATEMENTS}}


def test_atomic_three_fact_consumption_is_post_actual_source_and_bound_to_literal_context(tmp_path):
    inbox,tick,kwargs=setup(tmp_path);literal=deepcopy(kwargs['source_response'])
    offered=inbox.prepare_release_bundle(**kwargs);identity=offered['bundle_id']
    assert offered['deadline_ns']==2_800_000_000  # NX start + original duration, never source absolute clock.
    assert offered['source_challenge_sha256']==digest(literal['challenge'])
    assert offered['created_ns']>=kwargs['response_received_ns']
    assert len(inbox.pending('run'))==1 and inbox.pending('foreign')==[]
    assert 'expected_actor' not in offered and 'proofs' not in offered
    kwargs['source_response']['challenge']['park_request_id']='caller-mutated'
    tick[0]+=20
    result=inbox.confirm_release_bundle(identity,affirmative(),authenticated_actor=ACTOR)
    assert result['state']=='consumed' and inbox.pending()==[]
    retained=inbox.wait_release_bundle(identity)
    assert retained['source_response']==literal
    proofs=retained['proofs'];assert set(proofs)==set(STATEMENTS)
    assert len({p['challenge_id'] for p in proofs.values()})==3
    for statement,proof in proofs.items():
        assert proof['state']=='consumed' and proof['statement']==statement and proof['authenticated_actor']==ACTOR
        assert proof['observing'] is True and proof['affirmed'] is True
        assert 900_000_000<=proof['created_ns']<=proof['received_ns']==proof['consumed_ns']==tick[0]
        assert proof['after_command_id']==('actual-giver-command' if statement=='giver_stationary' else 'actual-receiver-command')
    stored=json.loads(inbox._bundle_path(identity).read_text());assert stored['state']=='claimed'
    with pytest.raises(ValueError,match='claimed'):inbox.wait_release_bundle(identity)
    with pytest.raises(ValueError):inbox.confirm_release_bundle(identity,affirmative(),authenticated_actor=ACTOR)


@pytest.mark.parametrize('change',['old','future','bool_time','same_clock','wrong_run','wrong_object','wrong_actor',
    'source_control','native_expired','ttl_extended','missing_command','nan'])
def test_changed_reply_or_noncausal_clock_cannot_create_fresh_questions(tmp_path,change):
    inbox,tick,kwargs=setup(tmp_path);reply=kwargs['source_response'];challenge=reply['challenge']
    if change=='old':kwargs['request_started_ns']=0;tick[0]=2_000_000_001
    elif change=='future':kwargs['response_received_ns']=tick[0]+1
    elif change=='bool_time':kwargs['request_started_ns']=True
    elif change=='same_clock':challenge['native_clock_domain']=DOMAIN
    elif change=='wrong_run':challenge['run_id']='foreign'
    elif change=='wrong_object':challenge['object_id']='foreign'
    elif change=='wrong_actor':challenge['actor_id']='foreign'
    elif change=='source_control':reply['native_command_called']=True
    elif change=='native_expired':challenge['expires_native_ns']=challenge['issued_native_ns']
    elif change=='ttl_extended':challenge['expires_native_ns']+=1
    elif change=='missing_command':challenge.pop('after_receiver_command_id')
    else:challenge['source_context']['invalid']=float('nan')
    with pytest.raises((ValueError,TimeoutError)):inbox.prepare_release_bundle(**kwargs)
    assert inbox.pending()==[] and list((tmp_path/'release-bundles').glob('*.json'))==[]


@pytest.mark.parametrize('change',['partial','numeric_true','not_observing','actor_body','proof_body','source_body','foreign_actor','expired'])
def test_old_checkbox_payload_or_injected_proof_never_becomes_post_source_consumed_evidence(tmp_path,change):
    inbox,tick,kwargs=setup(tmp_path);offered=inbox.prepare_release_bundle(**kwargs);body=affirmative();actor=ACTOR
    if change=='partial':body['affirmations'].pop('giver_stationary')
    elif change=='numeric_true':body['affirmations']['giver_stationary']=1
    elif change=='not_observing':body['observing']=False
    elif change=='actor_body':body['actor_id']=ACTOR
    elif change=='proof_body':body['proofs']={statement:{} for statement in STATEMENTS}
    elif change=='source_body':body['source_challenge']=source()['challenge']
    elif change=='foreign_actor':actor='different-authenticated-owner'
    else:tick[0]=offered['deadline_ns']
    with pytest.raises(ValueError):inbox.confirm_release_bundle(offered['bundle_id'],body,authenticated_actor=actor)
    row=inbox._bundle(offered['bundle_id']);assert row['state']=='pending' and 'proofs' not in row


def test_individual_confirmation_endpoint_cannot_partially_consume_bundle_questions(tmp_path):
    inbox,tick,kwargs=setup(tmp_path);offered=inbox.prepare_release_bundle(**kwargs)
    for identity in [offered['bundle_id']]+[row['challenge_id'] for row in offered['questions'].values()]:
        with pytest.raises(ValueError,match='Unknown challenge'):
            inbox.confirm(identity,{'affirmed':True,'observing':True},authenticated_actor=ACTOR)
    assert inbox._bundle(offered['bundle_id'])['state']=='pending'
    with pytest.raises(ValueError,match='already exists'):inbox.prepare_release_bundle(**kwargs)


def test_cancel_and_new_process_do_not_restore_release_or_allow_a_second_waiter(tmp_path):
    inbox,tick,kwargs=setup(tmp_path);offered=inbox.prepare_release_bundle(**kwargs);identity=offered['bundle_id']
    restarted=OperatorEvidenceInbox(tmp_path,clock=lambda:tick[0],clock_domain=DOMAIN)
    assert restarted.pending()==[]
    with pytest.raises(ValueError):restarted.confirm_release_bundle(identity,affirmative(),authenticated_actor=ACTOR)
    with pytest.raises(RuntimeError):restarted.wait_release_bundle(identity)
    inbox.cancel_run('run')
    assert inbox.pending()==[] and inbox._bundle(identity)['state']=='cancelled'
    with pytest.raises(RuntimeError):inbox.wait_release_bundle(identity)
    with pytest.raises(ValueError):inbox.confirm_release_bundle(identity,affirmative(),authenticated_actor=ACTOR)
    with pytest.raises(RuntimeError):inbox.prepare_release_bundle(**kwargs)


def test_wait_is_woken_by_real_cancel_without_synthesizing_any_proof(tmp_path):
    inbox=OperatorEvidenceInbox(tmp_path,clock_domain=DOMAIN)
    started=time.monotonic_ns();offered=inbox.prepare_release_bundle(run_id='run',object_id='object',
        after_giver_command_id=None,source_response=source(),authenticated_actor=ACTOR,
        request_started_ns=started,response_received_ns=time.monotonic_ns())
    errors=[]
    def waiter():
        try:inbox.wait_release_bundle(offered['bundle_id'])
        except Exception as error:errors.append(error)
    thread=threading.Thread(target=waiter);thread.start();inbox.cancel_run('run');thread.join(1)
    assert not thread.is_alive() and len(errors)==1 and isinstance(errors[0],RuntimeError)
    assert 'proofs' not in inbox._bundle(offered['bundle_id'])


@pytest.mark.parametrize('stage',['prepare','consume','claim'])
def test_expiry_during_durable_write_never_returns_authority(tmp_path,monkeypatch,stage):
    inbox,tick,kwargs=setup(tmp_path);write=module._write
    offered=None
    if stage!='prepare':offered=inbox.prepare_release_bundle(**kwargs)
    if stage=='claim':inbox.confirm_release_bundle(offered['bundle_id'],affirmative(),authenticated_actor=ACTOR)
    def slow(path,value):
        write(path,value);tick[0]=2_800_000_000
    monkeypatch.setattr(module,'_write',slow)
    with pytest.raises(TimeoutError):
        if stage=='prepare':inbox.prepare_release_bundle(**kwargs)
        elif stage=='consume':inbox.confirm_release_bundle(offered['bundle_id'],affirmative(),authenticated_actor=ACTOR)
        else:inbox.wait_release_bundle(offered['bundle_id'])
    assert inbox.pending()==[]


def test_durable_consumption_error_retains_actual_file_but_cannot_be_reconfirmed(tmp_path,monkeypatch):
    inbox,tick,kwargs=setup(tmp_path);offered=inbox.prepare_release_bundle(**kwargs);write=module._write
    def failed(path,value):
        write(path,value)
        if value.get('state')=='consumed':raise OSError('actual final directory fsync failure fixture')
    monkeypatch.setattr(module,'_write',failed)
    with pytest.raises(OSError):inbox.confirm_release_bundle(offered['bundle_id'],affirmative(),authenticated_actor=ACTOR)
    assert inbox._bundle(offered['bundle_id'])['state']=='consumed'
    with pytest.raises(ValueError):inbox.confirm_release_bundle(offered['bundle_id'],affirmative(),authenticated_actor=ACTOR)
    with pytest.raises(ValueError,match='not successfully published'):inbox.wait_release_bundle(offered['bundle_id'])
    inbox.cancel_run('run');assert inbox._bundle(offered['bundle_id'])['state']=='cancelled'


def test_even_trusted_reattach_id_does_not_restore_ram_publication_after_process_restart(tmp_path):
    inbox,tick,kwargs=setup(tmp_path);offered=inbox.prepare_release_bundle(**kwargs)
    inbox.confirm_release_bundle(offered['bundle_id'],affirmative(),authenticated_actor=ACTOR)
    restarted=OperatorEvidenceInbox(tmp_path,clock=lambda:tick[0],clock_domain=DOMAIN,session_id=inbox.session_id)
    with pytest.raises(ValueError,match='not successfully published'):restarted.wait_release_bundle(offered['bundle_id'])


def test_failed_claim_cannot_be_retried_after_an_actual_file_write(tmp_path,monkeypatch):
    inbox,tick,kwargs=setup(tmp_path);offered=inbox.prepare_release_bundle(**kwargs)
    inbox.confirm_release_bundle(offered['bundle_id'],affirmative(),authenticated_actor=ACTOR)
    write=module._write
    def failed(path,value):
        write(path,value)
        if value.get('state')=='claimed':raise OSError('actual claim directory fsync failure fixture')
    monkeypatch.setattr(module,'_write',failed)
    with pytest.raises(OSError):inbox.wait_release_bundle(offered['bundle_id'])
    with pytest.raises(ValueError,match='claimed'):inbox.wait_release_bundle(offered['bundle_id'])


@pytest.mark.parametrize('mode',['cooperative_policy','keyboard','browser_gamepad'])
def test_actual_fake_source_challenge_accepts_only_new_atomic_three_fact_bundle_and_keeps_native_deadlines(tmp_path,mode):
    from test_giver_parked_release05 import rig
    (tmp_path/'source').mkdir()
    values,base,helper,prepare=rig(tmp_path/'source',mode=mode)
    owner,mux,teleop,arm,publisher,tap,activation,native_tick,binding,path=values
    inbox=OperatorEvidenceInbox(tmp_path/'inbox',clock_domain=DOMAIN)
    request_started=time.monotonic_ns();reply,received=prepare();before=len(publisher.messages)
    offered=inbox.prepare_release_bundle(run_id=binding['run_id'],object_id=binding['object_id'],after_giver_command_id=None,
        source_response=reply,authenticated_actor=ACTOR,request_started_ns=request_started,response_received_ns=received)
    literal=deepcopy(reply['challenge']);inbox.confirm_release_bundle(offered['bundle_id'],affirmative(),authenticated_actor=ACTOR)
    claimed=inbox.wait_release_bundle(offered['bundle_id'])
    assert len(publisher.messages)==before and owner.guard.parked
    result=helper.resume_release(base(release_request_id=literal['release_request_id'],challenge_id=literal['challenge_id'],
        operator_confirmations=claimed['proofs'],coordinator_clock_domain=DOMAIN,
        challenge_received_coordinator_ns=received,proofs_checked_coordinator_ns=time.monotonic_ns(),
        coordinator_verified_confirmation_created_after_challenge=True),authenticated_actor=ACTOR)
    assert result['release_grant']['challenge']==literal
    assert result['release_grant']['expires_native_ns']==literal['expires_native_ns']
    assert teleop.lease==native_tick[0]+.45 and len(publisher.messages)==before
    assert result['actual_arm_opening_command_sent'] is False
