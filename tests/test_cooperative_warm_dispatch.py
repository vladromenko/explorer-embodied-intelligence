"""Exercise only typed worker dispatch/terminal fencing; no model or device IO."""
import json

import pytest

from explorer.store import Store


@pytest.mark.parametrize('cancel_after_compute',[False,True])
def test_warm_job_dispatches_existing_worker_and_late_result_cannot_replace_cancel(tmp_path,monkeypatch,cancel_after_compute):
    from explorer import job_runner,cooperative_policy_job
    store=Store(tmp_path/'state');job=store.create_job('cooperative_policy_warm','05',dict(schema='SOFTWARE_DISPATCH_FIXTURE'))
    store.update_job(job['id'],status='running');calls=[]
    def worker(selected,actual,config):
        assert actual['id']==job['id'] and actual['kind']=='cooperative_policy_warm'
        calls.append(actual['id'])
        if cancel_after_compute:selected.request_job_operation(job['id'],'cancel')
        return dict(compute_finished=True,hardware_commands_dispatched=False,software_dispatch_fixture=True)
    monkeypatch.setattr(cooperative_policy_job,'run_cooperative_policy_session',worker)
    monkeypatch.setattr(job_runner,'load',lambda:dict(data_dir=str(store.root),nodes=[]))
    monkeypatch.delenv('EXPLORER_WORKER_LEASE_NONCE',raising=False)
    job_runner.main(job['id'])
    assert calls==[job['id']]
    assert store.job(job['id'])['status']==('cancelled' if cancel_after_compute else 'completed')
    assert json.loads((store.root/'jobs'/job['id']/'result.json').read_text())['hardware_commands_dispatched'] is False
