#!/usr/bin/env python3
"""ENGINEERING_GLUE: isolated process/outbox/cgroup qualification, never robot IO.

Only newly allocated state, loopback HTTP, owned subprocesses and an optional
64MiB transient user cgroup are touched. This is not a device reboot or Wi-Fi
qualification. Actual source, process, database and kernel receipts are retained.
"""
from __future__ import annotations

import argparse
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import tarfile
import threading
import time
import uuid

import psutil


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_json(path, body):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name('.'+path.name+'.tmp')
    with temporary.open('w') as stream:
        json.dump(body, stream, indent=2, sort_keys=True, allow_nan=False)
        stream.flush(); os.fsync(stream.fileno())
    temporary.replace(path)


def wait_for(predicate, timeout=30, interval=.01):
    deadline = time.monotonic()+timeout
    value = predicate()
    while not value and time.monotonic() < deadline:
        time.sleep(interval); value = predicate()
    if not value:
        raise TimeoutError('Isolated qualification condition did not arrive')
    return value


def source_imports(root):
    sys.path[:0] = [str(Path(root)/'00-control-center/src'), str(Path(root)/'shared')]


def environment(root, config=None):
    value = dict(os.environ, NO_PROXY='127.0.0.1,localhost', PYTHONPATH=str(Path(root)/'00-control-center/src')+':'+str(Path(root)/'shared'))
    if config:
        value['EXPLORER_CONFIG'] = str(config)
    return value


def kill_owned(process, children=()):
    """Only Popen handles and descendants observed before a deliberate crash."""
    for child in children:
        try:
            if child.is_running() and child.uids().real == os.getuid():
                os.kill(child.pid, signal.SIGKILL)
        except (ProcessLookupError, psutil.NoSuchProcess):
            pass
    if process and process.poll() is None:
        process.kill()
    if process:
        process.wait(timeout=10)


def archive_release(source, commit, destination):
    resolved = subprocess.check_output(['git', '-C', str(source), 'rev-parse', '--verify', commit+'^{commit}'], text=True).strip()
    folder = destination/resolved
    folder.mkdir(parents=True, exist_ok=False)
    raw = subprocess.check_output(['git', '-C', str(source), 'archive', '--format=tar', resolved])
    with tarfile.open(fileobj=io.BytesIO(raw)) as archive:
        archive.extractall(folder, filter='data')
    (folder/'RELEASE_COMMIT').write_text(resolved+'\n')
    return folder


