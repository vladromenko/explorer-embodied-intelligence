"""Opt-in official native9 dataset export on NEW approved generated originals."""
import json
import os
from pathlib import Path
import uuid

import pytest

from explorer.store import Store
from explorer import transfer_geometry as geometry,transfer_calibration as calibration,transfer_target as target
from explorer_shared.learning.rosmaster_skill_pipeline import CanonicalCorpus
from native02_queue_helpers import sha
from test_transfer_target_original_cohort import original_target_cohort

ROOT=Path(__file__).parents[1]


@pytest.mark.skipif(os.environ.get('EXPLORER_RUN_TRANSFER06_NATIVE_TARGET_EXPORT')!='1',reason='Official native9 export of NEW generated approved originals; no optimizer/physical proof')
def test_actual_native06_originals_keep_approval_lineage_through_official_native9_export(tmp_path,monkeypatch):
    root=Path(os.environ['EXPLORER_TRANSFER06_SOURCE_STATE']).resolve()
    assert root.is_relative_to(Path('/mnt/explorer-data/explorer-work'))
    assert not root.is_relative_to((Path.home()/'.local/share/explorer/state').resolve())
    store=Store(root);source_id=os.environ['EXPLORER_TRANSFER06_SOURCE_MODEL']
    assert not [j for j in store.queue_jobs() if j['status'] not in ('completed','failed','cancelled')]
    model=geometry.admitted_model(store,source_id);prepared,_=geometry.build_geometry(model,geometry.saved_simulation_calibration(model))
    wizard=calibration.create_session(store,dict(skill_id=model['manifest']['skill_id'],source_version=1,target_version=9,
        domain='physical',request_id=str(uuid.uuid4())),audit=prepared['audit'],source_model_binding=prepared['source_model_binding'],authenticated_actor='SOFTWARE-FIXTURE-OWNER')
    source_originals={str(root/'jobs'/source_id/'result.json'):sha(root/'jobs'/source_id/'result.json')}
    source_originals.update({str(Path(model['checkpoint_path'])/name):expected for name,expected in model['checkpoint_sha256'].items()})
    _,_,path,imports,originals=original_target_cohort(tmp_path/'original-cohort',monkeypatch,store=store,wizard=wizard)
    corpus=CanonicalCorpus(path);assert len(corpus.train)==3 and len(corpus.development)==1 and len(corpus.sealed_ids)==1
    assert all(len(row['frames'])==7 for row in corpus.episodes)
    dataset=store.create_job(target.DATASET_KIND,'06',dict(inputs='GENERATED_NATIVE_TAP_NOT_PHYSICAL_EXPERTS'))
    store.complete_job(dataset['id'],dict(dataset_manifest=str(path),dataset_manifest_sha256=sha(path)))
    # Existing public budgets5/10/25 must stay insufficient: three originals are
    # not silently inflated. This actual core qualifier uses the same parent
    # directly for official interchange/tensor roundtrip; no fitting bypass.
    assert not target.target_dataset(store,wizard['wizard_id'],dataset['id'])[2].readiness()['budgets'][0]['ready']
    # No optimizer bypass for an undersized approved cohort: preserve this as
    # native-writer/export/admission proof until five true TRAIN originals exist.
    from explorer_shared.learning.rosmaster_lerobot_dataset import export_official_dataset,OfficialFusedChunks
    train_folder,train_index=export_official_dataset(corpus,tmp_path/'official-train',image_size=32,partition='train')
    dev_folder,dev_index=export_official_dataset(corpus,tmp_path/'official-development',image_size=32,partition='development')
    train=OfficialFusedChunks(corpus,corpus.train,2,32,train_folder,train_index)
    assert train[0]['action'].shape==(2,9) and train[0]['observation.state'].shape==(106,)
    assert train_index['original_demo_count']==3 and dev_index['original_demo_count']==1
    assert all(sha(name)==expected for name,expected in dict(originals['originals_sha256'],**source_originals).items())
    receipt=dict(schema='explorer06_actual_generated_native_original_official_export_v1',source_model_job_id=source_id,
        source_commit=__import__('subprocess').check_output(['git','rev-parse','HEAD'],text=True).strip(),
        inputs='GENERATED_FAKE_NATIVE_TAP_AND_REAL_FILE_WRITER_NOT_PHYSICAL_EXPERTS',real_experts=0,
        original_counts=dict(train=3,development=1,sealed=1),original_manifests_archives_unchanged=True,
        import_job_ids=imports,dataset_job_id=dataset['id'],dataset_manifest=str(path),dataset_manifest_sha256=sha(path),
        official_train_index=train_index,official_development_index=dev_index,optimizer_steps_executed=0,
        budget5_correctly_blocked=True,actual_native_commands=0,physical_acceptance=False)
    (tmp_path/'qualification.json').write_text(json.dumps(receipt,indent=2)+'\n')
