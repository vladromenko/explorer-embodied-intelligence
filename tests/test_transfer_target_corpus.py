"""Generated source-bound originals: actual common reader, no optimizer/robot IO."""
from copy import deepcopy
import hashlib
import io
import json
from pathlib import Path
import uuid

import numpy as np
from PIL import Image
import pytest

from explorer_shared.contracts import ACTION_NAMES, canonical, digest
from explorer_shared.embodied_episode import inspect_embodied_episode
from explorer_shared.experiments import experiment_identity
from explorer_shared.learning.rosmaster_skill_features import PipelineBlocked
from explorer_shared.learning.rosmaster_skill_pipeline import CanonicalCorpus
from explorer_shared.learning.transfer_target_corpus import TransferTargetCorpus
from test_embodied_episode import command_estimate_facts, package


def generated_target_corpus(tmp_path, *, train_count=25, project='cross_embodiment_transfer', source_binding=None, source_spec=None):
    """Generate06 identity BEFORE sealing originals, never relabel old datasets."""
    root = tmp_path/'generated-target-originals';root.mkdir(parents=True)
    skill_id = str(uuid.uuid4())
    binding = dict(model_job_id='1'*32, skill_id=skill_id, skill_version=1,
        **{name: 'a'*64 for name in ('spec_sha256', 'aggregate_manifest_sha256',
           'training_receipt_sha256', 'checkpoint_content_sha256',
           'source_calibration_sha256', 'source_urdf_sha256')})
    if source_binding is not None:
        binding=deepcopy(source_binding);skill_id=binding['skill_id']
    spec = dict(skill_id=skill_id, version=2, title='GENERATED target9 software fixture',
                stages=[dict(id=name) for name in ('approach','grasp')])
    if source_spec is not None:
        spec=deepcopy(source_spec);spec.update(skill_id=skill_id,version=2,title='GENERATED target9 software fixture')
    phases=[row['id'] for row in spec['stages']]
    inventory, descriptors, originals = {}, [], {}
    for index in range(train_count+1):
        partition = 'train' if index < train_count else 'development'
        original = 'GENERATED-'+partition+'-'+str(index)
        facts, _ = command_estimate_facts()
        facts.update(experiment_identity(project), episode_id=original, run_id='run-'+original,
                     episode_scope='whole_episode',source_kind='simulation')
        facts['samples'][0]['action']['stage']='simulated_dispatch'
        facts['samples'][0]['action']['delivery'].update(level='simulated_dispatch',observer_kind='simulation_step')
        facts['skill'] = dict(id=skill_id, version=2, variant_id='variant-'+original,
                             session_id='session-'+original)
        facts['split'].update(partition=partition, episode_id=original,
            scene_id='scene-'+original, session_id='session-'+original,
            object_instance_id='object-'+original, group_id='group-'+original)
        pixels=np.full((32,32,3), index+1, np.uint8);pixels[:,::2,1]=20+index
        encoded=io.BytesIO();Image.fromarray(pixels).save(encoded,format='PNG')
        assets={'rgb.png': encoded.getvalue()}
        first=deepcopy(facts['samples'][0]);facts['samples']=[]
        for position, phase in enumerate(phases):
            sample=deepcopy(first);sample['sample_id']='sample-'+str(position)
            for record in sample['channels'].values():
                if isinstance(record.get('stamp'),dict):record['stamp']['monotonic_ns']=10+position*30
            sample['channels']['camera_rgb']['value'].update(source_file='rgb.png',
                sha256=hashlib.sha256(assets['rgb.png']).hexdigest(),encoding='png',width=32,height=32,
                camera_id='GENERATED-target-camera',frame_id=original+'-'+str(position))
            sample['action']['stamp']['monotonic_ns']=20+position*30
            sample['action']['delivery']['stamp']['monotonic_ns']=20+position*30
            facts['samples'].append(sample)
        facts['phases']=[dict(id=name,start_sample=n,end_sample=n,
            transition_reason='GENERATED independent fixture outcome',recovery_of=None)
            for n,name in enumerate(phases)]
        facts['outcomes']['per_phase']={name:deepcopy(facts['outcomes']['end_to_end']) for name in phases}
        facts['identity']={key:deepcopy(facts[key]) for key in ('episode_id','run_id','project_id',
            'experiment_slug','experiment_numbering_version','source_kind','skill','episode_scope','action_contract')}
        envelope,payloads=package(facts,assets);report=inspect_embodied_episode(envelope,payloads)
        assert report['admitted_for_dataset'],report['missing_facts']
        report['envelope']=deepcopy(envelope)
        folder=root/original;folder.mkdir();mapping={}
        for name,raw in dict(payloads,**{'__envelope__/original-document.json':canonical(envelope)}).items():
            path=folder/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(raw)
            relative=str(path.relative_to(root));inventory[relative]=hashlib.sha256(raw).hexdigest()
            mapping[name]=relative;originals[str(path)]=inventory[relative]
        admission=folder/'canonical-admission.json';admission.write_bytes(canonical(report))
        relative=str(admission.relative_to(root));inventory[relative]=hashlib.sha256(admission.read_bytes()).hexdigest()
        descriptors.append(dict(source_episode_id=original,split=partition,skill_id=skill_id,
            skill_version=2,variant_id='variant-'+original,source_project_id=facts['project_id'],
            episode_scope='whole_episode',target_phase_ids=list(phases),
            canonical_admission_file=relative,canonical_admission_sha256=inventory[relative],source_artifact_map=mapping))
    manifest=dict(schema='explorer_unified_embodied_skill_dataset_v1',skill_id=skill_id,
        skill_version=2,skill_spec=spec,skill_spec_sha256=digest(spec),
        action_contract_id='rosmaster_joint9_position6_velocity3_v1',action_names=list(ACTION_NAMES),
        simultaneous_9d=True,source_files_sha256=inventory,episodes=descriptors,
        sealed_sources_excluded=[dict(original_episode_id='GENERATED-sealed-no-payload')])
    path=root/'aggregate.json';path.write_bytes(canonical(manifest))
    return path,binding,originals


