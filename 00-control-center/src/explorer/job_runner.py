"""ENGINEERING_GLUE: isolated job entry point; durable real result evidence."""
import hashlib
import json
import os
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
    if not isinstance(episode_id,str) or len(episode_id)!=32 or any(c not in '0123456789abcdef' for c in episode_id):
        raise ValueError('Invalid station episode ID')
    node = next(node for node in config['nodes'] if node['id']=='pi')
    with httpx.Client(timeout=60,trust_env=False) as client:
        response = client.get('http://'+node['host']+':'+str(node['port'])+'/station/recordings/'+episode_id+'/download',
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
    destination = store.root/'episodes'/'station'/episode_id
    marker=destination/'.archive-sha256'
    if not marker.is_file() or marker.read_text().strip()!=digest:
        stage=destination.with_name(episode_id+'.importing')
        if stage.exists():
            import shutil
            shutil.rmtree(stage)
        stage.mkdir(parents=True)
        with tarfile.open(artifact) as archive:
            archive.extractall(stage,filter='data')
        (stage/'.archive-sha256').write_text(digest+'\n')
        if destination.exists():
            import shutil
            shutil.rmtree(destination)
        stage.replace(destination)
    sys.path.insert(0,str(Path(config['root'])/'01-autonomous-learning/src'))
    from explorer_sim.station_adapter import convert_station_episode
    from explorer_sim.dataset_export import export_lerobot_v3
    target = store.root/'datasets'/('station-'+episode_id)
    converted = convert_station_episode(destination/'manifest.json',target)
    exported = export_lerobot_v3(target/'manifest.json',target/'lerobot_v3')
    return dict(episode_id=episode_id,dataset_manifest=str(target/'manifest.json'),
        source_archive_sha256=digest,source_manifest_sha256=converted['source_manifest_sha256'],
        frames=sum(item['frames'] for item in converted['episodes']),state_basis=converted['state_basis'],
        action_basis=converted['action_basis'],action_names=converted['action_names'],
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
        elif job['kind'] == 'station_import':
            result = station_import(store,job,config)
        elif job['kind'] == 'sim_pilot':
            from .simulation import run_sim_pilot
            result = run_sim_pilot(store, job, config)
        elif job['kind'] == 'train_act':
            from .simulation import run_train_act
            result = run_train_act(store, job, config)
        elif job['kind'] in ('virtual_transfer', 'handover_test'):
            from .transfer import run_virtual_experiment
            result = run_virtual_experiment(store, job, config)
        else:
            raise ValueError('Unsupported job kind')
        (folder / 'result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
        store.ingest(event('nx', job['project_id'], {'kind':'job.completed', 'job_id':job_id, 'result':result}, run_id=job_id))
        store.update_job(job_id, status='completed', progress=1, result=result, error=None)
    except Exception as error:
        traceback.print_exc()
        store.update_job(job_id, status='failed', error=type(error).__name__ + ': ' + str(error))
        raise SystemExit(1)


if __name__ == '__main__':
    main(sys.argv[1])
