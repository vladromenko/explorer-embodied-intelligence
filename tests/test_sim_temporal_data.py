"""Real canonical source fixtures; no optimizer, MuJoCo, network or devices."""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path

import numpy as np
import pytest

from explorer_shared.contracts import ACTION_NAMES,canonical,digest
from explorer_shared.learning.sim_object_route import canonical_teacher,sha
from explorer_shared.learning.sim_temporal_route import CELLS,TemporalObjectChunks,decode_increment9,source_rate_profile
from explorer_shared.sim_initial_state import encode
from test_sim_object_route import fixture,plan


RATE=dict(velocity_deg_s=[48,48,48,60,60,72],period_s=.05)


def dataset(tmp_path):
    case,result,rows,obs,images,score=fixture();envelope,payloads,report,admission=canonical_teacher(result,rows,obs,images,case,'skill',3,score)
    with (tmp_path/'view.npz').open('wb') as f:np.savez_compressed(f,rgb=np.full((9,8,8,3),107,np.uint8),
        state_native=np.array([o['preceding_command9'] for o in obs],np.float32),action_native=np.array([r['actual_dispatched_action9'] for r in rows],np.float32),
        frame_monotonic_ns=200000000+np.arange(9,dtype=np.int64)*50000000)
    (tmp_path/'envelope.json').write_bytes(canonical(envelope));(tmp_path/'admission.json').write_bytes(canonical(report))
    descriptor=dict(id=case['id'],geometry_signature=case['geometry_signature'],split='train',admission=admission,file='view.npz',sha256=sha((tmp_path/'view.npz').read_bytes()),
        canonical_envelope_file='envelope.json',canonical_envelope_sha256=digest(envelope),canonical_admission_file='admission.json',canonical_admission_sha256=digest(report))
    source=dict(schema='explorer_fresh_target_visual_dataset_v1',action_names=list(ACTION_NAMES),split_plan=plan(),episodes=[descriptor],source_files_sha256={'view.npz':descriptor['sha256']})
    (tmp_path/'manifest.json').write_bytes(canonical(source));return tmp_path/'manifest.json',rows,obs


def test_factorial_source_boundary_identical_first_state10_clock_ablation_and_increment_targets(tmp_path):
    path,rows,observations=dataset(tmp_path);original=path.read_bytes();cells={cell:TemporalObjectChunks(path,cell=cell,chunk_size=4,image_size=8,rate_profile=RATE,horizon_s=40) for cell in CELLS}
    first=[cell[0]['observation.state'].numpy() for cell in cells.values()]
    assert all(value.shape==(10,) and np.array_equal(value,first[0]) for value in first)
    zero=cells['ZERO_ABS'][5];elapsed=cells['ELAPSED_ABS'][5]
    np.testing.assert_array_equal(zero['observation.state'][:9],elapsed['observation.state'][:9]);assert zero['observation.state'][9]==0<elapsed['observation.state'][9]
    np.testing.assert_array_equal(zero['action'],elapsed['action'])
    increment=cells['ELAPSED_INCREMENT'][5]['action'][0].numpy();decoded=decode_increment9(increment,observations[5]['preceding_command9'],velocity6=RATE['velocity_deg_s'],period_s=RATE['period_s'])
    np.testing.assert_allclose(decoded,rows[5]['actual_dispatched_action9'],atol=1e-5)
    assert cells['ZERO_ABS'].resume_context['actual_recorded_clock_available'] is True and cells['ZERO_ABS'].resume_context['clock_value_used'] is False
    assert path.read_bytes()==original


