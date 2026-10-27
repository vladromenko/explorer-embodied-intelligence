"""Literal installed grant, real source API, mocked original Publisher only."""
from copy import deepcopy
import time

import pytest

from explorer_shared.contracts import digest
from explorer_shared.cooperative_contract05 import ROLE_CONTRACTS
from explorer_shared.parked_release_transport05 import InstalledGrantGiverPolicyTransport,InstalledGrantGiverManualTransport
from test_giver_parked_release05 import rig,actual_confirmations,request as resume_body,DOMAIN
from test_native_giver_phase_http import client_for,TOKEN


def ready(tmp_path,*,manual=False,emit=True):
    values,base,helper,prepare=rig(tmp_path,mode='keyboard' if manual else 'cooperative_policy')
    owner,mux,teleop,arm,publisher,tap,activation,tick,binding,path=values
    reply,received=prepare();proofs=actual_confirmations(tmp_path/'facts',owner.guard.binding)
    resumed=helper.resume_release(resume_body(base,reply,received,proofs),authenticated_actor=next(iter(proofs.values()))['authenticated_actor'])
    grant=resumed['release_grant'];challenge=grant['challenge'];calls=[];pending=[];client=client_for(owner,mux,activation)
    descriptor={'challenge':challenge,'source_challenge_sha256':digest(challenge),'received_coordinator_ns':received,
        'received_clock_domain':DOMAIN,'deadline_nx_ns':received+2_000_000_000,'actor_id':proofs['receiver_holds_object']['authenticated_actor']}
    def request(method,path,body):
        calls.append((method,path,deepcopy(body)))
        if emit and method=='GET' and '/dispatch?' in path and pending:
            incoming=pending.pop(0);record={'command_generation':mux.generation,'input_received_at':arm.stream_intent['received_at']}
            arm._publish_command([round(value) for value in incoming['action9'][:6]],80,record)
        response=client.request(method,path.replace('/legacy/','/api/',1),headers={'Authorization':'Bearer '+TOKEN},json=body if body is not None else None)
        if response.status_code!=200:raise ValueError(response.text)
        if method=='POST' and path.endswith('/input'):pending.append(deepcopy(body))
        return response.json()
    model=deepcopy(owner.policy)
    def predict(command):
        return {'action_contract':ROLE_CONTRACTS['giver'],'model':model,'action9':[90.]*5+[80.]+[0.,0.,0.],
            'source_sequence':mux.sequence+1,'source_observation':request('GET','/legacy/explorer/cooperative/observation',None)}
    arguments=dict(clock_domain=DOMAIN,installed_grant=grant,release_descriptor=descriptor)
    transport=(InstalledGrantGiverManualTransport(request,owner.guard.binding,teleop.generation,**arguments) if manual else
        InstalledGrantGiverPolicyTransport(request,owner.guard.binding,mux.epoch,predict,action_constraint=owner.action_constraint['constraint'],**arguments))
    authorization={'preinstalled_parked_release_grant':deepcopy(grant),'native_release_challenge':deepcopy(descriptor),
        'operator_confirmation_receipt':{'confirmation':deepcopy(proofs['receiver_holds_object'])}}
    return values,transport,authorization,calls,client,grant


@pytest.mark.parametrize('manual',[False,True])
def test_constructor_inert_fresh_installed_grant_validation_gets_only_no_second_authorize_or_lease(tmp_path,manual):
    values,transport,authorization,calls,client,grant=ready(tmp_path,manual=manual);owner,mux,teleop,arm,publisher,tap,activation,tick,binding,path=values
    try:
        assert calls==[];lease=teleop.lease;before=len(publisher.messages)
        result=transport._authorize_release(authorization)
        assert result==grant and all(method=='GET' for method,_,_ in calls) and teleop.lease==lease and len(publisher.messages)==before
        assert not any(path.endswith('/authorize') or path.endswith('/lease') for _,path,_ in calls)
    finally:client.close()


def test_policy_uses_existing_latest_intent_and_actual_opening_receipt_with_alreadyinstalled_grant(tmp_path):
    values,transport,authorization,calls,client,grant=ready(tmp_path);owner,mux,teleop,arm,publisher,tap,activation,tick,binding,path=values
    command={'command_id':'software-root-release-command','run_id':binding['run_id'],'object_id':binding['object_id'],'role':'giver',
        'coordinator_epoch':binding['coordinator_epoch'],'operation':'release','reference':deepcopy(owner.policy)}
    try:
        lease=teleop.lease;result=transport.submit_observed(command,authorization)
        assert result.dispatched and result.source['basis']=='actual_original_native_host_publication'
        assert owner.guard.last_publication['opening'] and owner.guard.last_publication['guard_grant']==grant
        posts=[path for method,path,_ in calls if method=='POST'];assert posts==['/legacy/explorer/cooperative/input']
        assert teleop.lease==lease and not result.source['attainment_verified']
    finally:client.close()


@pytest.mark.parametrize('changed',['missing','different_grant','descriptor','actor','expired_native','missing_source_clock','clock','boot','owner','revision','source_grant','unknown_fault','unknown_park'])
def test_unknown_foreign_expired_or_tampered_grant_never_authorizes_or_posts(tmp_path,changed):
    values,transport,authorization,calls,client,grant=ready(tmp_path);owner,mux,teleop,arm,publisher,tap,activation,tick,binding,path=values
    original=transport.request
    if changed=='missing':authorization.pop('preinstalled_parked_release_grant')
    elif changed=='different_grant':authorization['preinstalled_parked_release_grant']['grant_id']='foreign'
    elif changed=='descriptor':authorization['native_release_challenge']['challenge']['revision']+=1
    elif changed=='actor':owner.guard.binding['observed_source_authorization']['actor_id']='foreign'
    elif changed=='expired_native':
        tick[0]+=2.1;teleop.lease=tick[0]+.45  # SOFTWARE isolate grant expiry from the independent original motor lease.
        assert transport.observe_state()['current_native_owner_ready']
    elif changed=='owner':teleop.generation+=1
    elif changed=='revision':owner.guard.revoke('EXPLICIT_USER_REVOKE')
    elif changed=='source_grant':owner.guard.grant['grant_id']='foreign-current-source-grant'
    else:
        def request(method,path,body):
            result=original(method,path,body)
            if path.endswith('/cooperative/status'):
                if changed=='missing_source_clock':result['release_boundary'].pop('source_stamp')
                elif changed=='unknown_fault':result['release_boundary'].pop('reference_fault')
                elif changed=='unknown_park':result['release_boundary'].pop('parked')
                else:result['release_boundary']['source_stamp']['clock_domain' if changed=='clock' else 'boot_id']='foreign'
            return result
        transport.request=request
    before=len(publisher.messages);lease=teleop.lease
    try:
        with pytest.raises(ValueError):transport._authorize_release(authorization)
        assert not any(method=='POST' for method,_,_ in calls) and len(publisher.messages)==before and teleop.lease==lease
    finally:client.close()
