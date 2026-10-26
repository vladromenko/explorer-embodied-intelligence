"""ENGINEERING_GLUE: SQLite WAL registry, immutable artifact paths and backup."""
import json
import re
import sqlite3
import time
import uuid
from pathlib import Path
from explorer_shared.contracts import canonical, digest, event


class Store:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / 'research.sqlite3'
        with self.connect() as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS events (event_id TEXT PRIMARY KEY,node_id TEXT NOT NULL,received REAL NOT NULL,sha256 TEXT NOT NULL,body TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS nodes (node_id TEXT PRIMARY KEY,received REAL NOT NULL,body TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY,kind TEXT NOT NULL,project_id TEXT NOT NULL,status TEXT NOT NULL,created REAL NOT NULL,updated REAL NOT NULL,config TEXT NOT NULL,result TEXT,progress REAL NOT NULL DEFAULT 0,error TEXT);
            CREATE TABLE IF NOT EXISTS skills (id TEXT PRIMARY KEY,created REAL NOT NULL,body TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS sessions (token_hash TEXT PRIMARY KEY,expires REAL NOT NULL,csrf TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS tickets (token_hash TEXT PRIMARY KEY,expires REAL NOT NULL);
            ''')

    def connect(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA journal_mode=WAL')
        db.execute('PRAGMA synchronous=FULL')
        db.execute('PRAGMA foreign_keys=ON')
        return db

    def ingest(self, body):
        for key in ('event_id', 'node_id', 'schema_version', 'clock_domain', 'monotonic_ns', 'wall_utc', 'payload', 'sha256'):
            if key not in body:
                raise ValueError('Event field missing: ' + key)
        if body['schema_version'] != 1 or digest(body['payload']) != body['sha256']:
            raise ValueError('Event schema/hash mismatch')
        if not isinstance(body['payload'], dict):
            raise ValueError('Event payload must be an object')
        serialized = canonical(body).decode()
        with self.connect() as db:
            old = db.execute('SELECT body FROM events WHERE event_id=?', (body['event_id'],)).fetchone()
            if old and old['body'] != serialized:
                raise ValueError('Conflicting event identity')
            inserted = db.execute('INSERT OR IGNORE INTO events VALUES (?,?,?,?,?)',
                (body['event_id'], body['node_id'], time.time(), body['sha256'], serialized)).rowcount
            if body['payload'].get('type') == 'inventory':
                db.execute('INSERT INTO nodes VALUES (?,?,?) ON CONFLICT(node_id) DO UPDATE SET received=excluded.received,body=excluded.body',
                    (body['node_id'], time.time(), json.dumps(body['payload'].get('inventory', body['payload']))))
        return bool(inserted)

    def update_node(self, node_id, body):
        with self.connect() as db:
            db.execute('INSERT INTO nodes VALUES (?,?,?) ON CONFLICT(node_id) DO UPDATE SET received=excluded.received,body=excluded.body',
                (node_id, time.time(), json.dumps(body, allow_nan=False)))

    def nodes(self):
        with self.connect() as db:
            return {row['node_id']: dict(last_seen=row['received'], **json.loads(row['body'])) for row in db.execute('SELECT * FROM nodes')}

    def create_job(self, kind, project_id, config):
        job_id = uuid.uuid4().hex
        now = time.time()
        with self.connect() as db:
            db.execute('INSERT INTO jobs(id,kind,project_id,status,created,updated,config) VALUES (?,?,?,?,?,?,?)',
                (job_id, kind, project_id, 'queued', now, now, json.dumps(config, allow_nan=False)))
        return self.job(job_id)

    def create_import_job(self,kind,project_id,episode_id):
        if kind not in ('station_import','mobile_import'):
            raise ValueError('Unknown immutable import kind')
        job_id=uuid.uuid4().hex
        now=time.time()
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            previous=db.execute("SELECT * FROM jobs WHERE kind=? AND json_extract(config,'$.episode_id')=? ORDER BY created LIMIT 1",(kind,episode_id)).fetchone()
            if previous:
                return self.decode_job(previous)
            db.execute('INSERT INTO jobs(id,kind,project_id,status,created,updated,config) VALUES (?,?,?,?,?,?,?)',
                (job_id,kind,project_id,'queued',now,now,json.dumps({'episode_id':episode_id})))
        return self.job(job_id)

    def pin_job_release(self,job_id,commit):
        if not re.fullmatch('[0-9a-f]{40}',commit):
            raise ValueError('Execution release must be a complete commit identity')
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT config FROM jobs WHERE id=?',(job_id,)).fetchone()
            if row is None:
                raise ValueError('Unknown job')
            settings=json.loads(row['config'])
            pinned=settings.get('_execution_commit')
            if pinned is None:
                settings['_execution_commit']=commit
                db.execute('UPDATE jobs SET config=? WHERE id=?',(json.dumps(settings),job_id))
                pinned=commit
            if not isinstance(pinned,str) or not re.fullmatch('[0-9a-f]{40}',pinned):
                raise ValueError('Stored execution release identity is invalid')
        return pinned

    def complete_job(self,job_id,result):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT * FROM jobs WHERE id=?',(job_id,)).fetchone()
            if row is None:
                raise ValueError('Unknown job')
            terminal='cancelled' if row['status'] in ('cancel_requested','cancelled') else 'completed'
            ident='job.completed:'+job_id
            previous=db.execute('SELECT body FROM events WHERE event_id=?',(ident,)).fetchone()
            if previous:
                saved=json.loads(previous['body'])
                if saved['payload']['result']!=result:
                    raise ValueError('Completed job evidence cannot change')
                terminal=saved['payload']['status']
            else:
                body=event('nx',row['project_id'],{'kind':'job.finalized','job_id':job_id,'status':terminal,'result':result},run_id=job_id)
                body['event_id']=ident
                db.execute('INSERT INTO events VALUES (?,?,?,?,?)',(ident,'nx',time.time(),body['sha256'],canonical(body).decode()))
            db.execute('UPDATE jobs SET status=?,progress=?,result=?,error=NULL,updated=? WHERE id=?',
                (terminal,1,json.dumps(result,allow_nan=False),time.time(),job_id))
        return terminal

    def jobs(self, limit=100):
        with self.connect() as db:
            rows = db.execute('SELECT * FROM jobs ORDER BY created DESC LIMIT ?', (limit,)).fetchall()
        return [self.decode_job(row) for row in rows]

    def job(self, job_id):
        with self.connect() as db:
            row = db.execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone()
        return self.decode_job(row) if row else None

    def decode_job(self, row):
        result = dict(row)
        for key in ('config', 'result'):
            result[key] = json.loads(result[key]) if result[key] else None
        frame = self.root / 'jobs' / result['id'] / 'frame.jpg'
        result['frame_available'] = frame.is_file()
        return result

    def update_job(self, job_id, **values):
        permitted = {'status', 'result', 'progress', 'error'}
        if not set(values).issubset(permitted):
            raise ValueError('Unknown job field')
        values['updated'] = time.time()
        if 'result' in values:
            values['result'] = json.dumps(values['result'], allow_nan=False)
        with self.connect() as db:
            db.execute('UPDATE jobs SET ' + ','.join(key + '=?' for key in values) + ' WHERE id=?', (*values.values(), job_id))

    def add_skill(self, card):
        required = {'id', 'task', 'source_type', 'action_contract', 'lineage', 'evaluation', 'status', 'limitations'}
        if not required.issubset(card):
            raise ValueError('Incomplete SkillCard')
        if card['status'] == 'physically_validated' and not card['evaluation'].get('physical_run_ids'):
            raise ValueError('Physical validation requires physical run evidence')
        with self.connect() as db:
            db.execute('INSERT INTO skills VALUES (?,?,?)', (card['id'], time.time(), canonical(card).decode()))

    def skills(self):
        with self.connect() as db:
            return [json.loads(row['body']) for row in db.execute('SELECT body FROM skills ORDER BY created DESC')]

    def backup(self, destination):
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as source, sqlite3.connect(destination) as target:
            source.backup(target)
            if target.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise ValueError('Backup integrity verification failed')
        return destination

