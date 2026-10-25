"""ENGINEERING_GLUE: independent durable resource scheduler; no robot I/O."""
import fcntl
import os
import signal
import subprocess
import sys
import time
import psutil


def supervise(config, store):
    lock = (store.root / 'scheduler.lock').open('w')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    processes = {}
    for job in store.jobs(10000):
        if job['status'] in ('running', 'pause_requested', 'resume_requested'):
            store.update_job(job['id'], status='queued', error='Supervisor restarted; resuming from persisted artifacts')
    while True:
        jobs = list(reversed(store.jobs(10000)))
        for job in jobs:
            job_id = job['id']
            process = processes.get(job_id)
            if process and process.poll() is not None:
                processes.pop(job_id)
                current = store.job(job_id)
                if current['status'] not in ('completed', 'failed', 'cancelled'):
                    store.update_job(job_id, status='failed', error='Worker exited without a completed result')
            elif process and job['status'] == 'cancel_requested':
                os.killpg(process.pid, signal.SIGCONT)
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                store.update_job(job_id, status='cancelled')
            elif process and job['status'] == 'pause_requested':
                os.killpg(process.pid, signal.SIGSTOP)
                store.update_job(job_id, status='paused')
            elif process and job['status'] == 'resume_requested':
                os.killpg(process.pid, signal.SIGCONT)
                store.update_job(job_id, status='running')
            elif process is None and job['status'] == 'cancel_requested':
                store.update_job(job_id, status='cancelled')
            elif process is None and job['status'] in ('queued', 'resume_requested'):
                training = job['kind'] in ('sim_pilot', 'train_act')
                busy = any(store.job(key)['kind'] in ('sim_pilot', 'train_act') for key in processes)
                memory_ok = psutil.virtual_memory().available > 2.5 * 1024 ** 3
                if (not training or not busy) and (not training or memory_ok):
                    folder = store.root / 'jobs' / job_id
                    folder.mkdir(parents=True, exist_ok=True)
                    with (folder / 'worker.log').open('ab') as log:
                        process = subprocess.Popen([sys.executable, '-m', 'explorer.job_runner', job_id],
                            stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
                    processes[job_id] = process
                    store.update_job(job_id, status='running')
        time.sleep(1)
