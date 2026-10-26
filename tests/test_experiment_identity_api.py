"""Real authenticated ASGI views across retained and canonical numbering epochs."""
import hashlib
import importlib
import json

from fastapi.testclient import TestClient

from explorer.store import Store
from explorer_shared.contracts import canonical,digest


def test_public_jobs_state_routes_and_registry_use_current_semantics_without_raw_rewrite(tmp_path,monkeypatch):
    module=importlib.import_module('explorer.app');store=Store(tmp_path/'state')
    config=dict(module.CONFIG,data_dir=str(store.root),password_salt='fixture',
        password_hash=hashlib.pbkdf2_hmac('sha256',b'fixture',b'fixture',310000).hex(),nodes=[])
    monkeypatch.setattr(module,'STORE',store);monkeypatch.setattr(module,'CONFIG',config)
    legacy_spec=dict(owner_experiment='06',skill_id='retained')
    raw_config=canonical(dict(spec=legacy_spec,spec_sha256=digest(legacy_spec))).decode()
    with store.connect() as db:
        db.execute('INSERT INTO jobs(id,kind,project_id,status,created,updated,config) VALUES (?,?,?,?,?,?,?)',
            ('legacy','station_import','06','completed',1,1,raw_config))
    current=store.create_job('policy_transfer','06',dict(project_id='06',experiment_numbering_version=2))
    with TestClient(module.app) as client:
        assert client.get('/api/jobs').status_code==401
        assert client.post('/api/login',json={'password':'fixture'}).status_code==200
        registry=client.get('/api/projects').json()
        assert registry['experiment_numbering_version']==2
        assert {row['id']:row['slug'] for row in registry['projects']}['04']=='so101_source_learning'
        assert {row['id']:row['slug'] for row in registry['projects']}['06']=='cross_embodiment_transfer'
        rows={row['id']:row for row in client.get('/api/jobs').json()['jobs']}
        assert rows['legacy']['project_id']=='04'
        assert rows['legacy']['config']['spec']['owner_experiment']=='04'
        assert rows['legacy']['config']['spec_sha256']==digest(legacy_spec)
        assert rows['legacy']['original_config_sha256']==hashlib.sha256(raw_config.encode()).hexdigest()
        assert rows[current['id']]['project_id']=='06' and rows[current['id']]['config']['project_id']=='06'
        views=client.get('/api/state').json()
        assert next(row for row in views['experiments'] if row['id']=='04')['latest_job']['id']=='legacy'
        redirect=client.get('/learning/so101',follow_redirects=False)
        assert redirect.status_code==307 and redirect.headers['location']=='/experiments/04'
        headers={'X-Explorer-CSRF':client.cookies['explorer_csrf']}
        rejected=client.post('/api/jobs',json=dict(kind='station_shadow',project_id='04',config=dict(source_numbering_version=1)),headers=headers)
        assert rejected.status_code==400
    with store.connect() as db:assert db.execute('SELECT config FROM jobs WHERE id=?',('legacy',)).fetchone()[0]==raw_config
