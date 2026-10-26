"""ENGINEERING_GLUE: a new worker may act only after its scheduler lease is durable.

This closes the fork→PID-persistence crash window. No hardware or learner is
imported until the same jobs-table claim/PID/start/boot/nonce is verified.
"""
import sys
import time

from .config import load
from .store import Store
from explorer_shared.learning.checkpoint_control import atomic_json,worker_identity

PAUSED_BEFORE_WORK_EXIT=77
LEASE_NOT_ADMITTED_EXIT=78


def prework_guard(store,job_id,nonce=None):
    identity=worker_identity();state=store.admit_worker_entry(job_id,nonce,identity)
    if state in ('cancelled','terminal'):return False
    if state=='paused':
        atomic_json(store.root/'jobs'/job_id/'worker-not-started.json',dict(worker=identity,launch_nonce=nonce,phase='paused_before_work',physical_io=False))
        raise SystemExit(PAUSED_BEFORE_WORK_EXIT)
    return True


def main(job_id,nonce):
    config=load();store=Store(config['data_dir']);began=time.monotonic();identity=worker_identity()
    while time.monotonic()-began<5:
        job=store.job(job_id)
        if job is None:raise SystemExit(LEASE_NOT_ADMITTED_EXIT)
        runtime=store.schedule_status(job_id)['runtime']
        if runtime.get('launch_nonce')==nonce and runtime.get('worker')==identity:
            if job['status']=='cancel_requested':
                prework_guard(store,job_id,nonce);return
            if job['status']=='pause_requested':
                prework_guard(store,job_id,nonce);raise SystemExit(PAUSED_BEFORE_WORK_EXIT)
            if job['status']=='running':
                store.update_schedule_runtime(job_id,phase='running',bootstrap_admitted=True)
                if not prework_guard(store,job_id,nonce):return
                from .job_runner import main as run_job
                run_job(job_id);return
        time.sleep(.02)
    raise SystemExit(LEASE_NOT_ADMITTED_EXIT)


if __name__=='__main__':main(sys.argv[1],sys.argv[2])
