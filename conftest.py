"""Isolate the API's import-time Store before any test can import explorer.app.

The temporary environment applies only to that import. CLI subprocess tests keep
their normal explicit configuration semantics. No test bootstrap loads the real
fleet configuration, starts a poller, imports a model or removes production data.
"""
import hashlib
import importlib
import json
import os
from pathlib import Path
import sys
import tempfile
import uuid


def pytest_sessionstart(session):
    production=(Path.home()/'.local/share/explorer/state').resolve()
    preferred=Path('/mnt/explorer-data/explorer-work')
    base=Path(os.environ.get('EXPLORER_TEST_ROOT') or (preferred if preferred.is_dir() and os.access(preferred,os.W_OK) else Path(tempfile.gettempdir())/'explorer-tests'))
    if base.resolve()==production or base.resolve().is_relative_to(production):raise RuntimeError('Test bootstrap must remain outside production state')
    root=base/('pytest-api-import-'+uuid.uuid4().hex)
    root.mkdir(parents=True,mode=0o700)
    config=root/'config.json'
    body=dict(data_dir=str(root/'state'),public_url='http://testserver',nodes=[],
        password_salt='isolated-import',password_hash=hashlib.pbkdf2_hmac('sha256',b'no-production-access',b'isolated-import',310000).hex(),
        ingest_token='isolated-import-no-fleet-credentials')
    config.write_text(json.dumps(body));config.chmod(0o600)
    previous=os.environ.get('EXPLORER_CONFIG');before=set(sys.modules)
    explicit_private=False
    if previous:
        selected=json.loads(Path(previous).expanduser().read_text())
        selected_state=Path(selected['data_dir']).expanduser().resolve()
        if selected_state!=production and not selected_state.is_relative_to(production):
            config=Path(previous).expanduser();explicit_private=True
    try:
        os.environ['EXPLORER_CONFIG']=str(config)
        module=importlib.import_module('explorer.app')
    finally:
        if previous is None:os.environ.pop('EXPLORER_CONFIG',None)
        else:os.environ['EXPLORER_CONFIG']=previous
    actual=module.STORE.root.resolve()
    if actual==production or actual.is_relative_to(production):raise RuntimeError('Tests must never initialize the production API Store')
    if 'torch' in set(sys.modules)-before:raise RuntimeError('API bootstrap must not import optional models')
    (root/'receipt.json').write_text(json.dumps(dict(schema='explorer_pytest_api_import_isolation_v1',
        isolated_api_state=str(actual),production_state_used=False,environment_restored=True,
        explicit_private_config_honored=explicit_private,fleet_nodes_configured=len(module.CONFIG.get('nodes',[])),
        optional_model_imported=False,physical_requests=0),indent=2))
