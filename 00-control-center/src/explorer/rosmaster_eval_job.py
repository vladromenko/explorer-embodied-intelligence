"""ENGINEERING_GLUE: isolated saved native9 model DEV evaluation, no dispatch.

Loads immutable CPU model/source snapshots. No optimizer, sealed samples,
simulator, Source network connection or motor operation is constructed.
"""
import json
from pathlib import Path

from explorer_shared.contracts import canonical,digest
from .rosmaster_learning import evaluation_request,_sha


def run_rosmaster_evaluation(store,job,*,loader=None,evaluator=None):
    config=job.get('config') or {}
    expected=evaluation_request(store,config.get('skill_id'),config.get('skill_version'),config.get('model_job_id'))
    if job.get('kind')!='rosmaster_skill_eval' or job.get('project_id')!='02' or config!=expected:
        raise ValueError('Exact queued saved-model DEV evaluation binding required')
    def guard():
        current=store.job(job['id'])
        if current and current['status'] in ('cancel_requested','cancelled'):raise RuntimeError('Offline evaluation cancelled before load/next sample/result')
    guard();training=store.job(config['model_job_id']);result=training['result'];path=Path(result['model_manifest'])
    model=json.loads(path.read_bytes())
    from explorer_shared.learning.rosmaster_skill_pipeline import CanonicalCorpus,FusedChunks,RosmasterSkillPolicy,offline_metrics
    corpus=CanonicalCorpus(result['source_manifest_path'])
    if (model.get('train_original_episode_ids')!=[row['descriptor']['source_episode_id'] for row in corpus.train] or
            model.get('development_original_episode_ids')!=[row['descriptor']['source_episode_id'] for row in corpus.development] or
            model.get('preprocessing',{}).get('normalizer')!=corpus.normalizer.document()):
        raise ValueError('Saved model train/DEV/preprocessing differs from its immutable canonical cohort')
    guard();policy=(loader or RosmasterSkillPolicy)(path);guard()
    options=model['preprocessing'];chunks=FusedChunks(corpus,corpus.development,policy.policy.config.chunk_size,options['image_size'])
    if evaluator is None:
        # Source/checkpoint guards before every evaluated original sample.
        class Guarded:
            indices=chunks.indices;episodes=chunks.episodes
            def __len__(self):return len(chunks)
            def __getitem__(self,index):guard();return chunks[index]
        evaluation=offline_metrics(policy.policy,corpus,Guarded(),policy.phase_model,'cpu')
    else:evaluation=evaluator(policy,corpus,chunks,guard)
    guard();corpus.recheck()
    if _sha(path)!=config['model_manifest_sha256']:raise ValueError('Saved model metadata changed during evaluation')
    checkpoint=Path(result['checkpoint_dir'])
    for name,checksum in result['checkpoint_sha256'].items():
        if _sha(checkpoint/name)!=checksum:raise ValueError('Evaluation changed or lost the original weights')
    if evaluation.get('sealed_test_used') is not False or evaluation.get('learned_policy_rollout_executed') is not False:
        raise ValueError('Only independent DEV action diagnostics are supported')
    report={'schema':'explorer_rosmaster_saved_model_evaluation_v1',**expected,'evaluation':evaluation,
        'evaluated_original_episode_ids':[row['descriptor']['source_episode_id'] for row in corpus.development],
        'weights_unchanged':True,'optimizer_steps_executed':0,'source_network_requests_sent':0,
        'physical_control_requests_sent':0,'dispatch_performed':False,'independent_full_task_success':False}
    folder=store.root/'jobs'/job['id'];folder.mkdir(parents=True,exist_ok=True)
    from explorer_shared.cooperative_recording05 import write_original
    raw=canonical(report);artifact=folder/'offline-evaluation.json'
    if artifact.exists():
        if artifact.read_bytes()!=raw:raise ValueError('Immutable offline evaluation result changed')
    else:write_original(artifact,raw)
    return {**report,'evaluation_artifact':str(artifact),'evaluation_artifact_sha256':digest(report)}
