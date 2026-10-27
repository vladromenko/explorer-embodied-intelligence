#!/usr/bin/env python3
"""ENGINEERING_GLUE: private HTTP API→durable worker→download→new-PID byte restore.

Only the new producer's source job is selected by ID. Native nodes are absent.
The scientific MJB/source blobs are copied as data, never loaded or executed.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import secrets
import socket
import sqlite3
import subprocess
import sys
import tarfile
import time

GIB=1024**3


def sha(path):
    with Path(path).open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()


def canonical(value):return json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()
def read(path):
    path=Path(path)
    if path.stat().st_size>32*1024**2:raise ValueError('Bounded qualifier JSON required')
    return json.loads(path.read_bytes())
def write(path,value):
    path=Path(path);path.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
    with path.open('xb') as stream:stream.write(canonical(value));stream.flush();os.fsync(stream.fileno())


def resources():
    relative=next(line.split('::',1)[1] for line in Path('/proc/self/cgroup').read_text().splitlines() if line.startswith('0::'))
    root=Path('/sys/fs/cgroup')/relative.lstrip('/')
    return dict(pid=os.getpid(),cgroup=str(root),limits={name:(root/name).read_text().strip() for name in ('memory.max','memory.swap.max','cpu.max')},
        memory_peak_bytes=int((root/'memory.peak').read_text()),swap_peak_bytes=int((root/'memory.swap.peak').read_text()),memory_events=(root/'memory.events').read_text())


def guard(unit):
    value=resources();quota,period=value['limits']['cpu.max'].split()
    if Path(value['cgroup']).name!=unit+'.service' or value['limits']['memory.max']!=str(2*GIB) or value['limits']['memory.swap.max']!='0' or quota=='max' or int(quota)!=int(period):
        raise ValueError('Declared own CPU1/2GiB/swap0 export unit required')
    return value


def source_row(store,identity):
    with sqlite3.connect(Path(store.path).resolve().as_uri()+'?mode=ro',uri=True) as db:
        db.execute('PRAGMA query_only=ON')
        return list(db.execute('SELECT config,result,error,status FROM jobs WHERE id=?',(identity,)).fetchone())


def independent_restore(output):
    started=time.monotonic();plan=read(output/'restore-input.json');archive_path=Path(plan['downloaded_archive']);original=Path(plan['original_state_root']).resolve()
    assert sha(archive_path)==plan['archive_sha256']
    from explorer_shared.research_export import restore_export,_relative,LIMITS
    denied=[0]
    def audit(name,args):
        if name=='open' and args and isinstance(args[0],(str,bytes,os.PathLike)):
            raw=os.fsdecode(args[0]);candidate=Path(raw).absolute().resolve()
            if candidate.is_relative_to(original):
                denied[0]+=1;raise PermissionError('Qualification forbids independent restore reading original source state')
    sys.addaudithook(audit)
    try:
        with open(plan['original_MJB_path'],'rb'):raise AssertionError('Source-read positive control unexpectedly opened')
    except PermissionError:positive=True
    assert denied[0]==1;denied[0]=0
    unpack=output/'download-unpacked';unpack.mkdir()
    with tarfile.open(archive_path,'r:') as archive:
        first=archive.getmember('bundle/manifest.json');assert first.isfile() and first.size<=LIMITS['max_manifest_bytes']
        raw_manifest=archive.extractfile(first).read();assert hashlib.sha256(raw_manifest).hexdigest()==plan['manifest_sha256']
        manifest=json.loads(raw_manifest)
        expected={'bundle/manifest.json':dict(sha256=plan['manifest_sha256'],size_bytes=len(raw_manifest)),
            'bundle/'+manifest['protocol_file']:dict(sha256=manifest['protocol_sha256']),
            'restore-verification.json':dict(sha256=plan['worker_restore_sha256'])}
        for row in manifest['file_manifest']:
            key='bundle/'+row['relative_path'];assert key not in expected;expected[key]=dict(sha256=row['sha256'],size_bytes=row['size_bytes'])
        seen=set();total=0
        for member in archive:
            _relative(member.name)
            assert member.isfile() and member.name in expected and member.name not in seen and member.size<=LIMITS['max_file_bytes']
            seen.add(member.name);total+=member.size;assert total<=LIMITS['max_total_bytes']+64*1024**2
            entry=expected[member.name]
            if 'size_bytes' in entry:assert member.size==entry['size_bytes']
            destination=unpack/member.name;destination.parent.mkdir(parents=True,exist_ok=True)
            checksum=hashlib.sha256();count=0
            with archive.extractfile(member) as source,destination.open('xb') as sink:
                chunk=source.read(1024**2)
                while chunk:
                    checksum.update(chunk);count+=len(chunk);sink.write(chunk);chunk=source.read(1024**2)
            assert count==member.size and checksum.hexdigest()==entry['sha256']
        assert seen==set(expected)
    restored=restore_export(unpack/'bundle/manifest.json',plan['manifest_sha256'],output/'second-independent-restore')
    restored_manifest=read(unpack/'bundle/manifest.json');expected_original=plan['captured_job_files'];matched={};admissions=[]
    for row in restored_manifest['file_manifest']:
        locator=row.get('source_locator') or {};relative=locator.get('relative_path')
        if locator.get('root')=='job_'+plan['source_job_id'] and relative in expected_original:
            assert row['sha256']==expected_original[relative]['sha256'] and row['size_bytes']==expected_original[relative]['size_bytes']
            matched[relative]=row['sha256']
        if row['role']=='profile':
            content=read(unpack/'bundle'/row['relative_path'])
            if content.get('schema')=='explorer_recorded_initial_state_export_admission_v1':admissions.append(content)
    assert set(matched)==set(expected_original)
    assert len(admissions)==1 and admissions[0]['manifest_sha256']==plan['initial_manifest_sha256']
    assert admissions[0]['original_runtime_OS_closure']=='PARTIAL' and not admissions[0]['numerical_reexecution_verified_by_export']
    assert denied[0]==0 and restored['verification']=='PARTIAL'
    result=dict(schema='explorer_recorded_initial_download_new_PID_restore_v1',pid=os.getpid(),archive_sha256=plan['archive_sha256'],
        manifest_sha256=plan['manifest_sha256'],original_source_read_positive_control_blocked=positive,original_source_open_attempts_during_restore=denied[0],
        original_source_guard_scope='Python audit open events; no archived code or model execution',
        exact_captured_job_files=len(matched),matched_original_files_sha256=matched,portable_byte_admission=admissions[0],
        independent_restore_verification=restored['verification'],numeric_reexecution_performed=False,RGB_reexecution_performed=False,
        physical_io=False,production_state_writes=False,elapsed_s=time.monotonic()-started)
    write(output/'independent-restore-qualification.json',result)
    return result


def run(root,capture,output,commit,unit):
    started=time.monotonic();bound=guard(unit);root=Path(root).resolve();capture=Path(capture).resolve();output=Path(output).resolve()
    assert subprocess.check_output(['git','-C',str(root),'rev-parse','HEAD'],text=True).strip()==commit
    assert not subprocess.check_output(['git','-C',str(root),'status','--porcelain'],text=True).strip()
    assert not output.exists();output.mkdir(mode=0o700,parents=True);write(output/'resources-start.json',bound)
    proof=read(capture/'capture-qualification.json');assert proof['source_commit']==commit and proof['initial_state_status']=='RECORDED' and proof['target_advance_calls']==0
    source_id=proof['source_job_id'];state=Path(proof['private_store_root']);assert state==capture/'state'
    archive=output/'current-source.tar';release=output/'releases'/commit;release.mkdir(parents=True)
    with archive.open('xb') as stream:subprocess.run(['git','-C',str(root),'archive',commit],stdout=stream,check=True)
    with tarfile.open(archive) as stream:stream.extractall(release,filter='data')
    (release/'RELEASE_COMMIT').write_text(commit+'\n')
    sys.path[:0]=[str(release/'shared'),str(release/'00-control-center/src')]
    os.environ.pop('MUJOCO_GL',None);os.environ['CUDA_VISIBLE_DEVICES']=''
    from explorer.store import Store
    from explorer.scheduler import Supervisor
    from explorer_shared.learning.checkpoint_control import worker_identity
    import httpx
    store=Store(state);original=source_row(store,source_id);assert original[-1]=='failed'
    source_snapshot={name:sha(state/'jobs'/source_id/name) for name in proof['job_files']};assert all(source_snapshot[k]==v['sha256'] for k,v in proof['job_files'].items())
    # Authentication exists only in this private loopback process/config. It is
    # not included in selected scientific source artifacts or public receipts.
    access=secrets.token_urlsafe(32);salt=secrets.token_hex(16)
    config=dict(data_dir=str(state),nodes=[],password_salt=salt,password_hash=hashlib.pbkdf2_hmac('sha256',access.encode(),salt.encode(),310000).hex())
    write(output/'private-api-config.json',config);os.environ['EXPLORER_CONFIG']=str(output/'private-api-config.json')
    env=dict(os.environ,PYTHONPATH=str(release/'shared')+':'+str(release/'00-control-center/src'),OMP_NUM_THREADS='1',MKL_NUM_THREADS='1',OPENBLAS_NUM_THREADS='1')
    sock=socket.socket();sock.bind(('127.0.0.1',0));port=sock.getsockname()[1];sock.listen(32)
    server=None;supervisor=None;failure=None;http_trace=[];workers={}
    try:
        with (output/'api-server.log').open('xb') as log:
            server=subprocess.Popen([sys.executable,'-m','uvicorn','explorer.app:app','--fd',str(sock.fileno()),'--log-level','warning'],
                env=env,stdout=log,stderr=subprocess.STDOUT,pass_fds=(sock.fileno(),),start_new_session=True)
        sock.close()
        with httpx.Client(base_url='http://127.0.0.1:'+str(port),timeout=90,trust_env=False) as client:
            ready=False
            for attempt in range(200):
                try:ready=client.get('/api/health').status_code==200
                except httpx.TransportError:pass
                if ready:break
                if server.poll() is not None:raise RuntimeError('Private API exited before readiness')
                time.sleep(.05)
            assert ready
            def request(method,path,**kwargs):
                response=client.request(method,path,**kwargs);http_trace.append(dict(method=method,path=path,status=response.status_code));return response
            assert request('GET','/api/research/exports').status_code==401
            assert request('POST','/api/login',json=dict(password=access)).status_code==200
            csrf={'X-Explorer-CSRF':client.cookies['explorer_csrf']}
            assert request('POST','/api/research/exports',json=dict(job_ids=[source_id])).status_code==403
            assert request('POST','/api/research/exports',json=dict(job_ids=[source_id],path='/not-client-selected'),headers=csrf).status_code==400
            response=request('POST','/api/research/exports',json=dict(job_ids=[source_id]),headers=csrf);assert response.status_code==200,response.text
            export_id=response.json()['id'];job=store.job(export_id);assert job['kind']=='research_export' and job['status']=='queued'
            settings=job['config'];assert settings['job_ids']==[source_id] and settings['estimated_payload_bytes']>proof['compiled_MJB_bytes']
            route='/api/research/exports/'+export_id+'/download';assert request('GET',route).status_code in (404,409)
            supervisor=Supervisor(dict(root=str(release)),store,quantum_s=3600)
            while time.monotonic()-started<105:
                supervisor.tick()
                for identity,process in supervisor.processes.items():
                    identity_record=worker_identity(process.pid)
                    if identity_record is not None:workers[identity]=identity_record
                actual=store.job(export_id)
                if actual['status'] in ('completed','failed','cancelled') and export_id not in supervisor.processes:break
                time.sleep(.02)
            actual=store.job(export_id);assert actual['status']=='completed',actual.get('error');result=actual['result']
            assert export_id not in supervisor.processes and result['verification']=='PARTIAL' and result['independent_restore_verified']
            download=output/'downloaded-evidence.tar';checksum=hashlib.sha256();download_size=0
            with client.stream('GET',route) as response:
                assert response.status_code==200 and response.headers['cache-control']=='no-store'
                with download.open('xb') as stream:
                    for chunk in response.iter_bytes(1024**2):stream.write(chunk);checksum.update(chunk);download_size+=len(chunk)
                assert response.headers['x-content-sha256']==checksum.hexdigest()==result['archive_sha256'] and download_size==result['archive_bytes']
            http_trace.append(dict(method='GET',path=route,status=200,streamed_bytes=download_size,verified_SHA256=checksum.hexdigest()))
            assert request('GET',route+'?path=/etc/passwd').status_code==400
        server.terminate();server.wait(timeout=10);api_pid=server.pid;server=None
        assert source_row(store,source_id)==original and all(sha(state/'jobs'/source_id/name)==expected for name,expected in source_snapshot.items())
        original_result=json.loads(original[1]);initial=original_result['initial_state']
        restore_plan=dict(downloaded_archive=str(download),archive_sha256=result['archive_sha256'],manifest_sha256=result['manifest_sha256'],
            worker_restore_sha256=result['restore_verification_sha256'],original_state_root=str(state),original_MJB_path=str(state/'jobs'/source_id/'initial-state/compiled-model.mjb'),
            captured_job_files=proof['job_files'],source_job_id=source_id,initial_manifest_sha256=initial['manifest_sha256'])
        write(output/'restore-input.json',restore_plan)
        child_env=dict(env,EXPLORER_CONFIG=str(output/'private-api-config.json'))
        with (output/'independent-restore.log').open('xb') as log:
            child=subprocess.Popen([sys.executable,str(Path(__file__).resolve()),'--mode','restore','--output',str(output),'--unit',unit],env=child_env,stdout=log,stderr=subprocess.STDOUT)
            try:code=child.wait(timeout=60)
            except subprocess.TimeoutExpired:child.kill();child.wait();raise
        assert code==0 and child.pid!=os.getpid() and child.pid!=api_pid
        independent=read(output/'independent-restore-qualification.json')
        report=dict(schema='explorer_recorded_initial_authenticated_queue_export_v1',source_commit=commit,driver_sha256=sha(__file__),source_archive_sha256=sha(archive),
            source_job_id=source_id,export_job_id=export_id,source_capture_qualification_sha256=sha(capture/'capture-qualification.json'),
            actual_loopback_HTTP=True,authenticated_API=True,id_only_selection=True,HTTP_trace=http_trace,API_process_PID=api_pid,worker_processes=workers,
            actual_Supervisor_bootstrap_job_runner=True,worker_reaped=True,independent_restore_PID=child.pid,source_row_bytes_unchanged=True,source_files_unchanged=True,
            export_result=result,independent_restore=independent,initial_state_status='RECORDED',compiled_MJB_bytes=proof['compiled_MJB_bytes'],CAD_assets=proof['CAD_assets'],
            source_full_skill_success=False,numerical_reexecution_verified_by_export=False,RGB_reexecution_verified_by_export=False,physical_io=False,production_state_writes=False,
            elapsed_s=time.monotonic()-started)
        assert not {'torch','mujoco','OpenGL'}&set(sys.modules)
        write(output/'export-qualification.json',report)
        print(json.dumps(dict(source_job_id=source_id,export_job_id=export_id,verification=result['verification'],archive_bytes=download_size,
            exact_original_files=independent['exact_captured_job_files'],independent_PID=child.pid,elapsed_s=report['elapsed_s'])))
        return report
    except BaseException as error:
        failure=dict(error_type=type(error).__name__,detail=str(error));write(output/'export-failure.json',failure);raise
    finally:
        if server is not None:
            server.terminate()
            try:server.wait(timeout=10)
            except subprocess.TimeoutExpired:server.kill();server.wait()
        if supervisor is not None:
            for process in supervisor.processes.values():
                if process.poll() is None:
                    process.terminate()
                    try:process.wait(timeout=5)
                    except subprocess.TimeoutExpired:process.kill();process.wait()
        write(output/'resources-final.json',dict(resources(),failure=failure))


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--mode',choices=('export','restore'),default='export');parser.add_argument('--source-root');parser.add_argument('--capture');parser.add_argument('--output',required=True);parser.add_argument('--commit');parser.add_argument('--unit',required=True)
    a=parser.parse_args();guard(a.unit)
    if a.mode=='restore':independent_restore(Path(a.output))
    else:run(a.source_root,a.capture,a.output,a.commit,a.unit)
