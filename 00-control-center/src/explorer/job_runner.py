"""ENGINEERING_GLUE: isolated job entry point; durable real result evidence."""
import hashlib
import json
import os
import signal
import sys
import tarfile
import time
import traceback
from pathlib import Path
import httpx
from explorer_shared.contracts import event
from .config import load
from .store import Store


def recording(store, job, config):
    camera = job['config']['camera_id']
    node = next(node for node in config['nodes'] if camera.startswith(node['id'] + '-'))
    base = 'http://' + node['host'] + ':' + str(node['port'])
    headers = {'Authorization':'Bearer ' + node['token']}
    with httpx.Client(timeout=15, trust_env=False, headers=headers) as client:
        response = client.post(base + '/recordings', json=dict(camera_id=camera,
            duration_s=job['config']['duration_s'], recording_id=job['id'], project_id=job['project_id']))
        response.raise_for_status()
        manifest = response.json()
        started = time.monotonic()
        while manifest.get('state') == 'recording' and time.monotonic() - started < job['config']['duration_s'] + 30:
            time.sleep(1)
            response = client.get(base + '/recordings/' + job['id'])
            response.raise_for_status()
            manifest = response.json()
            store.update_job(job['id'], progress=min(.95, (time.monotonic() - started) / job['config']['duration_s']))
        folder = store.root / 'jobs' / job['id']
        response = client.get(base + '/recordings/' + job['id'] + '/download')
        response.raise_for_status()
        digest = hashlib.sha256(response.content).hexdigest()
        if digest != response.headers.get('X-Content-SHA256'):
            raise ValueError('Transferred recording hash mismatch')
        artifact = store.root / 'artifacts' / digest[:2] / (digest + '.tar')
        artifact.parent.mkdir(parents=True, exist_ok=True)
        if not artifact.exists():
            temporary = artifact.with_suffix('.tmp')
            temporary.write_bytes(response.content)
            temporary.replace(artifact)
        with tarfile.open(artifact) as archive:
            stored = json.load(archive.extractfile('manifest.json'))
            for frame in stored['frames']:
                data = archive.extractfile(frame['file']).read()
                if hashlib.sha256(data).hexdigest() != frame['sha256']:
                    raise ValueError('Recorded frame hash mismatch')
        (folder / 'manifest.json').write_text(json.dumps(stored, indent=2, ensure_ascii=False))
        return dict(recording_id=job['id'], camera_id=camera, source_type='physical_camera_observation',
            camera_only=True, supervised_actions_available=False, state=stored['state'],
            frame_count=len(stored['frames']), archive_sha256=digest, artifact=str(artifact),
            local_original_retained=True, result_scope='Observation recording; not a trained physical skill')


def station_import(store, job, config):
    episode_id = job['config']['episode_id']
    import re
    mobile=job['kind']=='mobile_import'
    if not isinstance(episode_id,str) or not re.fullmatch('[A-Za-z0-9][A-Za-z0-9_.-]{0,79}' if mobile else '[0-9a-f]{32}',episode_id):
        raise ValueError('Invalid station episode ID')
    node = next(node for node in config['nodes'] if node['id']==('nano' if mobile else 'pi'))
    source='mobile' if mobile else 'station'
    with httpx.Client(timeout=60,trust_env=False) as client:
        response = client.get('http://'+node['host']+':'+str(node['port'])+'/'+source+'/recordings/'+episode_id+'/download',
            headers={'Authorization':'Bearer '+node['token']})
        response.raise_for_status()
    digest = hashlib.sha256(response.content).hexdigest()
    if digest!=response.headers.get('X-Content-SHA256'):
        raise ValueError('Station archive transfer hash mismatch')
    artifact = store.root/'artifacts'/digest[:2]/(digest+'.tar')
    artifact.parent.mkdir(parents=True,exist_ok=True)
    if not artifact.exists() or hashlib.sha256(artifact.read_bytes()).hexdigest()!=digest:
        temporary=artifact.with_suffix('.tmp')
        with temporary.open('wb') as stream:
            stream.write(response.content)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(artifact)
    destination = store.root/'episodes'/source/episode_id
    marker=destination/'.archive-sha256'
    if not marker.is_file() or marker.read_text().strip()!=digest:
        stage=destination.with_name(episode_id+'.importing')
        if stage.exists():
            import shutil
            shutil.rmtree(stage)
        stage.mkdir(parents=True)
        with tarfile.open(artifact) as archive:
            archive.extractall(stage,filter='data')
        if mobile:
            transport=json.loads((stage/'transport.json').read_text())
            for name,expected in transport['source_files_sha256'].items():
                path=(stage/name).resolve()
                if not path.is_relative_to(stage.resolve()) or hashlib.sha256(path.read_bytes()).hexdigest()!=expected:
                    raise ValueError('Transferred original mobile source checksum mismatch')
        (stage/'.archive-sha256').write_text(digest+'\n')
        if destination.exists():
            import shutil
            shutil.rmtree(destination)
        stage.replace(destination)
    if mobile:
        descriptor=json.loads((destination/'episode.json').read_text())
        if descriptor.get('format')=='explorer_mobile_observation_episode_v1':
            sys.path.insert(0,str(Path(config['root'])/'01-autonomous-learning/src'))
            from explorer_sim.mobile_observation_adapter import convert_mobile_observation
            converted=convert_mobile_observation(destination/'episode.json',store.root/'datasets'/('mobile-observation-'+episode_id))
            exported=None
            if converted['dataset_manifest']:
                from explorer_sim.dataset_export import export_lerobot_v3
                exported=export_lerobot_v3(Path(converted['dataset_manifest']),Path(converted['dataset_manifest']).parent/'lerobot_v3')
            return dict(episode_id=episode_id,observation_only=not converted['dataset_available'],observation_manifest=str(destination/'episode.json'),
                source_archive_sha256=digest,source_original_retained=True,physical_success_inferred=False,
                frames=converted['frames'] if converted['dataset_available'] else descriptor.get('frames',0),telemetry_rows=descriptor.get('samples',0),
                state_basis=converted['state_basis'],training_admission=converted['training_admission'],
                dataset_manifest=converted['dataset_manifest'],action_names=converted['action_names'],
                action_basis=converted['action_basis'],dispatch_confirmed=False,lerobot_v3=exported,qualification=converted['qualification'],
                result_scope='Raw observations plus qualified host-target labels only where available; no physical success inferred')
    sys.path.insert(0,str(Path(config['root'])/'01-autonomous-learning/src'))
    from explorer_sim.station_adapter import convert_station_episode
    from explorer_sim.dataset_export import export_lerobot_v3
    target = store.root/'datasets'/(source+'-'+episode_id)
    if mobile:
        from explorer_sim.mobile_adapter import convert_mobile_episode
        converted=convert_mobile_episode(destination/'episode.json',target)
    else:
        converted = convert_station_episode(destination/'manifest.json',target)
    exported = export_lerobot_v3(target/'manifest.json',target/'lerobot_v3')
    return dict(episode_id=episode_id,dataset_manifest=str(target/'manifest.json'),
        source_archive_sha256=digest,source_manifest_sha256=converted.get('source_manifest_sha256',converted.get('source_episode_sha256')),
        frames=sum(item['frames'] for item in converted['episodes']),state_basis=converted['state_basis'],
        action_basis=converted['action_basis'],action_names=converted['action_names'],
        training_admission=converted.get('training_admission'),
        lerobot_v3=exported,source_original_retained=True,physical_success_inferred=False)


