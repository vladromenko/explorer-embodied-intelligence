"""Archive-integrity checks for real native6 controller runs, no model imports."""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).parents[1]


def test_actual_final_controller_proof_covers_both_backends_and_exact_worker_pins():
    index = json.loads((ROOT / '_registry/evidence/native6-queue-final-20261026.json').read_text())
    def read(suffix):
        return json.loads((ROOT / 'shared/training' / ('NATIVE6_QUEUE_FINAL_20261026_' + suffix + '.json')).read_text())
    for name, expected in index['archives'].items():
        assert Path(name).name == name
        assert hashlib.sha256((ROOT / 'shared/training' / name).read_bytes()).hexdigest() == expected
    runtime, library = read('RUNTIME'), read('SOURCE_LIBRARY')
    assert runtime['pytest_exit_code'] == 0 and index['actual_tests_passed'] == 2
    assert {row['backend'] for row in library['worker_jobs']} == {'act', 'diffusion'}
    assert len(library['worker_jobs']) == 4
    assert len({row['sha256'] for row in library['source_archives']}) == 1
    assert len({row['tree_sha256'] for row in library['source_archives']}) == 1
    assert all(row['regular_files'] > 900 for row in library['source_archives'])
    for row in library['worker_jobs']:
        assert row['config']['_execution_commit'] == index['source_commit']
        assert row['status'] == 'completed' and row['kind'] == 'so101_skill_train'
        assert row['training_resume']['completed_steps'] == 8
    for backend in ('ACT', 'DIFFUSION'):
        receipt = read(backend + '_QUALIFICATION')
        assert receipt['actual_supervisor_bootstrap_job_runner_used']
        assert receipt['model_optimizer_losses_Torch_RNG_sampler_exactly_equal']
        assert receipt['own_Python_NumPy_RNG_checkpoint_continuation_verified']
        assert receipt['old_process_gone'] and receipt['old_resident_bytes'] > 0
        assert receipt['paused_at_completed_step'] == 2 and receipt['updates_in_resumed_process'] == 6
        assert receipt['completed_updates'] == 8 and not receipt['task_success_claimed']
    # The original exact run remains tied to its archived release as new queue
    # kinds are added. It is never relabelled as the latest controller graph.
    tree = json.loads((ROOT / 'shared/training/NATIVE6_QUEUE_FINAL_20261026_SOURCE_TREE.json').read_text())
    tree_sha = hashlib.sha256(json.dumps(tree,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    assert all(row['tree_sha256'] == tree_sha for row in library['source_archives'])
    for name, expected in runtime['implementation_sha256'].items():
        assert tree[name] == expected
    assert runtime['cgroup_memory_events'].splitlines() == ['low 0', 'high 0', 'max 0', 'oom 0', 'oom_kill 0', 'oom_group_kill 0']
    assert not index['public_production_queue_tested'] and not index['physical_success']
