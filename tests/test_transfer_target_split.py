"""Explicit MOCK trials exercise partition accounting only, never task success."""
import pytest
from explorer_shared.skill_transfer import record_target_trial,load_source_trace,target_trial_split
from explorer_shared.transfer_mapping import TransferBlocked,sha
from test_skill_transfer import source_trace,mapper_fixture,MockTarget


def split(partition):return dict(partition=partition,episode_id='new-target-episode',scene_id='new-target-scene',session_id='new-target-session',group_id='new-target-group')


@pytest.mark.parametrize('source_partition,target_partition,expected',[('train',None,False),('train','development',False),('train','sealed_test',False),('development','train',False),('train','train',True)])
def test_source_train_never_admits_unknown_or_heldout_target_to_optimizer(tmp_path,source_partition,target_partition,expected):
    from explorer_shared.contracts import canonical
    path,document,_=source_trace(tmp_path);document['split']['partition']=source_partition;path.write_bytes(canonical(document))
    bundle=load_source_trace(path,sha(path.read_bytes()));spec=dict(skill_id=document['skill_id'],version=2,stages=[dict(id='place_verify')])
    result=record_target_trial(bundle,mapper_fixture(),MockTarget(),tmp_path/'target',target_version=2,target_skill_spec=spec,seed=8,target_split=None if target_partition is None else split(target_partition))
    assert result['optimizer_partition_allowed'] is expected
    assert result['target_split']['partition']==(target_partition or 'unknown')
    assert result['source_split']['partition']==source_partition
    assert not result['eligible_as_simulation_expert'] and not result['full_target_simulation_success']


@pytest.mark.parametrize('value',[{'partition':'train'},dict(split('train'),group_id=''),dict(split('train'),session_id=True),dict(split('train'),partition='test'),dict(split('train'),extra='forged')])
def test_incomplete_or_forged_target_partition_is_not_filled_from_source(value):
    with pytest.raises(TransferBlocked,match='EXPLICIT_TARGET'):target_trial_split(value)
