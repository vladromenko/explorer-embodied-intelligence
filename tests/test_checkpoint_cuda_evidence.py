"""Current ACT9 measured graph3 evidence; no GPU/model allocation in this test."""
import hashlib
import json
from pathlib import Path

from explorer.qualification_bindings import PIPELINES

ROOT = Path(__file__).parents[1]


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def test_actual_current_checkpoint_cuda_evidence_is_narrow_and_hash_bound():
    index = json.loads((ROOT / '_registry/evidence/gpu9-checkpoint-current-20261026.json').read_text())
    for name, digest in index['archives'].items():
        assert Path(name).name == name and sha(ROOT / 'shared/training' / name) == digest
    def read(suffix):
        return json.loads((ROOT / 'shared/training' / ('ACT9_SHARED_CUDA_QUEUE_20261026_' + suffix + '.json')).read_text())
    receipt, result, before, binding = (read(name) for name in ('QUALIFICATION', 'RESULT', 'PREFLIGHT', 'BINDING'))
    assert receipt['result_sha256'] == index['qualification_result_sha256'] == sha(ROOT / 'shared/training/ACT9_SHARED_CUDA_QUEUE_20261026_RESULT.json')
    assert receipt['pipeline_graph_version'] == binding['binding']['binding']['pipeline_graph_version'] == 3
    assert set(receipt['pipeline_source_sha256']) == set(PIPELINES['manipulation_student'])
    assert len(receipt['pipeline_source_sha256']) == 15
    assert receipt['runtime_identity'] == before['runtime'] == binding['binding']['binding']['runtime_identity']
    assert receipt['official_implementation_sha256'] == before['official_implementation_sha256']
    for name, digest in receipt['pipeline_source_sha256'].items():
        assert sha(ROOT / name) == digest
        assert any(path.endswith('/' + name) and measured == digest for path, measured in result['provenance']['context']['source_sha256'].items())
    assert receipt['qualified_tensor_profile'] == dict(device='cuda', precision='float32', state_dimension=9,
        action_dimension=9, image_size=64, batch_size=2, chunk_size=4)
    assert receipt['optimizer_steps'] == 8 and receipt['weights_changed']
    assert receipt['official_saved_processors_executed'] and receipt['reload_max_abs_error'] == 0
    assert result['evaluation']['episodes'][0]['native_replay']['within_1e_minus_9']
    assert receipt['cgroup_memory_events'].splitlines() == ['low 0', 'high 0', 'max 0', 'oom 0', 'oom_kill 0', 'oom_group_kill 0']
    assert receipt['independent_development_successes'] == 0 and not receipt['physical_skill_qualified']
    assert not receipt['other_actor_shapes_qualified'] and not index['training_resume_process_restart_tested']
