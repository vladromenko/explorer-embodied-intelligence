"""CPU-light state codec/RNG safety; actual CAD replay is a separate qualifier."""
import base64
import copy
import json
from types import SimpleNamespace

import numpy as np
import pytest

from explorer_shared.sim_initial_state import (InitialStateBundle,InitialStateRejected,
    PROFILE,SCHEMA,canonical,decode,digest,encode,validate_bundle,_generator_state,_restore_generator)


def test_safe_JSON_preserves_array_ABI_large_unsigned_RNG_ints_tuple_sets_and_scalars():
    value=dict(matrix=np.arange(12,dtype='>f8').reshape(3,4),scalar=np.uint64(2**63+17),
        rng=(3,(2**130+11,9),None),ids={2,1},flags=[True,False])
    result=decode(json.loads(canonical(encode(value))))
    assert result['matrix'].dtype.str=='>f8' and result['matrix'].shape==(3,4)
    assert result['matrix'].tobytes()==value['matrix'].tobytes()
    assert type(result['scalar']) is np.uint64 and result['scalar']==value['scalar']
    assert result['rng']==value['rng'] and result['ids']=={1,2} and result['flags']==[True,False]


@pytest.mark.parametrize('value',[np.array([object()],dtype=object),np.array([float('nan')]),float('inf'),object(),{1:'nonstring'}])
def test_unsafe_arbitrary_objects_pickle_and_nonfinite_values_rejected(value):
    with pytest.raises(InitialStateRejected):encode(value)


def test_decoder_rejects_fake_shape_dtype_payload_and_excessive_depth():
    value=encode(np.ones(3,dtype=np.float64))
    for change in (dict(dtype='O'),dict(shape=[2**40]),dict(shape=[True]),dict(data='not base64'),dict(shape=[4])):
        with pytest.raises(InitialStateRejected):decode(dict(value,**change))
    nested=None
    for _ in range(66):nested={'_type':'list','items':[nested]}
    with pytest.raises(InitialStateRejected,match='nesting'):decode(nested)


def test_capture_does_not_touch_lazy_unused_rng_properties_or_create_new_generator():
    class Owner:
        @property
        def np_random(self):raise AssertionError('Lazy RNG must not be initialized')
    owner=Owner();before=vars(owner).copy()
    record=_generator_state(owner)
    assert record==dict(attribute_present=False,state=None) and vars(owner)==before
    restored=Owner();_restore_generator(restored,record)
    assert vars(restored)==before


def test_existing_generator_state_replays_exactly_without_advancing_original():
    owner=SimpleNamespace(_np_random=np.random.default_rng(812));before=copy.deepcopy(owner._np_random.bit_generator.state)
    record=_generator_state(owner);assert owner._np_random.bit_generator.state==before
    restored=SimpleNamespace();_restore_generator(restored,record)
    assert np.array_equal(owner._np_random.standard_normal(32),restored._np_random.standard_normal(32))
    assert owner._np_random.bit_generator.state==restored._np_random.bit_generator.state


def test_bundle_hash_and_every_payload_bound_before_restore_without_executing_any_code():
    payload=b'opaque MJB/NPY source bytes never executed by this pure validator'
    document=dict(schema=SCHEMA,profile=PROFILE,payloads={'file.bin':dict(bytes=len(payload),sha256=digest(payload))},
        physical_io=False,retroactive_capture=False,original_runtime_OS_closure='PARTIAL')
    bundle=InitialStateBundle(document,{'file.bin':payload})
    assert validate_bundle(bundle,bundle.sha256)==document
    with pytest.raises(InitialStateRejected):validate_bundle(bundle,'0'*64)
    with pytest.raises(InitialStateRejected):validate_bundle(InitialStateBundle(document,{'file.bin':payload+b'changed'}),bundle.sha256)
    false=InitialStateBundle(dict(document,original_runtime_OS_closure='COMPLETE'),{'file.bin':payload})
    with pytest.raises(InitialStateRejected):validate_bundle(false,false.sha256)


def test_forged_huge_NPY_shape_rejected_before_array_allocation():
    import io
    from explorer_shared.sim_initial_state import _read_native
    stream=io.BytesIO();np.lib.format.write_array_header_1_0(stream,dict(descr='<f8',fortran_order=False,shape=(2**50,)))
    with pytest.raises(InitialStateRejected):_read_native(stream.getvalue(),3)


def test_immutable_bundle_files_roundtrip_and_reject_path_symlink_extra_and_tamper(tmp_path):
    from explorer_shared.sim_initial_state import save_initial_bundle,load_initial_bundle
    raw=b'opaque bytes';document=dict(schema=SCHEMA,profile=PROFILE,payloads={'source/file.bin':dict(bytes=len(raw),sha256=digest(raw))},physical_io=False,retroactive_capture=False,original_runtime_OS_closure='PARTIAL')
    bundle=InitialStateBundle(document,{'source/file.bin':raw});folder=tmp_path/'snapshot'
    save_initial_bundle(bundle,folder)
    assert load_initial_bundle(folder,expected_sha256=bundle.sha256).payloads==bundle.payloads
    with pytest.raises(InitialStateRejected):save_initial_bundle(bundle,folder)
    (folder/'extra').write_bytes(b'unaccounted')
    with pytest.raises(InitialStateRejected):load_initial_bundle(folder,expected_sha256=bundle.sha256)
    (folder/'extra').unlink();(folder/'source/file.bin').write_bytes(b'changed')
    with pytest.raises(InitialStateRejected):load_initial_bundle(folder,expected_sha256=bundle.sha256)
    (folder/'source/file.bin').unlink();(folder/'source/file.bin').symlink_to(folder/'manifest.json')
    with pytest.raises(InitialStateRejected):load_initial_bundle(folder,expected_sha256=bundle.sha256)
    bad=InitialStateBundle(dict(document,payloads={'../escape':dict(bytes=1,sha256=digest(b'x'))}),{'../escape':b'x'})
    with pytest.raises(InitialStateRejected):save_initial_bundle(bad,tmp_path/'bad')
    assert not (tmp_path/'escape').exists()


