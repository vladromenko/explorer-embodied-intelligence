"""Current R4 shared controller evidence plus explicitly historical PROFILE4 archive."""
import hashlib
import json
from pathlib import Path

ROOT=Path(__file__).parents[1]
PROOFS=ROOT/'shared/training'
CURRENT='NATIVE6_QUEUE_02A_CONTROLLER_R4_20261027_'
HISTORICAL='NATIVE6_QUEUE_PROFILE4_20261026_'


def read(suffix,prefix=CURRENT):return json.loads((PROOFS/(prefix+suffix+'.json')).read_bytes())
def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def test_actual_current_controller_resumes_both_official_CPU_backends_and_preserves_full_source():
    summary,runtime,library,tree=[read(name) for name in ('SUMMARY','RUNTIME','SOURCE_LIBRARY','SOURCE_TREE')]
    assert runtime['pytest_exit_code']==0 and runtime['failure'] is None and summary['actual_pytest_passed']==2
    assert runtime['source_commit']==summary['source_commit']==library['source_commit']
    for name,expected in summary['published_source_files_sha256'].items():
        assert Path(name).name==name and sha(PROOFS/name)==expected
    digest=hashlib.sha256(json.dumps(tree,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    assert len(library['source_archives'])==2
    assert all(row['tree_sha256']==digest and row['regular_files']==len(tree)>1000 and row['extracted_regular_bytes_verified'] for row in library['source_archives'])
    assert len({row['sha256'] for row in library['source_archives']})==1
    for name,expected in runtime['implementation_sha256'].items():
        assert tree[name]==expected
        # Gate and test/harness files are auxiliary frozen-source evidence.
        # Later Gate engineering changes are independently qualified; they do
        # not silently turn this historical auxiliary receipt into latest proof.
        if name!='shared/explorer_shared/station_publication_gate.py' and not name.startswith('tests/') and name!='conftest.py':
            assert sha(ROOT/name)==expected
    for name,expected in runtime['binding_files_sha256'].items():assert sha(ROOT/name)==expected
    active=json.loads((PROOFS/'DIFFUSION_NATIVE6_ACTIVE_BINDING.json').read_bytes())
    assert runtime['selector']['identity']['native6_qualification_sha256']==sha(PROOFS/active['qualification_result'])
    for name in ('ACT','DIFFUSION'):
        proof=read(name+'_QUALIFICATION');transition=library['process_transitions'][name.lower()]
        assert proof['source_commit']==runtime['source_commit']
        assert proof['actual_supervisor_bootstrap_job_runner_used'] and proof['old_process_gone']
        assert proof['model_optimizer_losses_Torch_RNG_sampler_exactly_equal'] and transition['typed_tensor_equality_verified']
        assert proof['own_Python_NumPy_RNG_checkpoint_continuation_verified']
        assert 1<=proof['paused_at_completed_step']<8 and proof['updates_in_resumed_process']==8-proof['paused_at_completed_step']
        assert transition['paused_worker']['pid']==proof['paused_pid']!=transition['resumed_worker']['pid']
        assert transition['paused_worker']['boot_id']==transition['resumed_worker']['boot_id']
        assert transition['paused_worker']['start_ticks']<transition['resumed_worker']['start_ticks']
        assert proof['model_weights_sha256']==read(name+'_QUALIFICATION',HISTORICAL)['model_weights_sha256']
    assert len(library['worker_jobs'])==4
    for row in library['worker_jobs']:
        assert row['config']['_execution_commit']==runtime['source_commit'] and row['status']=='completed'
    assert runtime['cgroup_memory_events'].splitlines()==['low 0','high 0','max 0','oom 0','oom_kill 0','oom_group_kill 0']
    assert runtime['cgroup_peak_memory_bytes']<=runtime['limits']['memory_max'] and runtime['cgroup_swap_peak_bytes']==0
    assert not summary['physical_io'] and not summary['production_state_writes'] and not summary['task_success_claimed']


def test_historical_profile4_archive_remains_bound_to_its_own_frozen_source():
    index=json.loads((ROOT/'_registry/evidence/native6-queue-profile4-current-20261026.json').read_bytes())
    for name,expected in index['archives'].items():
        assert Path(name).name==name and sha(PROOFS/name)==expected
    runtime,library,tree=[read(name,HISTORICAL) for name in ('RUNTIME','SOURCE_LIBRARY','SOURCE_TREE')]
    assert runtime['source_commit']==index['source_commit'] and runtime['pytest_exit_code']==0 and index['actual_tests_passed']==2
    for name,expected in runtime['implementation_sha256'].items():assert tree[name]==expected
    assert all(row['config']['_execution_commit']==index['source_commit'] for row in library['worker_jobs'])
    assert not index['physical_success'] and not index['public_production_queue_tested']
