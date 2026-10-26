"""Current native6 evidence is source-bound; no model or runtime allocation."""
import hashlib
import json
from pathlib import Path

ROOT=Path(__file__).parents[1]
PROOFS=ROOT/'shared/training'


def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def test_active_current_native6_binding_matches_real_resume_metadata_and_current_code():
    active=json.loads((PROOFS/'DIFFUSION_NATIVE6_ACTIVE_BINDING.json').read_text())
    assert active['schema']=='explorer_native6_diffusion_active_binding_v1'
    selected={}
    for key in ('source_binding','qualification_result','implementation_binding'):
        path=(PROOFS/active[key]).resolve();assert path.is_relative_to(PROOFS.resolve()) and '_20261026_CURRENT_' in path.name
        selected[key]=json.loads(path.read_text())
    source,receipt,implementation=[selected[key] for key in ('source_binding','qualification_result','implementation_binding')]
    assert source['qualification_result_sha256']==implementation['qualification_result_sha256']==sha(PROOFS/active['qualification_result'])
    signature=hashlib.sha256(json.dumps(implementation['resume_metadata'],sort_keys=True,allow_nan=False).encode()).hexdigest()
    assert signature==implementation['resume_signature']==receipt['training_resume']['signature']
    assert receipt['training_resume']['resumed_from_step']==4 and receipt['training_resume']['optimizer_steps_executed_this_process']==4 and receipt['optimizer_steps']==8
    assert receipt['project_id']==receipt['original_spec_owner_experiment']=='04' and receipt['source_kind']=='SIMULATION' and not receipt['physical_io']
    for name,expected in source['implementation_sha256'].items():
        path=(ROOT/name).resolve();assert path.is_relative_to(ROOT.resolve()) and sha(path)==expected
    for suffix in ('modeling_diffusion.py','configuration_diffusion.py','optimizers.py','scheduling_ddim.py'):
        assert any(name.endswith('/'+suffix) for name in implementation['official_implementation_sha256'])
    assert implementation['official_source_measurement_basis'].startswith('Fresh exact files hashed before and after')
    observation=json.loads((PROOFS/source['runtime_observation']).read_text())
    assert observation['before']['runtime']==observation['after']['runtime'] and observation['after']['pytest_exit_code']==0
    assert all(value['record_sha256'] for value in implementation['measured_distribution_records'].values())
    assert not source['public_queue_integration_qualified'] and not source['physical_rollout_qualified']
