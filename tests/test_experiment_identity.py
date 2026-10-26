"""Canonical numeric routes cannot silently reinterpret historical identities."""
import pytest
from pathlib import Path
import hashlib
from explorer_shared.experiments import canonical_experiment,experiment_id,experiment_identity,experiment_slug,registry_document


def test_swapped_ids_require_explicit_epoch_and_semantic_slug_is_stable():
    assert canonical_experiment('04',numbering_version=1)=='06'
    assert canonical_experiment('06',numbering_version=1)=='04'
    assert canonical_experiment('04',numbering_version=2)=='04'
    assert canonical_experiment('06',numbering_version=2)=='06'
    assert canonical_experiment('personal',numbering_version=1)=='04'
    for version in (1,2):
        assert experiment_slug('so101_source_learning',numbering_version=version)=='so101_source_learning'
        assert canonical_experiment('cross_embodiment_transfer',numbering_version=version)=='06'
    with pytest.raises(TypeError):canonical_experiment('04')
    with pytest.raises(ValueError):canonical_experiment('04',numbering_version=True)
    with pytest.raises(ValueError):canonical_experiment('04',numbering_version=3)


def test_registry_is_one_complete_canonical_mapping_and_copies_do_not_mutate_it():
    registry=registry_document();assert [row['id'] for row in registry['projects']]==['01','02','03','04','05','06']
    source=next(row for row in registry['projects'] if row['slug']=='so101_source_learning')
    assert source['id']=='04' and source['directory']=='04-so101-source-learning'
    assert experiment_identity('cross_embodiment_transfer')==dict(project_id='06',experiment_slug='cross_embodiment_transfer',experiment_numbering_version=2)
    source['id']='wrong';assert experiment_id('so101_source_learning')=='04'


def test_canonical_directories_and_deprecated_asset_paths_share_one_unchanged_source():
    root=Path(__file__).resolve().parents[1]
    assert (root/'04-so101-source-learning').is_dir() and (root/'06-cross-embodiment-transfer').is_dir()
    assert (root/'04-cross-embodiment-transfer').is_symlink()
    assert (root/'learning/so101-personal-lab').resolve()==root/'04-so101-source-learning'
    asset=root/'shared/robotics/so101/so101_new_calib.urdf'
    for path in (root/'04-so101-source-learning/configs/so101/so101_new_calib.urdf',root/'06-cross-embodiment-transfer/configs/so101/so101_new_calib.urdf',root/'04-cross-embodiment-transfer/configs/so101/so101_new_calib.urdf'):
        assert path.resolve()==asset
        assert hashlib.sha256(path.read_bytes()).digest()==hashlib.sha256(asset.read_bytes()).digest()