def initial_profile_fixture():
    from explorer_sim.manipulation_env import HOME
    from explorer_shared.sim_initial_state import ENV_STATE,EVALUATOR_FIELDS,DERIVED_FIELDS,OPTION_FIELDS
    Env=type('ExplicitFakeInitialEnv',(SimpleNamespace,),{'__module__':'explorer_sim.manipulation_env'})
    environment=dict(command=HOME.copy(),command_before=HOME.copy(),requested=HOME.copy(),raw_action=np.zeros(6),phase='uncontrolled',steps=0,
        last_distance=np.float64(.1),record_substeps=True,substep_trace=[])
    evaluator={name:0. for name in EVALUATOR_FIELDS}
    evaluator.update(goal=np.array([.22,.19,0.]),half=np.array([.073,.063]),initial_height=.01,rim=.044,dt=.002,
        grasped=False,lifted=False,carried=False,success=False,substeps=0,contact_substeps=0,bilateral_substeps=0,last={})
    options={name:np.zeros(1,dtype=np.uint8) for name in OPTION_FIELDS}
    options.update(label=0,frame=0,bvh_depth=1,flex_layer=0)
    derived={name:np.zeros(1) for name in DERIVED_FIELDS}
    provider=SimpleNamespace(phase_evidence_map={'approach':['approach_proximity']})
    env=Env(**environment,evaluator=SimpleNamespace(**evaluator),options=SimpleNamespace(**options),data=SimpleNamespace(**derived,warning=[SimpleNamespace(number=0,lastinfo=0)]))
    profile=dict(environment=encode(environment),static=encode({}),evaluator=encode(evaluator),options=encode(options),derived=encode(derived),
        warnings=encode([dict(number=0,lastinfo=0)]),provider=encode(dict(sequence=0,phase_results={},phase_evidence_map=provider.phase_evidence_map)),
        env_rng={'attribute_present':False,'state':None},action_space_rng={'attribute_present':False,'state':None},observation_space_rng={'attribute_present':False,'state':None},
        env_seed_attribute_present=True,env_seed=417,numpy_legacy_rng=encode(np.random.RandomState(17).get_state()),
        python_random_present=False,python_rng=None,renderer_initialized=False,joint_q_aliases_qids=True)
    assert set(environment)==set(ENV_STATE)
    return env,provider,profile


@pytest.mark.parametrize('change',['warning_overflow','warning_type','command_shape','endpoint_bound','outcome_latch','provider_fields','seed_shape','option_enum'])
def test_resealed_malformed_profile_is_rejected_before_native_setters_heavy_reads_or_state_changes(monkeypatch,change):
    import mujoco
    from explorer_shared import sim_initial_state as initial
    env,provider,profile=initial_profile_fixture();native_calls=[];source_reads=[]
    before=canonical(encode({name:getattr(env,name) for name in initial.ENV_STATE}));rng_before=canonical(encode(np.random.get_state()))
    if change.startswith('warning'):
        values=decode(profile['warnings']);values[0]['number']=2**40 if change=='warning_overflow' else 'not-an-int';profile['warnings']=encode(values)
    elif change=='command_shape':
        values=decode(profile['environment']);values['command']=np.zeros(7);profile['environment']=encode(values)
    elif change=='endpoint_bound':
        values=decode(profile['environment']);values['requested'][0]=1000;profile['environment']=encode(values)
    elif change=='outcome_latch':
        values=decode(profile['evaluator']);values['success']=True;profile['evaluator']=encode(values)
    elif change=='provider_fields':
        values=decode(profile['provider']);values['unregistered']=True;profile['provider']=encode(values)
    elif change=='seed_shape':profile['env_seed']=encode(np.array([417]))
    else:
        values=decode(profile['options']);values['label']=999;profile['options']=encode(values)
    raw=canonical(profile);document=dict(schema=SCHEMA,profile=PROFILE,payloads={'profile-state.json':dict(bytes=len(raw),sha256=digest(raw))},
        physical_io=False,retroactive_capture=False,original_runtime_OS_closure='PARTIAL')
    bundle=InitialStateBundle(document,{'profile-state.json':raw})
    monkeypatch.setattr(initial,'_profile',lambda value:env)
    monkeypatch.setattr(initial,'_source_payloads',lambda value:source_reads.append(True))
    monkeypatch.setattr(mujoco,'mj_setState',lambda *args:native_calls.append(True))
    with pytest.raises(InitialStateRejected):initial.restore_initial_trial(provider,bundle,expected_sha256=bundle.sha256,expected_source_bindings={})
    assert native_calls==[] and source_reads==[]
    assert canonical(encode({name:getattr(env,name) for name in initial.ENV_STATE}))==before
    assert canonical(encode(np.random.get_state()))==rng_before


def test_actual_profile_types_pass_complete_preflight_without_mutation():
    from explorer_shared.sim_initial_state import _validate_profile_state
    env,provider,profile=initial_profile_fixture()
    decoded={name:decode(profile[name]) for name in ('environment','evaluator','derived','warnings','options','provider')}
    _validate_profile_state(env,provider,profile,decoded)
