"""Active measured native6 evidence is source-bound; no model allocation."""
import hashlib
import json
from pathlib import Path

ROOT=Path(__file__).parents[1]
PROOFS=ROOT/'shared/training'


def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def test_active_current_native6_binding_matches_real_resume_metadata_and_current_code():
    active=json.loads((PROOFS/'DIFFUSION_NATIVE6_ACTIVE_BINDING.json').read_bytes())
    assert active['schema']=='explorer_native6_diffusion_active_binding_v1'
    selected={}
    for key in ('source_binding','qualification_result','implementation_binding'):
        path=(PROOFS/active[key]).resolve()
        assert path.is_relative_to(PROOFS.resolve()) and path.suffix=='.json' and path.is_file()
        selected[key]=json.loads(path.read_bytes())
    source,receipt,implementation=[selected[key] for key in ('source_binding','qualification_result','implementation_binding')]
    assert source['schema']=='explorer_native6_diffusion_source_qualification_binding_v1'
    assert implementation['schema']=='explorer_native6_diffusion_implementation_binding_v1'
    assert source['qualification_result_sha256']==implementation['qualification_result_sha256']==sha(PROOFS/active['qualification_result'])
    signature=hashlib.sha256(json.dumps(implementation['resume_metadata'],sort_keys=True,allow_nan=False).encode()).hexdigest()
    assert signature==implementation['resume_signature']==receipt['training_resume']['signature']
    assert receipt['training_resume']['resumed_from_step']==4 and receipt['training_resume']['optimizer_steps_executed_this_process']==4 and receipt['optimizer_steps']==implementation['completed_steps']==8
    assert receipt['project_id']==receipt['original_spec_owner_experiment']=='04' and receipt['source_kind']=='SIMULATION' and not receipt['physical_io']
    assert len(source['implementation_sha256'])==21 and 'shared/explorer_shared/so101_station.py' in source['implementation_sha256']
    for name,expected in source['implementation_sha256'].items():
        path=(ROOT/name).resolve();assert path.is_relative_to(ROOT.resolve()) and sha(path)==expected
    assert {'00-control-center/src/explorer/source_demo_workflow.py','shared/explorer_shared/native_capture_approval.py',
        'shared/explorer_shared/capture_timeline.py'}<=set(source['implementation_sha256'])
    assert implementation['shared_implementation_sha256']==source['implementation_sha256']
    observation_path=(PROOFS/source['runtime_observation']).resolve()
    assert observation_path.is_relative_to(PROOFS.resolve()) and sha(observation_path)==source['runtime_observation_sha256']
    observation=json.loads(observation_path.read_bytes());before,after=observation['before'],observation['after']
    assert before['runtime']==after['runtime'] and before['implementation_sha256']==after['implementation_sha256']==source['implementation_sha256']
    assert after['pytest_exit_code']==0 and after.get('failure') is None
    packages=before['runtime']['packages']
    assert all(len(value['record_sha256'])==64 for value in packages.values())
    assert implementation['measured_versions']=={name:row['version'] for name,row in packages.items()}
    official=implementation['official_implementation_sha256']
    for name,expected in before['runtime']['official_sources_sha256'].items():
        measured_path=str(Path(packages[name.split('/',1)[0]]['location'])/name)
        assert official[measured_path]==expected
    for suffix in ('modeling_diffusion.py','configuration_diffusion.py','optimizers.py','scheduling_ddim.py'):
        assert any(name.endswith('/'+suffix) for name in official)
    assert source['cgroup_limits_verified']['memory_swap_max']==0 and after['cgroup_swap_peak_bytes']==0
    assert not source['public_queue_integration_qualified'] and not source['physical_rollout_qualified']
