"""Per-response metadata sharing, both native consumers; no cached authority."""
from copy import deepcopy
from pathlib import Path

import pytest

from explorer import skill_workflow as mobile,lab_skill_workflow as lab
from test_cooperative_queued_shadows import configured


def test_multiple_sessions_share_native_metadata_only_within_one_response(tmp_path,monkeypatch):
    root,store,calls,client,spec,row,models=configured(tmp_path)
    try:
        for index in range(4):
            root.workflow.create_session(spec['skill_id'],1,{'scene_id':'extra-'+str(index),'object_instance_id':'block-'+str(index),
                'split':'train','giver_input_mode':'keyboard','receiver_input_mode':'leader'})
        counts={'giver':0,'receiver':0,'model_tables':0};old9,old6,oldmodels=mobile.workflow,lab.state_for_skill,root._read_models_for
        def nine(*args,**kwargs):counts['giver']+=1;return old9(*args,**kwargs)
        def six(*args,**kwargs):counts['receiver']+=1;return old6(*args,**kwargs)
        def model(*args,**kwargs):counts['model_tables']+=1;return oldmodels(*args,**kwargs)
        monkeypatch.setattr(mobile,'workflow',nine);monkeypatch.setattr(lab,'state_for_skill',six);monkeypatch.setattr(root,'_read_models_for',model)
        first=root.snapshot(row['session_id']);assert len(first['workbench']['sessions'])==5
        assert counts=={'giver':1,'receiver':1,'model_tables':2}
        assert first['queued_native_shadow_readiness']['ready'] and not calls
        root.snapshot(row['session_id']);assert counts=={'giver':2,'receiver':2,'model_tables':4}
        # Explicit non-snapshot inspection re-runs the original readers; no
        # response cache may leak into native command/train/review preflights.
        root._inspect_skill('giver',spec['subskills']['giver']);root._inspect_skill('receiver',spec['subskills']['receiver'])
        assert counts=={'giver':3,'receiver':3,'model_tables':6} and not calls
    finally:root.close();client.close()


@pytest.mark.parametrize('role',['giver','receiver'])
def test_next_response_rejects_changed_native_checkpoint_instead_of_reusing_prior_ready(tmp_path,role):
    root,store,calls,client,spec,row,models=configured(tmp_path)
    try:
        before=root.snapshot(row['session_id']);assert before['queued_native_shadow_readiness']['ready']
        job=store.job(models[role]);path=Path(job['result']['checkpoint_dir'])/'dummy-weight';path.write_bytes(b'EXPLICIT_CHANGED_SOFTWARE_FIXTURE')
        after=root.snapshot(row['session_id']);assert not after['queued_native_shadow_readiness']['ready']
        assert models[role] in after['model_source_errors'] and not calls
        assert before['queued_native_shadow_readiness']['ready']  # returned data is not a shared mutable cache
    finally:root.close();client.close()


def test_metadata_cache_is_cleared_after_snapshot_failure(tmp_path,monkeypatch):
    root,store,calls,client,spec,row,models=configured(tmp_path)
    try:
        original=root._snapshot
        def fail(identity):
            root._inspect_skill('giver',spec['subskills']['giver'])
            raise OSError('EXPLICIT failed response before publication')
        monkeypatch.setattr(root,'_snapshot',fail)
        with pytest.raises(OSError,match='failed response'):root.snapshot(row['session_id'])
        path=Path(store.job(models['giver'])['result']['checkpoint_dir'])/'dummy-weight';path.write_bytes(b'CHANGED AFTER FAILED RESPONSE')
        monkeypatch.setattr(root,'_snapshot',original)
        after=root.snapshot(row['session_id']);assert not after['queued_native_shadow_readiness']['ready'] and not calls
    finally:root.close();client.close()