def scheduler_probe(root_a, root_b, folder):
    """Actual supervisor + non-physical handover_test runner across an abrupt kill."""
    from explorer.store import Store
    store = Store(folder/'state')
    config = folder/'config.json'; write_json(config, {'data_dir':str(store.root)})
    job = store.create_job('handover_test', '05', {'qualification_only':True})
    command = [sys.executable, '-c', 'from explorer.config import load;from explorer.store import Store;from explorer.scheduler import supervise;c=load();supervise(c,Store(c["data_dir"]))']
    # A test-owned exec wrapper stops before bootstrap. The original Supervisor
    # still claims/pins the real job and records the original worker PID. This
    # avoids racing a fast non-physical job or depending on CPU scheduling.
    gate=folder/'owned-worker-gate.py'
    gate.write_text('import os,signal,sys\nos.kill(os.getpid(),signal.SIGSTOP)\nos.execv(sys.executable,[sys.executable,*sys.argv[1:]])\n')
    gated_code=('import subprocess;from explorer import scheduler;from explorer.config import load;from explorer.store import Store;'
        'original=scheduler.Supervisor;native=subprocess.Popen;'
        'scheduler.Supervisor=lambda *a,**kw:original(*a,popen=lambda argv,**opts:native([argv[0],'+repr(str(gate))+',*argv[1:]],**opts),**kw);'
        'c=load();scheduler.supervise(c,Store(c["data_dir"]))')
    initial_command=[sys.executable,'-c',gated_code]
    handles, children, worker, stopped_pid = [], [], None, None
    log = (folder/'supervisor.log').open('ab')
    try:
        first = subprocess.Popen(initial_command, env=environment(root_a, config), stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        handles.append(first)
        def find_worker():
            if first.poll() is not None:
                raise RuntimeError('Isolated scheduler exited before qualification: '+(folder/'supervisor.log').read_text()[-2000:])
            found = psutil.Process(first.pid).children(recursive=True)
            for process in found:
                try:
                    arguments=process.cmdline()
                    if (job['id'] in arguments and str(gate) in arguments and
                            any(name in arguments for name in ('explorer.worker_bootstrap','explorer.job_runner')) and
                            process.status()==psutil.STATUS_STOPPED):return process
                except psutil.Error:pass
            return None
        worker = wait_for(find_worker, 20, .001)
        worker.suspend(); children.append(worker); stopped_pid = worker.pid
        wait_for(lambda: store.job(job['id'])['status']=='running', 3)
        def original_admission_recorded():
            runtime=store.schedule_status(job['id'])['runtime']
            return (runtime if (runtime.get('worker') or {}).get('pid')==worker.pid and
                runtime.get('phase')=='starting' and runtime.get('bootstrap_admitted') is False else None)
        starting_runtime=wait_for(original_admission_recorded,3)
        before = store.job(job['id'])
        if before['config'].get('_execution_commit') != root_a.name:
            raise AssertionError('First execution source was not pinned')
        worker_environment = worker.environ().get('PYTHONPATH')
        first.send_signal(signal.SIGSTOP)
        kill_owned(first, [worker]); children.clear()
        crashed = store.job(job['id'])
        if crashed['status'] != 'running':
            raise AssertionError('The isolated crash was not observed before worker completion')
        second = subprocess.Popen(command, env=environment(root_b, config), stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        handles.append(second)
        completed = wait_for(lambda: (j if (j:=store.job(job['id']))['status'] in ('completed','failed') else None), 30)
        if completed['status'] != 'completed':
            raise AssertionError('Recovered runner failed: '+str(completed['error']))
        result = completed['result']
        if result.get('execution_commit') != root_a.name or not result.get('passed') or result.get('physical_execution') is not False:
            raise AssertionError('Recovered non-physical job/source evidence differs')
        with store.connect() as db:
            count_before = db.execute('SELECT count(*) FROM events WHERE event_id=?', ('job.completed:'+job['id'],)).fetchone()[0]
        store.complete_job(job['id'], result)
        conflict_rejected = False
        try:
            store.complete_job(job['id'], {**result, 'conflicting_qualification_result':True})
        except ValueError:
            conflict_rejected = True
        kill_owned(second, psutil.Process(second.pid).children(recursive=True))
        third = subprocess.Popen(command, env=environment(root_b, config), stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        handles.append(third)
        time.sleep(2.2)  # Two real scheduler ticks over the already completed job.
        after = store.job(job['id'])
        with store.connect() as db:
            count_after = db.execute('SELECT count(*) FROM events WHERE event_id=?', ('job.completed:'+job['id'],)).fetchone()[0]
            integrity = db.execute('PRAGMA integrity_check').fetchone()[0]
        if count_before != 1 or count_after != 1 or not conflict_rejected or after['result'] != result or after['status'] != 'completed' or integrity != 'ok':
            raise AssertionError('Durable finalization/idempotence did not hold')
        return dict(passed=True, scope='actual isolated supervisor SIGKILL before worker bootstrap and non-physical handover_test recovery',
            first_worker_test_owned_exec_gate=True, first_worker_bootstrap_entered_before_crash=False,
            test_owned_gate_sha256=digest(gate), restarted_worker_uses_original_bootstrap=True,
            first_worker_durable_phase=starting_runtime['phase'], first_worker_bootstrap_admitted=starting_runtime['bootstrap_admitted'],
            job_id=job['id'], source_commit_before_crash=root_a.name, current_release_after_restart=root_b.name,
            source_commit_after_restart=after['config']['_execution_commit'], execution_commit=result['execution_commit'],
            killed_isolated_supervisor_pid=first.pid, killed_isolated_worker_pid=stopped_pid,
            worker_module_path_before_crash=worker_environment, status_at_crash=crashed['status'], final_status=after['status'],
            single_completion_event=count_after, conflicting_finalization_rejected=conflict_rejected, database_integrity=integrity,
            physical_execution=False, kernel_or_device_reboot_tested=False)
    finally:
        for handle in handles:
            descendants = psutil.Process(handle.pid).children(recursive=True) if handle.poll() is None else []
            kill_owned(handle, descendants)
        for child in children:
            try: child.kill()
            except psutil.NoSuchProcess: pass
        log.close()


def delivery_child(root, state, central_url, token_path, status_path):
    source_imports(root)
    from explorer_shared.node_agent import EventDelivery
    delivery = EventDelivery('qualification', Path(state), central_url, Path(token_path).read_text().strip())
    attempts = 0
    while True:
        attempts += 1
        try:
            count = delivery.deliver_once()
            write_json(status_path, {'attempts':attempts, 'delivered_this_attempt':count, 'pending':len(delivery.outbox.pending()), 'error':None})
        except Exception as error:
            write_json(status_path, {'attempts':attempts, 'pending':len(delivery.outbox.pending()), 'error_type':type(error).__name__})
        time.sleep(.1)


def outbox_probe(source, folder):
    from explorer.store import Store
    from explorer_shared.node_agent import EventDelivery
    from explorer_shared.outbox import EventOutbox
    folder.mkdir(parents=True)
    receiver = Store(folder/'central')
    token = uuid.uuid4().hex
    token_path = folder/'fixture-token'; token_path.write_text(token); token_path.chmod(0o600)
    with socket.socket() as reservation:
        reservation.bind(('127.0.0.1', 0)); port = reservation.getsockname()[1]
    central_url = 'http://127.0.0.1:'+str(port)
    node_state = folder/'node'
    delivery = EventDelivery('qualification', node_state, central_url, token)
    ids = [delivery.emit({'kind':'recovery_qualification_probe', 'index':i, 'software_only':True}, run_id='isolated-outbox') for i in range(3)]
    original = {item['event_id']:item['payload'] for item in delivery.outbox.pending()}
    statuses, handles, requests = [], [], []
    first_received, drop_ack_released = threading.Event(), threading.Event()
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def do_POST(self):
            if self.path != '/api/events' or self.headers.get('Authorization') != 'Bearer '+token:
                self.send_error(403);return
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            inserted = receiver.ingest(body)
            requests.append({'event_id':body['event_id'], 'inserted':inserted})
            if len(requests) == 1:
                first_received.set(); drop_ack_released.wait(8)
                try: self.connection.shutdown(socket.SHUT_RDWR)
                except OSError: pass
                self.connection.close();return
            raw = b'{"ok":true}'
            self.send_response(200);self.send_header('Content-Length',str(len(raw)));self.end_headers()
            self.wfile.write(raw)
    server, thread = None, None
    def start_child(label):
        status = folder/(label+'.json'); statuses.append(status)
        handle = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), '--child-delivery', str(source), str(node_state), central_url, str(token_path), str(status)],
            env=environment(source), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        handles.append(handle);return handle, status
    try:
        first, status = start_child('offline-child')
        failure = wait_for(lambda: (json.loads(status.read_text()) if status.is_file() and json.loads(status.read_text()).get('error_type') else None), 10)
        kill_owned(first)
        pending_after_outage_crash = len(EventOutbox(node_state/'outbox.sqlite3').pending())
        if pending_after_outage_crash != 3:
            raise AssertionError('Outage or abrupt process death lost an event')
        server = ThreadingHTTPServer(('127.0.0.1',port), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True);thread.start()
        second, unused = start_child('lost-ack-child')
        if not first_received.wait(10):
            raise TimeoutError('Loopback receiver did not durably ingest first event')
        kill_owned(second);drop_ack_released.set()
        pending_after_ack_loss = len(EventOutbox(node_state/'outbox.sqlite3').pending())
        if pending_after_ack_loss != 3:
            raise AssertionError('Lost ACK was incorrectly recorded as delivery')
        third, status = start_child('reconnected-child')
        wait_for(lambda: not EventOutbox(node_state/'outbox.sqlite3').pending(), 15)
        kill_owned(third)
        with receiver.connect() as db:
            received = {row['event_id']:json.loads(row['body']) for row in db.execute('SELECT event_id,body FROM events')}
            receiver_integrity = db.execute('PRAGMA integrity_check').fetchone()[0]
        if received != original or len(requests) != 4 or sum(not item['inserted'] for item in requests) != 1:
            raise AssertionError('Actual retry/central deduplication changed event identity/content')
        conflict_rejected = False
        try:
            delivery.outbox.append(ids[0], {**original[ids[0]], 'changed':True})
        except ValueError:
            conflict_rejected = True
        with delivery.outbox.connect() as db:
            row_count = db.execute('SELECT count(*) FROM outbox').fetchone()[0]
            acknowledged = db.execute('SELECT count(*) FROM outbox WHERE delivered=1').fetchone()[0]
            outbox_integrity = db.execute('PRAGMA integrity_check').fetchone()[0]
        if not conflict_rejected or row_count != 3 or acknowledged != 3 or receiver_integrity != 'ok' or outbox_integrity != 'ok':
            raise AssertionError('Final outbox/receiver integrity failed')
        return dict(passed=True, scope='actual EventDelivery processes and loopback HTTP outage/lost ACK/reconnect',
            event_ids=ids, preserved_pending_after_outage_and_SIGKILL=pending_after_outage_crash,
            preserved_pending_after_durable_ingest_without_ACK=pending_after_ack_loss,
            HTTP_requests=requests, central_unique_events=len(received), acknowledged_events=acknowledged,
            immutable_event_bodies_preserved=True, conflicting_outbox_identity_rejected=conflict_rejected,
            receiver_integrity=receiver_integrity, outbox_integrity=outbox_integrity,
            actual_first_outage_error_type=failure['error_type'], physical_WiFi_interrupted=False,
            loopback_port=port, processes_SIGKILLed=[p.pid for p in handles])
    finally:
        drop_ack_released.set()
        for handle in handles: kill_owned(handle)
        if server: server.shutdown();server.server_close()
        if thread: thread.join(timeout=3)


def epoch_child(source, destination, previous=None):
    source_imports(source)
    from explorer_shared.pi_nano_route import NanoArmGateway
    calls = []
    def fixture(operation, body=None):
        calls.append(operation)
        if operation == 'arm/manual': return 200, {'servo_deg':[90,90,90,90,90,30], 'blocked_by':None}
        if operation == 'arm/trajectory': return 200, {'phase':'idle', 'busy':False}
        if operation == 'arm/trajectory/stop': return 200, {'cancel_requested':True, 'physical_stop_confirmed':False}
        raise AssertionError('No command may reach the fixture after cancellation/restart')
    registration = {'state':'REGISTERED_DECLARATION', 'registration_id':'SOFTWARE_FIXTURE_NOT_PHYSICAL_REGISTRATION', 'registration_sha256':'fixture'}
    gateway = NanoArmGateway(fixture, registration)
    if previous is None:
        owner = gateway.start({'owner_id':'qualification-fixture', 'expected_control_epoch':gateway.status()['control_epoch'],
            'registration_id':registration['registration_id'], 'registration_sha256':'fixture', 'observing':True})
        old = {'control_epoch':owner['control_epoch'], 'owner_id':owner['owner_id']}
        gateway.stop(old)
    else:
        old = json.loads(Path(previous).read_text())['cancelled_owner']
    rejected = []
    for operation, body in [('action',{**old,'sequence':1,'goal':[90,90,90,90,90,30]}), ('heartbeat',{**old,'held':True})]:
        try: getattr(gateway,operation)(body)
        except ValueError: rejected.append(operation)
    write_json(destination, {'cancelled_owner':old, 'new_control_epoch':gateway.status()['control_epoch'],
        'rejected':rejected, 'fixture_calls':calls, 'software_fixture_only':True})


def epoch_probe(source, folder):
    folder.mkdir(parents=True)
    before, after = folder/'before.json', folder/'after.json'
    subprocess.run([sys.executable,str(Path(__file__).resolve()),'--child-epoch',str(source),str(before)], env=environment(source), check=True)
    subprocess.run([sys.executable,str(Path(__file__).resolve()),'--child-epoch',str(source),str(after),str(before)], env=environment(source), check=True)
    a,b = json.loads(before.read_text()), json.loads(after.read_text())
    if a['rejected'] != ['action','heartbeat'] or b['rejected'] != ['action','heartbeat'] or b['fixture_calls'] or a['new_control_epoch'] == b['new_control_epoch']:
        raise AssertionError('Cancelled ownership epoch replayed across a process boundary')
    return dict(passed=True, scope='actual NanoArmGateway epoch validation in two separate processes with a non-physical executor fixture',
        cancelled_owner_epoch=a['cancelled_owner']['control_epoch'], restarted_epoch=b['new_control_epoch'],
        stale_action_and_lease_rejected_before_and_after_process_restart=True,
        original_executor_calls_after_restart=b['fixture_calls'], physical_commands_dispatched=0,
        physical_controller_reset_or_reboot_tested=False)


def _memory_events(path):
    return {key:int(value) for key,value in (line.split() for line in Path(path).read_text().splitlines())}


def oom_child(folder):
    """No allocation until both the kernel limit and an external permit are present."""
    folder = Path(folder)
    relative = Path('/proc/self/cgroup').read_text().strip().split('::',1)[1]
    group = Path('/sys/fs/cgroup')/relative.lstrip('/')
    maximum, swap = (group/'memory.max').read_text().strip(), (group/'memory.swap.max').read_text().strip()
    preflight = dict(cgroup=str(group), memory_max=maximum, memory_swap_max=swap, before=_memory_events(group/'memory.events'))
    write_json(folder/'preflight.json', preflight)
    if maximum != str(64*1024**2) or swap != '0':
        write_json(folder/'oom-result.json', {'refused':True,'reason':'Exact 64MiB/swap0 kernel cap absent'});return
    wait_for(lambda: (folder/'allocation-permit').is_file(), 10)
    child = os.fork()
    if child == 0:
        Path('/proc/self/oom_score_adj').write_text('500')
        pages = []
        for _ in range(128):
            pages.append(bytearray(b'x')*(1024**2))
            time.sleep(.003)
        os._exit(7)
    waited, status = os.waitpid(child, 0)
    after = _memory_events(group/'memory.events')
    result = dict(child_pid=waited, child_wait_status=status, child_signal=os.WTERMSIG(status) if os.WIFSIGNALED(status) else None,
        after=after, oom_kill_delta=after.get('oom_kill',0)-preflight['before'].get('oom_kill',0),
        memory_max=maximum, memory_swap_max=swap, memory_peak=(group/'memory.peak').read_text().strip())
    result['passed'] = result['child_signal']==9 and result['oom_kill_delta']>=1
    write_json(folder/'oom-result.json',result)


def oom_probe(folder):
    folder.mkdir(parents=True)
    if not Path('/sys/fs/cgroup/cgroup.controllers').is_file() or 'memory' not in Path('/sys/fs/cgroup/cgroup.controllers').read_text().split():
        return {'passed':False,'status':'UNAVAILABLE','reason':'cgroup v2 memory controller absent','host_pressure_attempted':False}
    unit = 'explorer-isolated-oom-qualification-'+uuid.uuid4().hex[:12]
    command = ['systemd-run','--user','--unit='+unit,'--property=MemoryMax=64M','--property=MemorySwapMax=0',
        '--property=RuntimeMaxSec=20','--property=OOMPolicy=continue','--property=RemainAfterExit=yes',
        sys.executable,str(Path(__file__).resolve()),'--child-oom',str(folder)]
    started = subprocess.run(command,capture_output=True,text=True,timeout=10)
    write_json(folder/'unit-start.json',{'unit':unit,'returncode':started.returncode,'stderr':started.stderr})
    if started.returncode:
        return {'passed':False,'status':'UNAVAILABLE','reason':'New constrained user unit could not be started','host_pressure_attempted':False}
    try:
        preflight_path = folder/'preflight.json'
        wait_for(lambda: preflight_path.is_file(),10)
        preflight=json.loads(preflight_path.read_text())
        if preflight['memory_max'] != str(64*1024**2) or preflight['memory_swap_max'] != '0':
            return {'passed':False,'status':'REFUSED','preflight':preflight,'host_pressure_attempted':False}
        (folder/'allocation-permit').write_text('Only this verified 64MiB cgroup may allocate.\n')
        result_path=folder/'oom-result.json'
        wait_for(lambda: result_path.is_file(),15)
        result=json.loads(result_path.read_text())
        return {**result,'status':'KERNEL_CGROUP_OOM_TESTED' if result.get('passed') else 'FAILED',
            'scope':'actual kernel OOM only inside a new own 64MiB/swap0 transient user cgroup',
            'unit':unit,'preflight':preflight,'host_memory_exhaustion_attempted':False,'production_worker_interrupted':False}
    finally:
        subprocess.run(['systemctl','--user','stop',unit+'.service'],capture_output=True,timeout=10)
        subprocess.run(['systemctl','--user','reset-failed',unit+'.service'],capture_output=True,timeout=10)


def main():
    if len(sys.argv)>1 and sys.argv[1]=='--child-delivery':
        delivery_child(*sys.argv[2:]);return
    if len(sys.argv)>1 and sys.argv[1]=='--child-epoch':
        epoch_child(*sys.argv[2:]);return
    if len(sys.argv)>1 and sys.argv[1]=='--child-oom':
        oom_child(sys.argv[2]);return
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-root',type=Path,default=Path(__file__).resolve().parents[1])
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--with-isolated-oom',action='store_true')
    args=parser.parse_args()
    output=args.output.resolve();output.mkdir(parents=True,exist_ok=False);output.chmod(0o700)
    root=args.source_root.resolve()
    a=subprocess.check_output(['git','-C',str(root),'rev-parse','HEAD~1'],text=True).strip()
    b=subprocess.check_output(['git','-C',str(root),'rev-parse','HEAD'],text=True).strip()
    release_a=archive_release(root,a,output/'releases');release_b=archive_release(root,b,output/'releases')
    source_imports(release_a)
    result=dict(schema='explorer_isolated_recovery_qualification_v1',created_unix_s=time.time(),source_repository=str(root),
        qualifier_sha256=digest(__file__),source_commits=[a,b],
        source_sha256={name:digest(release_a/name) for name in ('00-control-center/src/explorer/scheduler.py','00-control-center/src/explorer/job_runner.py','00-control-center/src/explorer/store.py','shared/explorer_shared/outbox.py','shared/explorer_shared/node_agent.py','shared/explorer_shared/pi_nano_route.py')},
        scheduler=scheduler_probe(release_a,release_b,output/'scheduler'),
        outbox=outbox_probe(release_a,output/'outbox'),
        ownership_epochs=epoch_probe(release_a,output/'epochs'),
        limitations=['No host/Pi/Nano reboot','No physical Wi-Fi disconnect or production network changes',
            'No original controller writes or robot commands','Gateway epoch check uses an explicitly non-physical executor fixture',
            'OOM qualification, when requested, is limited to a new own constrained user cgroup; it does not test production learner OOM recovery'])
    result['oom']=oom_probe(output/'oom') if args.with_isolated_oom else {'status':'NOT_REQUESTED','host_pressure_attempted':False}
    result['passed']=all(result[key]['passed'] for key in ('scheduler','outbox','ownership_epochs')) and (not args.with_isolated_oom or result['oom'].get('passed',False))
    write_json(output/'result.json',result)
    print(json.dumps({'passed':result['passed'],'result':str(output/'result.json'),'scope':'isolated processes, loopback HTTP and optionally one own constrained cgroup'},indent=2))
    if not result['passed']:raise SystemExit(1)


if __name__=='__main__':
    main()
