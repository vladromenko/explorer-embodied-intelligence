"""Small temp/mock driver tests, no full CAD/dynamics/renderer allocation."""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import xml.etree.ElementTree as ET
import weakref

import pytest

from explorer_shared import contact_diagnosis as contact
from explorer_shared import sim_initial_state as initial
from test_contact_diagnosis import XML, analysis_fixture


@pytest.fixture
def driver():
    path=Path(__file__).parents[1]/'scripts/run_contact_probes.py'
    spec=importlib.util.spec_from_file_location('contact_probe_driver_fixture',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def test_native_asset_locator_requires_exact_retained_archive_binding(driver,tmp_path):
    original=tmp_path/'original';archive=original/'source-archive';(archive/'objects').mkdir(parents=True)
    raw=b'EXPLICIT_SMALL_SOFTWARE_CAD_FIXTURE_NOT_A_PHYSICAL_STL';checksum=contact.sha(raw)
    descriptor=dict(original_locator='/historical/asset.STL',resolved_path='/historical/asset.STL',file='objects/cad.stl',sha256=checksum,bytes=len(raw))
    (archive/descriptor['file']).write_bytes(raw);manifest=contact.canonical(dict(files=[descriptor]));(archive/'manifest.json').write_bytes(manifest)
    xml=XML.replace(b'<worldbody>',b'<asset><mesh name="fixture" file="/historical/asset.STL"/></asset><worldbody>')
    assets=driver.prepare_assets(original,xml,tmp_path/'copied',expected_manifest_sha256=contact.sha(manifest))
    assert list(assets.values())==[checksum] and Path(next(iter(assets))).read_bytes()==raw
    located=driver.located_xml(xml,assets)
    assert contact.geometry_from_xml(located)['bin_bottom_world_top_z_m']==.008
    assert ET.fromstring(located).find('./asset/mesh').get('file')==next(iter(assets))
    (archive/descriptor['file']).write_bytes(b'CHANGED_RETAINED_ASSET')
    with pytest.raises(contact.ContactEvidenceRejected,match='SHA'):
        driver.prepare_assets(original,xml,tmp_path/'other',expected_manifest_sha256=contact.sha(manifest))
    with pytest.raises(contact.ContactEvidenceRejected,match='binding'):
        driver.prepare_assets(original,xml.replace(b'/historical/asset.STL',b'/unknown/asset.STL'),tmp_path/'unknown',expected_manifest_sha256=contact.sha(manifest))


def test_every_copied_condition_keeps_each_scenes_actual_static_goal_position(driver):
    positions=['.22 .19 0','.21 .18 0','.226 .198 0','.232 .178 0']
    for position in positions:
        raw=XML.replace(b'pos="0.2 0.1 0"',('pos="'+position+'"').encode())
        for condition in contact.probe_protocol_conditions():
            copied=contact.copied_condition_xml(raw,condition['id'])
            assert ET.fromstring(copied).find(".//body[@name='bin']").get('pos')==position


@pytest.mark.parametrize('change',['cpu','memory','swap'])
def test_unbounded_resources_rejected_before_provider_build(driver,monkeypatch,change):
    values={'cpu.max':'100000 100000','memory.max':str(2*1024**3),'memory.swap.max':'0'}
    values[{'cpu':'cpu.max','memory':'memory.max','swap':'memory.swap.max'}[change]]={'cpu':'200000 100000','memory':'max','swap':'1'}[change]
    monkeypatch.setattr(driver,'resource_snapshot',lambda:dict(values=values))
    with pytest.raises(contact.ContactEvidenceRejected,match='before any CAD'):
        driver.require_resources(dict(resources=dict(memory_max_bytes=2*1024**3)))


def mock_frozen(driver,tmp_path,monkeypatch):
    analysis=analysis_fixture();campaign=tmp_path/'original';campaign.mkdir()
    receipt=b'{"scope":"EXPLICIT_SOFTWARE_FIXTURE_NOT_ACTUAL_V9"}';(campaign/'campaign-qualification.json').write_bytes(receipt)
    xml=XML.replace(b'<worldbody>',b'<asset><mesh name="fixture" file="/historical/asset.STL"/></asset><worldbody>')
    cad=b'EXPLICIT_SOFTWARE_ASSET_BYTES_NOT_REAL_CAD';checksum=contact.sha(cad)
    for entry in analysis['attempts']:
        folder=campaign/entry['scenario']['id'];(folder/'source-archive/objects').mkdir(parents=True);(folder/'target').mkdir()
        state=[16.1,1.];native=dict(dispatch=dict(native_integration_state=state),actual_dispatched_action9=[90]*5+[30,0,0,0])
        trace=[{}]*317+[native]
        (folder/'target-model.xml').write_bytes(xml);(folder/'target/target-trace.json').write_bytes(contact.canonical(trace))
        (folder/'source-archive/objects/cad.stl').write_bytes(cad)
        manifest=contact.canonical(dict(files=[dict(original_locator='/historical/asset.STL',resolved_path='/historical/asset.STL',file='objects/cad.stl',sha256=checksum,bytes=len(cad))]))
        (folder/'source-archive/manifest.json').write_bytes(manifest)
        entry['bindings']=dict(target_model_xml_sha256=contact.sha(xml),target_trace_sha256=contact.sha(contact.canonical(trace)),source_archive_manifest_sha256=contact.sha(manifest))
        entry['new_initial_condition_source']=dict(original_native_state_json_sha256=contact.sha(contact.canonical(state)))
    monkeypatch.setattr(driver,'inspect_campaign',lambda *args,**kwargs:deepcopy(analysis))
    output=tmp_path/'probe';receipt=driver.freeze_campaign(campaign,output,contact.sha(receipt))
    return output,receipt


def test_freeze_only_retains_all_scene_bytes_and_pins_later_source_asset_and_condition(driver,tmp_path,monkeypatch):
    output,receipt=mock_frozen(driver,tmp_path,monkeypatch)
    plan=driver.load_protocol(output,receipt['protocol_sha256'])
    assert not receipt['actual_CAD_loaded'] and receipt['new_dynamics_steps']==0
    assert len(plan['cases'])==4 and len(plan['inputs'])==4 and len(plan['conditions'])==4
    assert plan['all_native_step_calls_planned']==plan['declared_contact_native_steps_total']+16*200
    assert not (output/'attempts').exists()
    Path(next(iter(plan['assets']))).write_bytes(b'CHANGED_AFTER_FREEZE')
    with pytest.raises(contact.ContactEvidenceRejected,match='SHA'):driver.load_protocol(output,receipt['protocol_sha256'])


def test_unsupported_registered_profile_retains_setup_failure_without_any_simulation(driver,tmp_path,monkeypatch):
    output,receipt=mock_frozen(driver,tmp_path,monkeypatch);plan=driver.load_protocol(output,receipt['protocol_sha256'])
    calls=[]
    def build(plan,case,condition,folder):
        calls.append('build');(folder/'compiled-input-model.xml').write_bytes(XML)
        class Position:
            def tolist(self):return [.2,.1,0.]
        return SimpleNamespace(env=SimpleNamespace(data=SimpleNamespace(time=0.),renderer=None,model=SimpleNamespace(body=lambda name:SimpleNamespace(pos=Position()))))
    def capture(*args,**kwargs):
        calls.append('capture');raise initial.InitialStateRejected('EXPLICIT_UNSUPPORTED_MOCK_PROFILE')
    monkeypatch.setattr(driver,'build_provider',build);monkeypatch.setattr(initial,'capture_initial_trial',capture)
    monkeypatch.setattr(driver,'require_resources',lambda plan:{'scope':'MOCK_NO_NATIVE_ALLOCATION'})
    monkeypatch.setattr(driver,'resource_snapshot',lambda:{'scope':'MOCK_NO_NATIVE_ALLOCATION'})
    monkeypatch.setattr(driver,'simulate',lambda *args:pytest.fail('Unsupported capture must prevent first native step'))
    result=driver.run_attempt(plan,output,0)
    assert calls==['build','capture'] and result['status']=='UNKNOWN_SETUP_OR_EXECUTION_FAILURE'
    assert result['initial_state_status']=='CAPTURE_FAILED' and result['native_steps']==0
    path=next((output/'attempts').glob('*/result.json'));assert json.loads(path.read_bytes())==result
    assert not list((output/'attempts').glob('*/substeps.jsonl'))


def test_failed_native_step_retains_exact_partial_state_bytes(driver,tmp_path):
    import numpy as np
    array=np.arange(15,dtype=np.float64).reshape(5,3);progress=dict(native_steps=1,native_states_recorded=1)
    with pytest.raises(RuntimeError,match='EXPLICIT_FAILURE'):
        with driver.trace_outputs(tmp_path,array,progress) as stream:
            stream.write(b'{"scope":"MOCK_PARTIAL"}\n');raise RuntimeError('EXPLICIT_FAILURE')
    actual=np.load(tmp_path/'native-states.npy',allow_pickle=False)
    assert actual.shape==(2,3) and actual.tobytes()==array[:2].tobytes()
    assert (tmp_path/'substeps.jsonl').read_bytes()==b'{"scope":"MOCK_PARTIAL"}\n'


def test_nonfinite_failure_evidence_is_explicit_not_zero_or_silently_removed(driver):
    actual=driver.nonfinite_evidence(dict(force=[float('nan'),float('inf'),-3.]))
    assert actual==dict(force=[dict(native_nonfinite='nan'),dict(native_nonfinite='inf'),-3.])
    assert json.loads(contact.canonical(actual))==actual


def test_contact_recovery_is_not_inferred_from_large_normal_magnitude(driver):
    geometry=contact.geometry_from_xml(XML)
    rows=[dict(post_step_time_s=t,object=dict(minimum_point_z_m=-.01),maximum_object_point_speed_m_s=0.,
        other_object_contacts=[],inside_bottom_xy=True,bottom_signed_world_force_N=[0.,0.,-33.],
        bottom_normal_magnitude_N=33.,contacts=[dict(distance_m=-.014)]) for t in (.8,.9,1.)]
    result=driver.contact_outcome(rows,geometry)
    assert not result['contact_recovered'] and not result['terminal_supported']
    assert result['maximum_downward_bottom_force_N']==33. and not result['full_task_success_claimed']


def fake_model():
    import numpy as np
    class Model:
        pass
    model=Model()
    for name in ('nq','nv','na','nu','nbody','njnt','ngeom','neq'):setattr(model,name,3)
    for name in ('jnt_type','jnt_qposadr','jnt_dofadr','geom_bodyid','actuator_trnid'):
        setattr(model,name,np.arange(3,dtype=np.int32))
    return model


def test_original_model_and_data_are_gone_before_replacement_CAD_compile(driver):
    class Data:
        def __init__(self,model):self.model=model
    env=SimpleNamespace(model=fake_model());env.data=Data(env.model)
    old_model=weakref.ref(env.model);old_data=weakref.ref(env.data);calls=[]
    def compile_next():
        calls.append('compile')
        assert env.model is None and env.data is None
        assert old_model() is None and old_data() is None
        return fake_model()
    result=driver.release_then_compile(env,compile_next)
    assert calls==['compile'] and result.nq==3 and env.model is None and env.data is None


def test_small_native_ABI_is_immutable_and_changed_replacement_does_not_get_installed(driver):
    model=fake_model();snapshot=driver.native_abi(model);model.jnt_type[0]=123
    assert snapshot['arrays']['jnt_type'][2]!=model.jnt_type.tobytes()
    env=SimpleNamespace(model=model,data=object())
    with pytest.raises(contact.ContactEvidenceRejected,match='shape or identity'):
        driver.release_then_compile(env,fake_model)
    assert env.model is None and env.data is None
