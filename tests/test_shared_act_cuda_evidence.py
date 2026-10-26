"""Source-bound records from actual ACT9 CUDA; no optional model allocation."""
import hashlib
import json
from pathlib import Path

from explorer.qualification_bindings import V2_PIPELINES

ROOT=Path(__file__).parents[1]


def read(name):return json.loads((ROOT/'shared/training'/name).read_text())


def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def test_actual_shared_cuda_evidence_preserves_exact_result_and_all_measured_graph_sources():
    index=json.loads((ROOT/'_registry/evidence/gpu9-shared-current-20261026.json').read_text())
    for name,digest in index['archives'].items():
        assert Path(name).name==name and sha(ROOT/'shared/training'/name)==digest
    receipt=read('ACT9_SHARED_CUDA_20261026_QUALIFICATION.json')
    result=read('ACT9_SHARED_CUDA_20261026_RESULT.json')
    before=read('ACT9_SHARED_CUDA_20261026_PREFLIGHT.json')
    binding=read('ACT9_SHARED_CUDA_20261026_BINDING.json')
    audit=read('ACT9_SHARED_CUDA_20261026_RESUME_AUDIT.json')
    assert receipt['passed'] and receipt['optimizer_steps']==8 and receipt['weights_changed']
    assert receipt['result_sha256']==index['qualification_result_sha256']==sha(ROOT/'shared/training/ACT9_SHARED_CUDA_20261026_RESULT.json')
    assert receipt['runtime_identity']==before['runtime']==binding['binding']['binding']['runtime_identity']
    assert receipt['official_implementation_sha256']==before['official_implementation_sha256']
    assert receipt['official_implementation_sha256']==binding['binding']['binding']['official_implementation_sha256']
    # Historical exact graph2 bytes stay evidence for their original source;
    # selection against current graph3 is independently rejected by the binder.
    assert set(receipt['pipeline_source_sha256'])==set(V2_PIPELINES['manipulation_student'])
    recorded=result['provenance']['context']['source_sha256']
    for name,digest in receipt['pipeline_source_sha256'].items():
        assert any(source.endswith('/'+name) and measured==digest for source,measured in recorded.items())
    assert receipt['pipeline_graph_version']==2 and len(receipt['pipeline_source_sha256'])==14
    assert result['student']['official_saved_processors_executed'] and result['student']['parameter_device']=='cuda:0'
    assert receipt['reload_max_abs_error']==0 and not receipt['tf32_enabled']
    assert receipt['cgroup_memory_events'].splitlines()==['low 0','high 0','max 0','oom 0','oom_kill 0','oom_group_kill 0']
    assert result['evaluation']['episodes'][0]['native_replay']['within_1e_minus_9']
    assert receipt['independent_development_successes']==0 and not index['training_resume_process_restart_tested']
    assert not receipt['physical_skill_qualified'] and not receipt['other_actor_shapes_qualified']
    signature=hashlib.sha256(json.dumps(audit['signature_metadata'],sort_keys=True,allow_nan=False).encode()).hexdigest()
    assert signature==audit['resume_signature']==result['student']['training_resume']['signature']
    assert audit['visual_convolution_weights_changed_max_abs']['model.backbone.conv1.weight']>0
    assert audit['extraction_only_no_optimizer']
