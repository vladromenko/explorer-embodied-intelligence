"""Original native9 recorder/service/importer with fake GET tap; no robot IO."""
from copy import deepcopy
import json
import time
from pathlib import Path
import uuid

import pytest
from fastapi.testclient import TestClient

from explorer import transfer_target as target
from explorer_shared.native_capture import NativeCaptureWriter,import_native_capture
from explorer_shared.native_capture_identity import normalize_capture_context,native_context_sha256,validate_native_capture_start,validate_native_capture_finish
from explorer_shared.native_capture_service import NativeCaptureService
from explorer_shared.transfer_target_capture import validate_target_capture_context
from test_transfer_geometry_api import configured
from test_native_capture import setup,publication,outcomes


def context_fixture(tmp_path,monkeypatch):
    app,store,ident,_=configured(tmp_path,monkeypatch);client=TestClient(app.app)
    client.post('/api/login',json={'password':'fixture'});csrf={'X-Explorer-CSRF':client.cookies['explorer_csrf']}
    response=client.post('/api/transfer/calibration',headers=csrf,json=dict(model_job_id=ident,
        target_version=2,target_domain='physical',request_id=str(uuid.uuid4())))
    assert response.status_code==200,response.text
    wizard=response.json();phase=target.source_for_wizard(store,wizard['wizard_id'])[1]['manifest']['skill_spec']['stages'][0]['id']
    metadata=dict(request_id=str(uuid.uuid4()),session_id=str(uuid.uuid4()),partition='train',scene_id='DECLARED-test-scene',
        object_instance_id='DECLARED-test-object',group_id='DECLARED-test-group',variant_id='DECLARED-test-variant',phase_id=phase)
    context=target.prepare_capture_context(store,wizard['wizard_id'],metadata,actor='OWN_TEST_ACTOR')
    return store,wizard,metadata,context


def test_same_native_writer_preserves_original06_identity_and_unknown_through_seal_review_import(tmp_path,monkeypatch):
    store,wizard,metadata,context=context_fixture(tmp_path,monkeypatch)
    old,tap,camera,clock,_,initial=setup(tmp_path/'old-producer');old.close()
    writer=NativeCaptureWriter(tmp_path/'shared-native-writer',camera,clock_domain='nano',boot_id='boot',clock=clock)
    calls=[]
    class Reader:
        def status(self):calls.append('GET-status');return {'schema':'explorer_native_retarget_mux_v1'}
        def reference(self):calls.append('GET-reference');return {'measured':False}
        def dispatch_source_packet(self,cursor):
            calls.append('GET-source');page=tap.since(cursor);return dict(source_bytes=json.dumps(page).encode(),payload=page)
    service=NativeCaptureService(writer,Reader(),poll_s=.01,clock=clock)
    try:
        before=deepcopy(context);started=service.start(context);identity=started['recording_id']
        assert context==before and service.capabilities()['projects']==['02','03','06']
        assert validate_native_capture_start(dict(skill_context=context),dict(clock_domain='nano',boot_id='boot'),service.status())==identity
        expected=normalize_capture_context(context)
        assert started['source_context_sha256']==native_context_sha256(expected)
        publication(tap,camera,clock,mode='keyboard')
        deadline=time.monotonic()+3
        while writer.status()['samples_written']==0:
            assert time.monotonic()<deadline,service.status()
            service.wake.set();time.sleep(.005)
        clock.value=2500
        outcome=json.loads(outcomes(identity,phases=(metadata['phase_id'],)))
        outcome['operator_outcome']='unknown'
        outcome['outcomes']['end_to_end']['status']='unknown'
        for row in outcome['outcomes']['per_phase'].values():row['status']='unknown'
        service.finish(identity,outcome);assert writer.done.wait(3),service.status()
        manifest=writer.folder/'manifest.json';raw=manifest.read_bytes();document=json.loads(raw)
        episode=document['embodied_episode'];assert document['project_id']==episode['project_id']=='06'
        assert episode['experiment_slug']=='cross_embodiment_transfer' and episode['experiment_numbering_version']==2
        assert episode['skill']['id']==wizard['skill_id'] and episode['skill']['version']==2
        assert episode['recording']['operator_outcome']=='unknown'
        assert not document['admission']['eligible_as_successful_bc_expert'] and not document['admission']['physical_qualification']
        assert episode['samples'] and all(len(sample['action']['values'])==9 for sample in episode['samples'])
        assert all(sample['channels']['joint_feedback']['value'] is None for sample in episode['samples'])
        assert document['motor_commands']==0 and not document['physical_success_inferred']
        assert validate_native_capture_finish(dict(skill_context=context),dict(clock_domain='nano',boot_id='boot'),identity,document,outcome)==identity
        info=json.loads((writer.folder/'archive-info.json').read_bytes())
        writer.review(identity,'reject','OWN_TEST_REVIEW',info['archive_sha256'])
        row=writer.ready_recordings()[0];assert row['project_id']=='06' and not row['transfer_ready']
        imported=import_native_capture(writer.archive(identity),info['archive_sha256'],tmp_path/'import')
        imported_raw=Path(imported['embodied_manifest']).read_bytes()
        assert json.loads(imported_raw)['project_id']=='06' and manifest.read_bytes()==raw
        assert all(call.startswith('GET-') for call in calls)
    finally:service.close()


@pytest.mark.parametrize('field,value',[
    ('automatic_motion',True),('native_payload_additions',{'action9':[1]*9}),
    ('target_domain','simulation'),('skill_version',1),('source_model_binding',[]),('split',[])])
def test_foreign_or_action_bearing06_context_is_rejected_before_even_source_GET(tmp_path,monkeypatch,field,value):
    store,wizard,metadata,context=context_fixture(tmp_path,monkeypatch)
    context[field]=value
    class ForbiddenReader:
        def status(self):pytest.fail('Invalid DATA context must fail before source access')
    service=NativeCaptureService(None,ForbiddenReader(),poll_s=1)
    try:
        with pytest.raises(ValueError):service.start(context)
    finally:
        # No writer/owner was constructed for this preflight-only service.
        service.closed.set();service.wake.set();service.thread.join(2)


def test_mutated_source_spec_or_reduced_phase_contract_cannot_be_sealed_as_target_metadata(tmp_path,monkeypatch):
    store,wizard,metadata,context=context_fixture(tmp_path,monkeypatch)
    from explorer_shared.contracts import digest
    bad=deepcopy(context);bad['skill_spec']['source_spec_snapshot']['title']='foreign source'
    bad['skill_spec_sha256']=bad['spec_sha256']=digest(bad['skill_spec'])
    with pytest.raises(ValueError,match='source SkillSpec snapshot'):validate_target_capture_context(bad)
    bad=deepcopy(context);bad['skill_spec']['stages']=bad['skill_spec']['stages'][:1]
    bad['skill_spec_sha256']=bad['spec_sha256']=digest(bad['skill_spec'])
    with pytest.raises(ValueError,match='complete phase contract'):validate_target_capture_context(bad)
    with pytest.raises(ValueError):target.prepare_capture_context(store,wizard['wizard_id'],dict(metadata,action9=[0]*9),actor='OWN_TEST_ACTOR')
