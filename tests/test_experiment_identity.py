"""Canonical numeric routes cannot silently reinterpret historical identities."""
import pytest
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