def main(job_id):
    config = load()
    store = Store(config['data_dir'])
    job = store.job(job_id)
    if not job:
        raise SystemExit('Unknown job')
    folder = store.root / 'jobs' / job_id
    folder.mkdir(parents=True, exist_ok=True)
    try:
        if job['kind'] == 'camera_recording':
            result = recording(store, job, config)
        elif job['kind'] in ('station_import','mobile_import'):
            result = station_import(store,job,config)
        elif job['kind'] in ('station_shadow','station_rollout'):
            from .station_runtime import run_station_policy
            def interrupted(signum,frame):
                raise RuntimeError('Interactive policy process interrupted')
            signal.signal(signal.SIGTERM,interrupted)
            signal.signal(signal.SIGINT,interrupted)
            result=run_station_policy(store,job,config)
        elif job['kind'] == 'sim_pilot':
            from .simulation import run_sim_pilot
            result = run_sim_pilot(store, job, config)
        elif job['kind'] == 'train_act':
            from .simulation import run_train_act
            result = run_train_act(store, job, config)
        elif job['kind']=='world_model':
            sys.path.insert(0,str(Path(config['root'])/'01-autonomous-learning/src'))
            from explorer_sim.world_model import run_world_model
            result=run_world_model(store,job)
        elif job['kind']=='dagger':
            sys.path.insert(0,str(Path(config['root'])/'01-autonomous-learning/src'))
            from explorer_sim.dagger import run_dagger
            result=run_dagger(store,job)
        elif job['kind']=='skill_cycle':
            sys.path.insert(0,str(Path(config['root'])/'01-autonomous-learning/src'))
            from explorer_sim.skill_cycle import run_skill_cycle
            result=run_skill_cycle(store,job)
        elif job['kind']=='program_trace':
            source=store.job(job['config'].get('source_job_id',''))
            if not source or source['kind']!='skill_cycle' or source['status']!='completed':
                raise ValueError('Program trace requires a completed skill-cycle source')
            sys.path.insert(0,str(Path(config['root'])/'01-autonomous-learning/src'))
            from explorer_sim.program_trace import export_program_trace
            result=export_program_trace(store.root/'jobs'/source['id']/'result.json',folder/'program_trace')
        elif job['kind'] in ('manipulation', 'manipulation_student', 'policy_transfer'):
            sys.path.insert(0,str(Path(config['root'])/'01-autonomous-learning/src'))
            if job['kind'] == 'manipulation':
                from explorer_sim.manipulation import run_manipulation
                result = run_manipulation(store, job)
            elif job['kind'] == 'manipulation_student':
                from explorer_sim.manipulation_student import run_manipulation_student
                result = run_manipulation_student(store, job)
            else:
                from .transfer import prepare_policy_transfer
                from explorer_sim.policy_transfer import run_policy_transfer
                job['config'] = prepare_policy_transfer(store, job['config'])
                result = run_policy_transfer(store, job, config)
        elif job['kind']=='handover_runtime_test':
            from .transfer import run_handover_runtime_test
            result = run_handover_runtime_test(store,job)
        elif job['kind'] in ('virtual_transfer', 'handover_test'):
            from .transfer import run_virtual_experiment
            result = run_virtual_experiment(store, job, config)
        else:
            raise ValueError('Unsupported job kind')
        result['execution_commit']=job['config'].get('_execution_commit')
        temporary=folder/'result.json.tmp'
        with temporary.open('w') as stream:
            json.dump(result,stream,ensure_ascii=False,indent=2,allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(folder/'result.json')
        store.complete_job(job_id,result)
    except Exception as error:
        traceback.print_exc()
        store.update_job(job_id, status='failed', error=type(error).__name__ + ': ' + str(error))
        raise SystemExit(1)


if __name__ == '__main__':
    main(sys.argv[1])
