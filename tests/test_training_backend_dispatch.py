"""Backend selection changes runtime only after source and option validation."""
import pytest

from explorer.training_options import so101_training_settings
from explorer.scheduler import executable_for
from explorer.store import Store
from explorer_shared.learning.diffusion_backend import PROFILE_ID


def test_default_act_and_explicit_diffusion_have_separate_bounded_options():
    act=so101_training_settings({});assert act['backend']=='act' and act['act_steps']==120 and act['chunk_size']==16
    dp=so101_training_settings(dict(backend='diffusion',optimizer_steps=8))
    assert dp==dict(backend='diffusion',profile_id=PROFILE_ID,optimizer_steps=8,batch_size=2,image_size=64,seed=0,device='cpu')


@pytest.mark.parametrize('body',[
    dict(backend='smolvla'),dict(backend='diffusion',device='cuda'),dict(backend='diffusion',batch_size=3),
    dict(backend='diffusion',image_size=32),dict(backend='diffusion',chunk_size=4),dict(backend='diffusion',profile_id='generic'),
    dict(backend='diffusion',act_steps=4,optimizer_steps=8),dict(optimizer_steps=True),dict(seed=-1),
    dict(executable='/tmp/untrusted'),dict(_policy_runtime_binding_sha256='a'*64),dict(source_numbering_version=1)])
def test_unqualified_or_unbounded_backend_settings_fail_before_work(body):
    with pytest.raises(ValueError):so101_training_settings(body)


def test_diffusion_executable_is_server_owned_and_binding_cannot_change_on_resume(tmp_path,monkeypatch):
    from explorer import policy_runtime
    store=Store(tmp_path);job=store.create_job('so101_skill_train','04',dict(backend='diffusion'))
    calls=[]
    def select(release):
        calls.append(release);return dict(executable='/qualified/persistent/bin/python',binding_sha256='a'*64)
    monkeypatch.setattr(policy_runtime,'qualified_diffusion_runtime',select)
    assert executable_for(job,'immutable-release',store)=='/qualified/persistent/bin/python'
    current=store.job(job['id'])
    assert current['config']['_policy_runtime_binding_sha256']=='a'*64 and calls==['immutable-release']
    assert executable_for(current,'immutable-release',store)=='/qualified/persistent/bin/python'
    monkeypatch.setattr(policy_runtime,'qualified_diffusion_runtime',lambda _:dict(executable='/changed/bin/python',binding_sha256='b'*64))
    with pytest.raises(ValueError,match='Pinned policy runtime'):executable_for(current,'immutable-release',store)
    with pytest.raises(ValueError,match='silently change'):store.pin_job_policy_binding(job['id'],'b'*64)
