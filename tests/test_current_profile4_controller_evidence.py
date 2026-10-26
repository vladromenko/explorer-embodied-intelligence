"""Inspect final actual controller/source pin evidence with no model allocation."""
import hashlib
import json
from pathlib import Path

ROOT=Path(__file__).parents[1]
PROOFS=ROOT/'shared/training'
PREFIX='NATIVE6_QUEUE_PROFILE4_20261026_'


def read(suffix):return json.loads((PROOFS/(PREFIX+suffix+'.json')).read_text())


def test_actual_current_profile4_controller_resumes_both_official_CPU_backends_and_preserves_full_source():
    index=json.loads((ROOT/'_registry/evidence/native6-queue-profile4-current-20261026.json').read_text())
    for name,expected in index['archives'].items():
        assert Path(name).name==name and hashlib.sha256((PROOFS/name).read_bytes()).hexdigest()==expected
    runtime,library,tree=read('RUNTIME'),read('SOURCE_LIBRARY'),read('SOURCE_TREE')
    assert runtime['pytest_exit_code']==0 and index['actual_tests_passed']==2
    digest=hashlib.sha256(json.dumps(tree,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    assert all(row['tree_sha256']==digest and row['regular_files']>1000 for row in library['source_archives'])
    assert len({row['sha256'] for row in library['source_archives']})==1
    for name,expected in runtime['implementation_sha256'].items():
        assert tree[name]==expected and hashlib.sha256((ROOT/name).read_bytes()).hexdigest()==expected
    for name in ('ACT','DIFFUSION'):
        proof=read(name+'_QUALIFICATION')
        assert proof['actual_supervisor_bootstrap_job_runner_used'] and proof['old_process_gone']
        assert proof['model_optimizer_losses_Torch_RNG_sampler_exactly_equal']
        assert proof['own_Python_NumPy_RNG_checkpoint_continuation_verified']
        assert 1<=proof['paused_at_completed_step']<8 and proof['updates_in_resumed_process']==8-proof['paused_at_completed_step']
    assert len(library['worker_jobs'])==4
    for row in library['worker_jobs']:
        assert row['config']['_execution_commit']==index['source_commit'] and row['status']=='completed'
    assert runtime['cgroup_memory_events'].splitlines()==['low 0','high 0','max 0','oom 0','oom_kill 0','oom_group_kill 0']
    assert not index['physical_success'] and not index['public_production_queue_tested']
