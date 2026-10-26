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
