"""ENGINEERING_GLUE: Local/SSH administrative CLI; credentials never embedded in source."""
import argparse
import hashlib
import json
import os
import secrets
import subprocess
import sys
import time
import uuid
from pathlib import Path
from .config import load
from .store import Store


def private_parent(path):
    """Create only missing backup ancestors with private modes; preserve existing user folders."""
    missing = []
    current = path
    while not current.exists():
        missing.append(current)
        current = current.parent
    if not current.is_dir():
        raise ValueError('Backup parent is not a directory')
    for directory in reversed(missing):
        directory.mkdir(mode=0o700)


def maintenance(args, parser):
    """Dispatch administrative tools before any live Store construction."""
    root = Path(__file__).resolve().parents[3]
    sys.path.insert(0, str(root / 'scripts'))
    from maintenance_common import MaintenanceError, replace_private_json, sync_directories, test_restore
    from rollback_release import RollbackError
    import sqlite3
    home = Path.home()
    config_path = Path(os.environ.get('EXPLORER_CONFIG', home / '.config/explorer/config.json')).expanduser()
    if args.apply and args.command != 'rollback':
        parser.error('--apply is only available for rollback; restore-check is always isolated')
    try:
        if args.command == 'doctor':
            if args.node == 'nano':
                parser.error('Doctor supports homebrain/pi; Nano maintenance remains unchanged')
            if config_path.resolve() != (home / '.config/explorer/config.json').resolve():
                parser.error('Doctor currently reads the standard local config.json; alternate EXPLORER_CONFIG is refused')
            from doctor import doctor
            result = doctor(home, root, args.node, no_http=args.no_http)
        elif args.command == 'backup':
            if config_path.name != 'config.json':
                parser.error('Metadata backup requires the selected configuration file to be named config.json')
            from backup_state import create_backup
            config = load()
            destination = args.output or home / '.local/state/explorer/backups' / ('metadata-' + time.strftime('%Y%m%dT%H%M%SZ', time.gmtime()) + '-' + uuid.uuid4().hex[:8])
            destination = destination.expanduser()
            private_parent(destination.parent)
            if args.output is None:
                destination.parent.chmod(0o700)
            staged = home / '.local/state/explorer/backup-staging/pi-original-calibration'
            calibration = args.calibration or (staged if staged.is_dir() else home / '.cache/huggingface/lerobot/calibration')
            result = create_backup(Path(config['data_dir']), config_path.parent, calibration.expanduser(), root, destination)
            result['metadata_only'] = True
            replace_private_json(Path(result['backup_directory']) / 'receipt.json', result)
            sync_directories(Path(result['backup_directory']))
        elif args.command == 'restore-check':
            if args.backup is None:
                parser.error('restore-check requires --backup DIRECTORY')
            result = test_restore(args.backup.expanduser().resolve(), root)
            result.update(metadata_only=True, live_data_changed=False, controller_settings_changed=False)
        else:
            from rollback_release import rollback
            config = load() if args.node == 'homebrain' and config_path.is_file() else None
            result = rollback(home, args.node, release=args.release,
                              state=Path(config['data_dir']) if config else None, apply=args.apply)
        print(json.dumps(result, indent=2, ensure_ascii=False))
    except (MaintenanceError, RollbackError, OSError, ValueError, RuntimeError, sqlite3.Error, ImportError, KeyError, TypeError) as error:
        print(json.dumps({'ok': False, 'error_type': type(error).__name__,
                          'detail': str(error) if isinstance(error, RollbackError) else 'Administrative check did not complete; no controller settings were changed',
                          'receipt': getattr(error, 'receipt', None)}, ensure_ascii=False))
        raise SystemExit(1)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=['status', 'init', 'login-url', 'doctor', 'backup', 'restore-check', 'rollback', 'worker', 'serve'])
    parser.add_argument('--host', default='0.0.0.0')
    parser.add_argument('--port', type=int, default=8088)
    parser.add_argument('--node', choices=['homebrain', 'pi', 'nano'], default='homebrain')
    parser.add_argument('--no-http', action='store_true', help='Doctor: inspect local files only')
    parser.add_argument('--output', type=Path, help='Backup: new private metadata folder')
    parser.add_argument('--calibration', type=Path, help='Backup: explicitly staged original calibration directory')
    parser.add_argument('--backup', type=Path, help='Restore-check: existing metadata backup folder')
    parser.add_argument('--release', help='Rollback: full commit or managed release path; defaults to previous-release')
    parser.add_argument('--apply', action='store_true', help='Rollback only: apply on idle Homebrain')
    args = parser.parse_args()
    if args.apply and args.command != 'rollback':
        parser.error('--apply is only available for rollback; restore-check is always isolated')
    if args.command in ('doctor', 'backup', 'restore-check', 'rollback'):
        maintenance(args, parser)
        return
    if args.command == 'init':
        path = Path(os.environ.get('EXPLORER_CONFIG', Path.home() / '.config/explorer/config.json'))
        if path.exists():
            raise SystemExit('Configuration already exists')
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        password = secrets.token_urlsafe(18)
        salt = secrets.token_hex(16)
        config = dict(data_dir=str(Path.home() / '.local/share/explorer/state'),
            public_url='http://homebrain.local:8088', password_salt=salt,
            password_hash=hashlib.pbkdf2_hmac('sha256', password.encode(), salt.encode(), 310000).hex(),
            ingest_token=secrets.token_urlsafe(32), nodes=[
                dict(id='pi', name='Станция рук', host='explorer-pi.local', port=8091, token=secrets.token_urlsafe(32)),
                dict(id='nano', name='ROSMASTER', host='explorer.local', port=8092, token=secrets.token_urlsafe(32))])
        path.write_text(json.dumps(config, indent=2))
        path.chmod(0o600)
        access = path.parent / 'access-code.txt'
        access.write_text(password + '\n')
        access.chmod(0o600)
        print('Initialized. Browser login: explorerctl login-url. Access code saved privately.')
    else:
        config = load()
        store = Store(config['data_dir'])
        if args.command == 'status':
            print('Explorer: ' + config['public_url'])
            for node in config['nodes']:
                seen = store.nodes().get(node['id'], {}).get('last_seen', 0)
                print(node['name'] + ': ' + ('online' if time.time() - seen < 20 else 'offline'))
            jobs = store.jobs(100)
            print('Заданий: ' + str(len(jobs)) + '; моделей: ' + str(len(store.skills())))
        if args.command == 'login-url':
            ticket = secrets.token_urlsafe(32)
            with store.connect() as db:
                db.execute('DELETE FROM tickets WHERE expires<?', (time.time(),))
                db.execute('INSERT INTO tickets VALUES (?,?)', (hashlib.sha256(ticket.encode()).hexdigest(), time.time() + 90))
            print(config['public_url'] + '/api/login?ticket=' + ticket)
        if args.command == 'serve':
            import uvicorn
            uvicorn.run('explorer.app:app', host=args.host, port=args.port, access_log=False)
        if args.command == 'worker':
            from .scheduler import supervise
            supervise(config, store)


if __name__ == '__main__':
    main()