def test_target06_reuses_shared_reader_and_counts_whole_originals_in_nested_budgets(tmp_path):
    path,binding,originals=generated_target_corpus(tmp_path)
    adapter=TransferTargetCorpus(path,source_model_binding=binding,target_version=2,target_domain='simulation')
    ready=adapter.readiness()
    assert ready['train_original_demonstrations']==25 and all(row['ready'] for row in ready['budgets'])
    assert not ready['source_RGB_pixel_overlap_checked']  # absent source pixels stay UNKNOWN
    assert not ready['source_action_head_reused'] and not ready['physical_execution_authorized']
    selected=[]
    for budget in (5,10,25):
        reference=adapter.selection_reference(budget);assert not Path(reference['dataset_manifest']).exists()
        receipt=adapter.save_selection(budget);assert receipt==reference==adapter.save_selection(budget)
        corpus=CanonicalCorpus(receipt['dataset_manifest']);selected.append(set(receipt['selection']['training_original_episode_ids']))
        assert len(corpus.train)==budget and len(corpus.development)==1 and corpus.sealed_ids==['GENERATED-sealed-no-payload']
        assert all(row['report']['experiment_identity']['canonical_project_id']=='06' for row in corpus.episodes)
        assert corpus.normalizer.document()['fit_partition']=='train'
        assert all(frame['action'].shape==(9,) for row in corpus.episodes for frame in row['frames'])
        assert all(not frame['masks'][9:15].any() for row in corpus.episodes for frame in row['frames'])
    assert selected[0]<selected[1]<selected[2]
    assert all(hashlib.sha256(Path(name).read_bytes()).hexdigest()==expected for name,expected in originals.items())


def test_small_cohort_is_not_promoted_by_frame_count_and_sealed_never_fills_budget(tmp_path):
    path,binding,_=generated_target_corpus(tmp_path,train_count=3)
    adapter=TransferTargetCorpus(path,source_model_binding=binding,target_version=2,target_domain='simulation')
    assert adapter.readiness()['budgets'][0]==dict(demonstrations=5,ready=False,missing_original_demonstrations=2)
    with pytest.raises(PipelineBlocked,match='INSUFFICIENT_ORIGINAL'):adapter.selection(5)
    for budget in (True,0,3,6,26,'5'):
        with pytest.raises(PipelineBlocked,match='BUDGET_MUST'):adapter.selection(budget)


def test_old02_consumer_remains_admitted_by_shared_reader_but_cannot_be_renamed06(tmp_path):
    path,binding,_=generated_target_corpus(tmp_path,train_count=5,project='rosmaster_keyboard_learning')
    assert len(CanonicalCorpus(path).train)==5
    with pytest.raises(PipelineBlocked,match='ORIGINAL_TARGET06'):
        TransferTargetCorpus(path,source_model_binding=binding,target_version=2,target_domain='simulation')


def test_source_target_binding_domain_pixels_and_late_original_drift_are_fenced(tmp_path):
    path,binding,_=generated_target_corpus(tmp_path,train_count=5)
    kwargs=dict(source_model_binding=binding,target_version=2,target_domain='simulation')
    for overrides,reason in ((dict(target_version=1),'ADVANCING'),(dict(target_version=3),'UUID_OR_VERSION'),
                             (dict(target_domain='physical'),'DOMAIN_MIXING'),
                             (dict(source_model_binding=dict(binding,skill_id=str(uuid.uuid4()))),'UUID_OR_VERSION')):
        with pytest.raises(PipelineBlocked,match=reason):TransferTargetCorpus(path,**dict(kwargs,**overrides))
    corpus=CanonicalCorpus(path);episode=corpus.train[0]
    image=Image.open(io.BytesIO(corpus.read(episode,'rgb.png'))).convert('RGB')
    with pytest.raises(PipelineBlocked,match='SOURCE_RGB_CANNOT'):
        TransferTargetCorpus(path,**kwargs,source_rgb_pixel_sha256=[hashlib.sha256(np.asarray(image).tobytes()).hexdigest()])
    adapter=TransferTargetCorpus(path,**kwargs)
    next(iter(episode['paths'].values())).write_bytes(b'late changed original')
    with pytest.raises(PipelineBlocked,match='SOURCE_CHANGED'):adapter.save_selection(5)
    assert not list(path.parent.glob('transfer-target-*.json'))