def test_source_rate_requires_all_dispatches_and_exact_captured_static_profile(tmp_path):
    initial=tmp_path/'initial-state';initial.mkdir()
    profile=canonical({'static':encode(dict(velocity=np.array(RATE['velocity_deg_s']),period=RATE['period_s']))});(initial/'profile-state.json').write_bytes(profile)
    manifest=canonical({'payloads':{'profile-state.json':{'sha256':sha(profile)}}});(initial/'manifest.json').write_bytes(manifest)
    rows=[dict(dispatch_preparation=dict(controller_settings_modified=False,original_period_s=RATE['period_s'],original_velocity_deg_s=RATE['velocity_deg_s'])) for _ in range(3)]
    def save():
        (tmp_path/'target-trace.json').write_bytes(canonical(rows));(tmp_path/'result.json').write_bytes(canonical(dict(eligible_as_simulation_expert=True,target_trace_sha256=digest(rows),initial_state={'manifest_sha256':sha(manifest)})))
    save();motion=tmp_path/'motion.json';motion.write_bytes(canonical({'streaming':{'velocity_deg_s':RATE['velocity_deg_s']}}))
    result=source_rate_profile(tmp_path,motion);assert result['verified_dispatch_rows']==3 and result['velocity_deg_s']==RATE['velocity_deg_s']
    rows[1]['dispatch_preparation']['original_period_s']=.1;save()
    with pytest.raises(ValueError,match='Every actual dispatch'):source_rate_profile(tmp_path,motion)


def test_four_model_freeze_rejects_different_initial_weights_before_any_model_read(tmp_path):
    spec=importlib.util.spec_from_file_location('new_factorial_driver',Path(__file__).resolve().parents[1]/'scripts/run_sim_temporal_learning.py');module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    for index,cell in enumerate(CELLS):
        folder=tmp_path/'train'/cell;folder.mkdir(parents=True);(folder/'training-result.json').write_bytes(canonical(dict(initial_parameters_sha256=str(index)*64,checkpoint_files_sha256={'config.json':'a'*64})))
    with pytest.raises(ValueError,match='identical initial parameters'):module.seal({},tmp_path)
    assert not (tmp_path/'four-policy-freeze.json').exists()


def test_four_checkpoint_freeze_binds_all_actual_mock_weights_configs_and_precedes_any_heldout(tmp_path):
    spec=importlib.util.spec_from_file_location('factorial_gate_driver',Path(__file__).resolve().parents[1]/'scripts/run_sim_temporal_learning.py');module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    settings={'chunk_size':16};plan_doc=dict(settings=settings,training_source_manifest_sha256='b'*64)
    (tmp_path/'protocol.json').write_bytes(canonical(plan_doc))
    config=dict(input_features={'observation.state':{'shape':[10]}},output_features={'action':{'shape':[9]}},chunk_size=16)
    for cell in CELLS:
        folder=tmp_path/'train'/cell;folder.mkdir(parents=True);checkpoint=folder/'student_act';checkpoint.mkdir()
        (checkpoint/'config.json').write_bytes(canonical(config));(checkpoint/'model.safetensors').write_bytes(('EXPLICIT_MOCK_NOT_MODEL_'+cell).encode())
        files={name:sha((checkpoint/name).read_bytes()) for name in ('config.json','model.safetensors')}
        row=dict(initial_parameters_sha256='a'*64,checkpoint_files_sha256=files,checkpoint_path=str(checkpoint),cell=cell,integration_only=False,optimizer_steps=1200,
            settings=settings,heldout_optimizer_examples=0,state_dimension=10,action_dimension=9,source_manifest_sha256='b'*64,reload_max_abs_error=0.,checkpoint_sha256=files['model.safetensors'])
        (folder/'training-result.json').write_bytes(canonical(row))
    result=module.seal(plan_doc,tmp_path);assert result['four_policy_freeze_sha256']==sha((tmp_path/'four-policy-freeze.json').read_bytes())
    (tmp_path/'rollout/development/started').mkdir(parents=True)
    with pytest.raises(ValueError,match='after heldout has begun'):module.seal(plan_doc,tmp_path)
    assert len(json.loads((tmp_path/'four-policy-freeze.json').read_bytes())['cells'])==4
