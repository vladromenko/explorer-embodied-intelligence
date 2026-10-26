"""Metadata only: stale finish never seals a later original source episode."""
import json
from types import SimpleNamespace

import pytest

from explorer_shared.so101_station import SO101Station


def test_finish_identity_fenced_at_original_record_transition_and_old_abi_preserved(tmp_path):
    config=tmp_path/'configuration.json';config.write_text(json.dumps({'roles':{}}))
    source=SO101Station(config,tmp_path,SimpleNamespace())
    source.mode='teleoperation'
    first=source._start_record({'project_id':'04'})['episode_id']
    with pytest.raises(ValueError,match='identity changed'):
        source.command('record/finish',{'episode_id':'another-source','outcome':'unknown'})
    assert source.record['episode_id']==first
    saved=source.command('record/finish',{'episode_id':first,'outcome':'unknown'})
    assert saved['episode_id']==first and source.status()['recording_identity_fence']
    assert source.command('record/finish',{'episode_id':first,'outcome':'unknown'})==saved
    second=source._start_record({'project_id':'04'})['episode_id']
    with pytest.raises(ValueError,match='identity changed'):
        source.command('record/finish',{'recording_id':first,'outcome':'success'})
    assert source.record['episode_id']==second
    assert source.command('record/finish',{'outcome':'unknown'})['episode_id']==second
