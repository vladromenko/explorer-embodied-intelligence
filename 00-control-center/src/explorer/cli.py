"""ENGINEERING_GLUE: Local/SSH administrative CLI; credentials never embedded in source."""
import argparse
import hashlib
import json
import os
import secrets
import subprocess
import sys
import time
from pathlib import Path
from .config import load
from .store import Store


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=['status', 'init', 'login-url', 'backup', 'worker', 'serve'])
    parser.add_argument('--host', default='0.0.0.0')
    parser.add_argument('--port', type=int, default=8088)
    args = parser.parse_args()
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
        if args.command == 'backup':
            target = store.root / 'backups' / ('research-' + str(int(time.time())) + '.sqlite3')
            print(store.backup(target))
        if args.command == 'serve':
            import uvicorn
            uvicorn.run('explorer.app:app', host=args.host, port=args.port, access_log=False)
        if args.command == 'worker':
            from .scheduler import supervise
            supervise(config, store)


if __name__ == '__main__':
    main()
