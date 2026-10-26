"""Immutable-input ownership regressions; fake native setters, no CAD/render/steps."""
from copy import deepcopy
import io
from pathlib import Path
from types import SimpleNamespace

import mujoco
import numpy as np
import pytest

from explorer_shared import sim_initial_state as state
from test_sim_initial_state import initial_profile_fixture


def npy(value):
    stream=io.BytesIO();np.save(stream,np.asarray([value],dtype=np.float64),allow_pickle=False)
    return stream.getvalue()


@pytest.fixture
def native_fixture(tmp_path,monkeypatch):
    original_rng=np.random.get_state()
    env,provider,profile=initial_profile_fixture();profile['joint_q_aliases_qids']=False
    env.model=object();env.xml='SOFTWARE_FIXTURE_XML';env.action_space=SimpleNamespace();env.observation_space=SimpleNamespace()
    native=np.zeros(1,dtype=np.float64);env.integration_state=lambda:native.copy();provider.boot='new-SIM-clock';provider.mesh_files={}
    source=tmp_path/'driver.py';source.write_bytes(b'SOFTWARE_SOURCE_BYTES_NOT_EXECUTED')
    bindings={'driver':dict(path=source,sha256=state.digest(source.read_bytes()))}
    identity,source_payloads=state._source_payloads(bindings)
    payloads=dict(source_payloads,**{'profile-state.json':state.canonical(profile),'native-integration.npy':npy(0.),'compiled-model.mjb':b'MODEL','model.xml':env.xml.encode()})
    document=dict(schema=state.SCHEMA,profile=state.PROFILE,source_bindings=identity,runtime={},environment_class={},evaluator_class={},provider_class={},assets={},
        state_size=1,state_spec_value=int(mujoco.mjtState.mjSTATE_INTEGRATION),original_sim_clock_domain='original-SIM-clock',
        payloads={name:dict(bytes=len(raw),sha256=state.digest(raw)) for name,raw in payloads.items()},physical_io=False,retroactive_capture=False,original_runtime_OS_closure='PARTIAL')
    bundle=state.InitialStateBundle(document,payloads);expected=bundle.sha256
    monkeypatch.setattr(state,'_profile',lambda _:env);monkeypatch.setattr(state,'ENV_STATIC',());monkeypatch.setattr(state,'_runtime',lambda:{})
    monkeypatch.setattr(state,'_class_identity',lambda _:{});monkeypatch.setattr(state,'_model_bytes',lambda _:b'MODEL')
    calls=[]
    def setter(model,data,values,spec):native[:]=values;calls.append(values.copy())
    monkeypatch.setattr(mujoco,'mj_stateSize',lambda *args:1);monkeypatch.setattr(mujoco,'mj_setState',setter);monkeypatch.setattr(mujoco,'mj_forward',lambda *args:None)
    try:yield SimpleNamespace(env=env,provider=provider,bundle=bundle,expected=expected,bindings=bindings,native=native,calls=calls,source=source)
    finally:np.random.set_state(original_rng)


@pytest.mark.parametrize('mutation',['native_payload','clear_payloads','document','bindings','all_inputs'])
def test_restore_consumes_original_owned_bytes_and_bindings_even_if_caller_mutates_after_validation(native_fixture,monkeypatch,mutation):
    fixture=native_fixture;original_source_payloads=state._source_payloads;received=[]
    def source_barrier(actual):
        received.append(deepcopy(actual))
        assert actual is not fixture.bindings and actual['driver'] is not fixture.bindings['driver']
        if mutation in ('native_payload','all_inputs'):fixture.bundle.payloads['native-integration.npy']=npy(7.)
        if mutation=='clear_payloads':fixture.bundle.payloads.clear()
        if mutation in ('document','all_inputs'):
            fixture.bundle.document['original_sim_clock_domain']='FORGED_CLOCK'
            fixture.bundle.document['source_bindings']['driver']['sha256']='0'*64
            fixture.bundle.document['state_size']=123
        if mutation in ('bindings','all_inputs'):
            fixture.bindings['driver']['path']=fixture.source.with_name('does-not-exist.py')
            fixture.bindings['driver']['sha256']='0'*64
        return original_source_payloads(actual)
    monkeypatch.setattr(state,'_source_payloads',source_barrier)
    receipt=state.restore_initial_trial(fixture.provider,fixture.bundle,expected_sha256=fixture.expected,expected_source_bindings=fixture.bindings)
    assert len(fixture.calls)==2 and all(np.array_equal(call,[0.]) for call in fixture.calls)
    assert fixture.native.tolist()==[0.] and receipt['native_initial_state_equal'] is True
    assert receipt['initial_snapshot_sha256']==fixture.expected and receipt['original_sim_clock_domain']=='original-SIM-clock'
    assert received[0]['driver']==dict(path=str(fixture.source),sha256=state.digest(fixture.source.read_bytes()))


def test_unbound_native_payload_already_changed_at_entry_is_rejected_before_any_setter_or_source_read(native_fixture,monkeypatch):
    fixture=native_fixture;fixture.bundle.payloads['native-integration.npy']=npy(7.);reads=[]
    monkeypatch.setattr(state,'_source_payloads',lambda value:reads.append(True))
    before=state.canonical(state.encode(np.random.get_state()))
    with pytest.raises(state.InitialStateRejected,match='payload bytes changed'):
        state.restore_initial_trial(fixture.provider,fixture.bundle,expected_sha256=fixture.expected,expected_source_bindings=fixture.bindings)
    assert fixture.calls==[] and reads==[] and fixture.native.tolist()==[0.]
    assert state.canonical(state.encode(np.random.get_state()))==before


def test_internal_snapshot_has_immutable_payload_mapping_and_independent_canonical_document_bytes(native_fixture):
    fixture=native_fixture;snapshot=state._snapshot_bundle(fixture.bundle)
    document=state.canonical(fixture.bundle.document);original=snapshot.payloads['native-integration.npy']
    with pytest.raises(TypeError):snapshot.payloads['native-integration.npy']=npy(7.)
    fixture.bundle.document.clear();fixture.bundle.payloads.clear()
    assert snapshot.document_bytes==document and snapshot.payloads['native-integration.npy']==original


def test_validator_returns_owned_document_not_a_live_reference_to_callers_manifest(native_fixture):
    fixture=native_fixture;validated=state.validate_bundle(fixture.bundle,fixture.expected)
    fixture.bundle.document['source_bindings']['driver']['sha256']='0'*64
    assert validated['source_bindings']['driver']['sha256']==state.digest(fixture.source.read_bytes())


def test_save_writes_exact_validated_snapshot_when_original_mappings_change_after_check(native_fixture,tmp_path,monkeypatch):
    fixture=native_fixture;original=state._validate_snapshot
    def barrier(snapshot,expected):
        document=original(snapshot,expected)
        fixture.bundle.payloads['native-integration.npy']=npy(7.)
        fixture.bundle.document['original_sim_clock_domain']='FORGED_CLOCK'
        return document
    monkeypatch.setattr(state,'_validate_snapshot',barrier)
    folder=tmp_path/'saved';receipt=state.save_initial_bundle(fixture.bundle,folder)
    assert receipt['manifest_sha256']==fixture.expected
    assert np.load(io.BytesIO((folder/'native-integration.npy').read_bytes()),allow_pickle=False).tolist()==[0.]
    restored=state.load_initial_bundle(folder,expected_sha256=fixture.expected)
    assert restored.document['original_sim_clock_domain']=='original-SIM-clock'
