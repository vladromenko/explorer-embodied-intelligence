"""Both existing native02/03 producers, original file writer and GET-only fake source."""
from copy import deepcopy
import json
import pytest
from explorer_shared.native_capture import NativeCaptureWriter
from explorer_shared.native_capture_service import NativeCaptureService
from explorer_shared.native_capture_identity import normalize_capture_context,native_context_sha256,validate_native_capture_start,validate_native_capture_finish
from test_native_capture import setup,context,encoded,outcomes


@pytest.mark.parametrize('project,mode',[('02','keyboard'),('03','so101_leader')])
def test_known_native_data_identity_survives_seal_without_control_or_foreign_adoption(tmp_path,project,mode):
    old,tap,camera,clock,_,initial=setup(tmp_path/'initial',project=project,mode=mode);old.close()
    writer=NativeCaptureWriter(tmp_path/'data',camera,clock_domain='nano',boot_id='boot',clock=clock)
    calls=[]
    class Reader:
        def status(self):calls.append('GET-status');return {'schema':'explorer_native_retarget_mux_v1'}
        def reference(self):calls.append('GET-reference');return {'measured':False}
        def dispatch_source_packet(self,cursor):
            calls.append('GET-page');return {'source_bytes':initial,'payload':json.loads(initial)}
    service=NativeCaptureService(writer,Reader(),poll_s=1,clock=clock)
    try:
        idle=service.status()['capture'];assert idle['source_clock_domain']=='nano' and idle['source_boot_id']=='boot' and idle['source_context_sha256'] is None
        skill=context(project,mode);skill.update(source_capture_attempt_id='a'*32,cooperative_capture_actor_id='owner',name='Реальный Unicode')
        before=deepcopy(skill);body={'skill_context':skill};identity=service.start(skill)['recording_id']
        expected=normalize_capture_context(skill);assert skill==before
        packet=service.status();assert packet['capture']['source_context_sha256']==native_context_sha256(expected)
        stamp={'clock_domain':'nano','boot_id':'boot'}
        assert validate_native_capture_start(body,stamp,packet)==identity
        for field,value in [('source_context_sha256','f'*64),('source_clock_domain','foreign'),('source_boot_id','foreign'),('recording_id','e'*32)]:
            bad=deepcopy(packet);bad['capture'][field]=value
            with pytest.raises(ValueError,match='Exact known'):validate_native_capture_start(body,stamp,bad)
        foreign=deepcopy(body);foreign['skill_context']['source_capture_attempt_id']='b'*32
        with pytest.raises(ValueError):validate_native_capture_start(foreign,stamp,packet)
        outcome=json.loads(outcomes(identity));service.finish(identity,outcome);assert writer.done.wait(3)
        manifest=json.loads((writer.folder/'manifest.json').read_bytes())
        assert validate_native_capture_finish(body,stamp,identity,manifest,outcome)==identity
        for key,value in [('skill_context',before),('physical_success_inferred',True),('state','recording')]:
            bad=deepcopy(manifest);bad[key]=value
            with pytest.raises(ValueError):validate_native_capture_finish(body,stamp,identity,bad,outcome)
        bad=deepcopy(manifest);bad['embodied_episode']['clock_domains']['nano']['boot_id']='other'
        with pytest.raises(ValueError):validate_native_capture_finish(body,stamp,identity,bad,outcome)
        bad=deepcopy(outcome);bad['outcomes']={}
        with pytest.raises(ValueError):validate_native_capture_finish(body,stamp,identity,manifest,bad)
        assert all(call.startswith('GET-') for call in calls)
    finally:service.close()
