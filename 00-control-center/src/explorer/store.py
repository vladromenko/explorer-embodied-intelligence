"""ENGINEERING_GLUE: SQLite WAL registry, immutable artifact paths and backup."""
import json
import hashlib
import math
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
            CREATE TABLE IF NOT EXISTS experiment_runtime_identity (entity_kind TEXT NOT NULL,entity_id TEXT NOT NULL,numbering_version INTEGER NOT NULL,experiment_slug TEXT NOT NULL,PRIMARY KEY(entity_kind,entity_id));
            CREATE TABLE IF NOT EXISTS job_scheduling(job_id TEXT PRIMARY KEY REFERENCES jobs(id),revision TEXT NOT NULL,metadata TEXT NOT NULL,runtime TEXT NOT NULL,updated REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS job_scheduling_history(id TEXT PRIMARY KEY,job_id TEXT NOT NULL,created REAL NOT NULL,sha256 TEXT NOT NULL,body TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS scheduler_usage(experiment_slug TEXT PRIMARY KEY,service_s REAL NOT NULL,updated REAL NOT NULL);
            ''')
        from .experiment_projection import ExperimentProjection
        self.experiment_projection=ExperimentProjection(self.root)

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
        from explorer_shared.experiments import experiment_slug
        epoch=body.get('experiment_numbering_version',1)
        if type(epoch) is not int or epoch not in (1,2):raise ValueError('Unsupported event experiment numbering version')
        if body.get('experiment_slug') is not None:
            if experiment_slug(body.get('project_id'),numbering_version=epoch)!=body['experiment_slug']:raise ValueError('Event semantic experiment identity conflicts with its numbering version')
        serialized = canonical(body).decode()
        with self.connect() as db:
            old = db.execute('SELECT body FROM events WHERE event_id=?', (body['event_id'],)).fetchone()
            if old and old['body'] != serialized:
                raise ValueError('Conflicting event identity')
            inserted = db.execute('INSERT OR IGNORE INTO events VALUES (?,?,?,?,?)',
                (body['event_id'], body['node_id'], time.time(), body['sha256'], serialized)).rowcount
            from .experiment_projection import record_runtime_identity
            record_runtime_identity(db,'event',body['event_id'],body.get('project_id'),numbering_version=body.get('experiment_numbering_version',1))
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
        from explorer_shared.experiments import canonical_experiment
        project_id=canonical_experiment(project_id,numbering_version=2)
        job_id = uuid.uuid4().hex
        now = time.time()
        with self.connect() as db:
            db.execute('INSERT INTO jobs(id,kind,project_id,status,created,updated,config) VALUES (?,?,?,?,?,?,?)',
                (job_id, kind, project_id, 'queued', now, now, json.dumps(config, allow_nan=False)))
            from .experiment_projection import record_runtime_identity
            record_runtime_identity(db,'job',job_id,project_id)
        return self.job(job_id)

    def create_import_job(self,kind,project_id,episode_id,source_sha256=None,*,source_format=None):
        from explorer_shared.experiments import canonical_experiment
        project_id=canonical_experiment(project_id,numbering_version=2)
        if kind not in ('station_import','mobile_import','retarget_import'):
            raise ValueError('Unknown immutable import kind')
        from explorer_shared.experiments import experiment_id
        role={'station_import':'so101_source_learning','mobile_import':'rosmaster_keyboard_learning','retarget_import':'rosmaster_leader_learning'}[kind]
        if project_id!=experiment_id(role):raise ValueError('Original recording producer belongs to a different semantic experiment')
        settings={'episode_id':episode_id}
        if source_format not in (None,'native_capture_v1'):raise ValueError('Unknown source producer format')
        if source_format=='native_capture_v1':
            if kind!='mobile_import' or project_id!='02':raise ValueError('Native mobile import is the direct02 producer; leader03 retains its own proxy import')
            settings['source_format']=source_format
        if kind=='retarget_import' or source_format=='native_capture_v1':
            if not isinstance(source_sha256,str) or not re.fullmatch('[0-9a-f]{64}',source_sha256):
                raise ValueError('Direct teaching import requires the immutable source archive SHA256')
            settings['archive_sha256']=source_sha256
        job_id=uuid.uuid4().hex
        now=time.time()
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            previous=db.execute("SELECT * FROM jobs WHERE kind=? AND json_extract(config,'$.episode_id')=? ORDER BY created DESC LIMIT 1",(kind,episode_id)).fetchone()
            if previous:
                if (kind=='retarget_import' or source_format=='native_capture_v1') and json.loads(previous['config']).get('archive_sha256')!=source_sha256:
                    raise ValueError('Conflicting direct teaching archive for the same original recording')
                if source_format and json.loads(previous['config']).get('source_format')!=source_format:raise ValueError('Original recording producer identity changed')
                return self.decode_job(previous)
            db.execute('INSERT INTO jobs(id,kind,project_id,status,created,updated,config) VALUES (?,?,?,?,?,?,?)',
                (job_id,kind,project_id,'queued',now,now,json.dumps(settings)))
            from .experiment_projection import record_runtime_identity
            record_runtime_identity(db,'job',job_id,project_id)
        return self.job(job_id)

    def retry_import_job(self,job_id):
        """An explicit copy retry has its own lineage; the failed attempt is retained."""
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            old=db.execute('SELECT * FROM jobs WHERE id=?',(job_id,)).fetchone()
            if old is None or old['kind'] not in ('station_import','mobile_import','retarget_import') or old['status'] not in ('failed','cancelled'):
                raise ValueError('Only a terminal failed/cancelled data import can be retried')
            child=db.execute("SELECT * FROM jobs WHERE json_extract(config,'$.retry_of_job_id')=? ORDER BY created DESC LIMIT 1",(job_id,)).fetchone()
            if child:return self.decode_job(child)
            original_settings=json.loads(old['config'])
            projected=self.experiment_projection.identity(db,'job',old['id'],old['project_id'],declared_version=original_settings.get('experiment_numbering_version'))
            from .experiment_projection import project_document
            settings=project_document(original_settings,numbering_version=projected.get('original_numbering_version',1))
            for key in ('_execution_commit','_gpu_binding_sha256','_policy_runtime_binding_sha256'):settings.pop(key,None)
            settings['retry_of_job_id']=job_id
            ident=uuid.uuid4().hex;now=time.time()
            db.execute('INSERT INTO jobs(id,kind,project_id,status,created,updated,config) VALUES (?,?,?,?,?,?,?)',
                (ident,old['kind'],old['project_id'],'queued',now,now,json.dumps(settings)))
            from .experiment_projection import record_runtime_identity
            settings['source_numbering_version']=projected.get('original_numbering_version',1)
            settings['experiment_numbering_version']=2
            settings['experiment_slug']=projected.get('experiment_slug')
            db.execute('UPDATE jobs SET config=? WHERE id=?',(json.dumps(settings),ident))
            record_runtime_identity(db,'job',ident,projected['project_id'])
            if projected['project_id']!=old['project_id']:db.execute('UPDATE jobs SET project_id=? WHERE id=?',(projected['project_id'],ident))
        return self.job(ident)

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

    def pin_job_gpu_binding(self,job_id,commit,binding_sha256):
        if not re.fullmatch('[0-9a-f]{64}',binding_sha256):
            raise ValueError('GPU binding requires its immutable SHA256')
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT config FROM jobs WHERE id=?',(job_id,)).fetchone()
            if row is None:raise ValueError('Unknown job')
            settings=json.loads(row['config'])
            if settings.get('_execution_commit')!=commit:
                raise ValueError('GPU source binding differs from pinned execution source')
            previous=settings.get('_gpu_binding_sha256')
            if previous is not None and previous!=binding_sha256:
                raise ValueError('Existing GPU qualification binding cannot silently change')
            settings['_gpu_binding_sha256']=binding_sha256
            db.execute('UPDATE jobs SET config=? WHERE id=?',(json.dumps(settings),job_id))
        return binding_sha256

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
                projected=self.experiment_projection.identity(db,'job',job_id,row['project_id'],declared_version=json.loads(row['config']).get('experiment_numbering_version'))
                body=event('nx',projected['project_id'],{'kind':'job.finalized','job_id':job_id,'status':terminal,'result':result,
                    'experiment_numbering_version':projected.get('original_numbering_version',1)},run_id=job_id)
                body['event_id']=ident
                db.execute('INSERT INTO events VALUES (?,?,?,?,?)',(ident,'nx',time.time(),body['sha256'],canonical(body).decode()))
            db.execute('UPDATE jobs SET status=?,progress=?,result=?,error=NULL,updated=? WHERE id=?',
                (terminal,1,json.dumps(result,allow_nan=False),time.time(),job_id))
        return terminal

    def pin_job_policy_binding(self,job_id,binding_sha256):
        if not re.fullmatch('[0-9a-f]{64}',binding_sha256):raise ValueError('Policy runtime binding requires an immutable SHA256')
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT config FROM jobs WHERE id=?',(job_id,)).fetchone()
            if row is None:raise ValueError('Unknown job')
            settings=json.loads(row['config']);previous=settings.get('_policy_runtime_binding_sha256')
            if previous is not None and previous!=binding_sha256:raise ValueError('Existing policy runtime binding cannot silently change')
            settings['_policy_runtime_binding_sha256']=binding_sha256
            db.execute('UPDATE jobs SET config=? WHERE id=?',(json.dumps(settings),job_id))
        return binding_sha256

    def jobs(self, limit=100):
        with self.connect() as db:
            rows = db.execute('SELECT * FROM jobs ORDER BY created DESC LIMIT ?', (limit,)).fetchall()
        return [self.decode_job(row) for row in rows]

    def queue_jobs(self):
        """All nonterminal jobs plus old dependency rows, from the same jobs table."""
        with self.connect() as db:
            rows=db.execute("SELECT * FROM jobs WHERE status NOT IN ('completed','failed','cancelled') OR id IN (SELECT value FROM job_scheduling,json_each(metadata,'$.dependencies')) ORDER BY created").fetchall()
        return [self.decode_job(row) for row in rows]

    def job(self, job_id):
        with self.connect() as db:
            row = db.execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone()
        return self.decode_job(row) if row else None

    def decode_job(self, row):
        result = dict(row)
        for key in ('config', 'result'):
            result[key] = json.loads(result[key]) if result[key] else None
        with self.connect() as db:
            identity=self.experiment_projection.identity(db,'job',result['id'],result['project_id'],declared_version=(result.get('config') or {}).get('experiment_numbering_version'))
            schedule=db.execute('SELECT * FROM job_scheduling WHERE job_id=?',(result['id'],)).fetchone()
        result.update(identity)
        if schedule:result['scheduling']=self._decode_schedule(schedule)
        frame = self.root / 'jobs' / result['id'] / 'frame.jpg'
        result['frame_available'] = frame.is_file()
        metadata = frame.with_name('frame.json')
        if metadata.is_file() and metadata.stat().st_size <= 16384:
            try:
                result['frame_metadata'] = json.loads(metadata.read_text())
            except (OSError,ValueError):
                pass
        training = frame.with_name('act_training.json')
        if training.is_file() and training.stat().st_size <= 4*1024**2:
            try:
                receipt = json.loads(training.read_text())
                losses = receipt.get('losses', []) if isinstance(receipt,dict) else []
                last = losses[-1] if isinstance(losses,list) and losses and isinstance(losses[-1],dict) else {}
                if (type(last.get('step')) is int and last['step'] == len(losses)
                        and type(last.get('loss')) in (int,float) and math.isfinite(last['loss'])):
                    result['completed_updates'] = last['step']
                    result['metrics'] = {'updates':last['step'],'loss':last['loss'],'device':receipt.get('device'),
                        'basis':'retained_actual_optimizer_receipt'}
            except (OSError,ValueError,TypeError,KeyError):
                pass
        return result

    def update_job(self, job_id, **values):
        permitted = {'status', 'result', 'progress', 'error'}
        if not set(values).issubset(permitted):
            raise ValueError('Unknown job field')
        values['updated'] = time.time()
        if 'result' in values:
            values['result'] = json.dumps(values['result'], allow_nan=False)
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE');current=db.execute('SELECT status FROM jobs WHERE id=?',(job_id,)).fetchone()
            if current is None:raise ValueError('Unknown job')
            if current['status'] in ('completed','failed','cancelled'):return
            if current['status']=='cancel_requested' and values.get('status') not in (None,'cancel_requested','cancelled'):
                values.pop('status')
            db.execute('UPDATE jobs SET ' + ','.join(key + '=?' for key in values) + ' WHERE id=?', (*values.values(), job_id))

    def schedule_status(self,job_id):
        from .queue_policy import default_metadata
        job=self.job(job_id)
        if job is None:raise ValueError('Unknown job')
        with self.connect() as db:row=db.execute('SELECT * FROM job_scheduling WHERE job_id=?',(job_id,)).fetchone()
        if row:return dict(self._decode_schedule(row),minimum_resources=default_metadata(job)['resources'])
        metadata=default_metadata(job)
        return dict(metadata=metadata,revision=digest(metadata),runtime={},persisted=False,minimum_resources=metadata['resources'])

    @staticmethod
    def _decode_schedule(row):
        metadata=json.loads(row['metadata'])
        if digest(metadata)!=row['revision']:raise ValueError('Scheduling metadata SHA changed')
        return dict(metadata=metadata,revision=row['revision'],runtime=json.loads(row['runtime']),persisted=True)

    def schedule_job(self,job_id,options=None,*,expected_revision=None):
        from .queue_policy import default_metadata,validate_options
        job=self.job(job_id)
        if job is None:raise ValueError('Unknown job')
        if options is not None and not isinstance(options,dict):raise ValueError('Scheduling options must be an object')
        if options and job['status'] in ('completed','failed','cancelled'):raise ValueError('Terminal scheduling history is immutable')
        if options and job['status']!='queued' and set(options)&{'dependencies','not_before','resources'}:raise ValueError('Active scheduling dependencies/budgets cannot change; adjust priority/preemption only')
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE');row=db.execute('SELECT * FROM job_scheduling WHERE job_id=?',(job_id,)).fetchone()
            old=json.loads(row['metadata']) if row else default_metadata(job);old_revision=row['revision'] if row else digest(old)
            if expected_revision is not None and expected_revision!=old_revision:raise ValueError('Scheduling metadata changed; refresh before editing')
            metadata=validate_options(job,old,{} if options is None else options)
            graph={entry['job_id']:json.loads(entry['metadata'])['dependencies'] for entry in db.execute('SELECT job_id,metadata FROM job_scheduling')}
            graph[job_id]=metadata['dependencies']
            for identity in metadata['dependencies']:
                if db.execute('SELECT 1 FROM jobs WHERE id=?',(identity,)).fetchone() is None:raise ValueError('Unknown dependency: '+identity)
            pending=[(job_id,False)];visiting=set();done=set()
            while pending:
                identity,leaving=pending.pop()
                if leaving:visiting.remove(identity);done.add(identity)
                elif identity in visiting:raise ValueError('Job dependency cycle is forbidden')
                elif identity not in done:
                    visiting.add(identity);pending.append((identity,True));pending.extend((target,False) for target in graph.get(identity,[]))
            revision=digest(metadata)
            if row is None or revision!=old_revision:
                runtime=row['runtime'] if row else '{}';now=time.time()
                db.execute('INSERT INTO job_scheduling VALUES(?,?,?,?,?) ON CONFLICT(job_id) DO UPDATE SET revision=excluded.revision,metadata=excluded.metadata,updated=excluded.updated',
                    (job_id,revision,json.dumps(metadata,allow_nan=False),runtime,now))
                body=dict(schema='explorer_job_schedule_change_v1',job_id=job_id,previous_revision=old_revision if row else None,revision=revision,metadata=metadata)
                db.execute('INSERT INTO job_scheduling_history VALUES(?,?,?,?,?)',(uuid.uuid4().hex,job_id,now,digest(body),canonical(body).decode()))
        return self.schedule_status(job_id)

    def update_schedule_runtime(self,job_id,**values):
        if not self.schedule_status(job_id)['persisted']:self.schedule_job(job_id)
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE');row=db.execute('SELECT runtime FROM job_scheduling WHERE job_id=?',(job_id,)).fetchone()
            runtime=json.loads(row['runtime']);runtime.update(values)
            db.execute('UPDATE job_scheduling SET runtime=?,updated=? WHERE job_id=?',(json.dumps(runtime,allow_nan=False),time.time(),job_id))
        return runtime

    def scheduling_usage(self):
        with self.connect() as db:return {row['experiment_slug']:row['service_s'] for row in db.execute('SELECT * FROM scheduler_usage')}

    def charge_scheduling_usage(self,experiment_slug,seconds):
        if not isinstance(experiment_slug,str) or type(seconds) not in (int,float) or not math.isfinite(seconds) or seconds<0:raise ValueError('Finite nonnegative budget-service charge required')
        with self.connect() as db:
            db.execute('INSERT INTO scheduler_usage VALUES(?,?,?) ON CONFLICT(experiment_slug) DO UPDATE SET service_s=service_s+excluded.service_s,updated=excluded.updated',(experiment_slug,seconds,time.time()))

    def claim_job_start(self,job_id):
        with self.connect() as db:
            return bool(db.execute("UPDATE jobs SET status='running',updated=? WHERE id=? AND status IN ('queued','resume_requested')",(time.time(),job_id)).rowcount)

    def request_job_operation(self,job_id,operation):
        from .queue_policy import PHYSICAL_JOBS,default_metadata
        job=self.job(job_id);desired={'pause':'pause_requested','resume':'resume_requested','cancel':'cancel_requested'}
        if job is None or operation not in desired:raise ValueError('Operation is not valid for this job')
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE');current=db.execute('SELECT status,kind FROM jobs WHERE id=?',(job_id,)).fetchone()
            if current['status'] in ('completed','failed','cancelled'):raise ValueError('Operation is not valid for this terminal job')
            if current['status']=='cancel_requested' and operation!='cancel':raise ValueError('Cancellation is pending; an older pause/resume cannot replace cancel intent')
            if operation in ('pause','resume') and current['kind'] in PHYSICAL_JOBS:raise ValueError('Physical attempts require a fresh observed attempt, never pause/resume')
            row=db.execute('SELECT * FROM job_scheduling WHERE job_id=?',(job_id,)).fetchone()
            if row:record=self._decode_schedule(row)
            else:
                metadata=default_metadata(job);record=dict(metadata=metadata,revision=digest(metadata),runtime={})
                db.execute('INSERT INTO job_scheduling VALUES(?,?,?,?,?)',(job_id,record['revision'],json.dumps(metadata,allow_nan=False),'{}',time.time()))
                body=dict(schema='explorer_job_schedule_change_v1',job_id=job_id,previous_revision=None,revision=record['revision'],metadata=metadata)
                db.execute('INSERT INTO job_scheduling_history VALUES(?,?,?,?,?)',(uuid.uuid4().hex,job_id,time.time(),digest(body),canonical(body).decode()))
            runtime=dict(record['runtime'],user_control_generation=uuid.uuid4().hex,user_pause_requested=operation=='pause',auto_resume=False)
            now=time.time();db.execute('UPDATE job_scheduling SET runtime=?,updated=? WHERE job_id=?',(json.dumps(runtime,allow_nan=False),now,job_id))
            db.execute('UPDATE jobs SET status=?,updated=? WHERE id=?',(desired[operation],now,job_id))
        return self.job(job_id)

    def admit_worker_entry(self,job_id,nonce,worker):
        """Final pre-work linearization; cancellation never becomes an admission."""
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE');job=db.execute('SELECT status FROM jobs WHERE id=?',(job_id,)).fetchone()
            if job is None:raise ValueError('Unknown job')
            if job['status'] in ('completed','failed','cancelled'):return 'terminal'
            row=db.execute('SELECT * FROM job_scheduling WHERE job_id=?',(job_id,)).fetchone()
            runtime=self._decode_schedule(row)['runtime'] if row else {}
            if nonce is not None and (runtime.get('launch_nonce')!=nonce or runtime.get('worker')!=worker or job['status'] not in ('running','resume_requested','pause_requested','cancel_requested')):
                raise ValueError('Worker entry does not match its durable process/nonce lease')
            if job['status']=='cancel_requested':
                db.execute("UPDATE jobs SET status='cancelled',updated=? WHERE id=?",(time.time(),job_id));return 'cancelled'
            if job['status'] in ('pause_requested','paused'):return 'paused'
            if job['status']=='resume_requested':db.execute("UPDATE jobs SET status='running',updated=? WHERE id=?",(time.time(),job_id))
            if row:
                runtime.update(worker_entry_admitted=True)
                db.execute('UPDATE job_scheduling SET runtime=?,updated=? WHERE job_id=?',(json.dumps(runtime,allow_nan=False),time.time(),job_id))
            return 'admitted'

    def add_skill(self, card):
        required = {'id', 'task', 'source_type', 'action_contract', 'lineage', 'evaluation', 'status', 'limitations'}
        if not required.issubset(card):
            raise ValueError('Incomplete SkillCard')
        if card['status'] == 'physically_validated' and not card['evaluation'].get('physical_run_ids'):
            raise ValueError('Physical validation requires physical run evidence')
        with self.connect() as db:
            db.execute('INSERT INTO skills VALUES (?,?,?)', (card['id'], time.time(), canonical(card).decode()))
            from .experiment_projection import record_runtime_identity
            project=card.get('project_id',card.get('owner_experiment'))
            parent=card.get('lineage',{}).get('job_id')
            projected=None
            if parent:
                row=db.execute('SELECT project_id,config FROM jobs WHERE id=?',(parent,)).fetchone()
                if row:
                    projected=self.experiment_projection.identity(db,'job',parent,row['project_id'],declared_version=json.loads(row['config']).get('experiment_numbering_version'))
            epoch=card.get('experiment_numbering_version',(projected or {}).get('original_numbering_version',2))
            if project is None and projected:project=projected.get('experiment_slug',projected['project_id'])
            if project is not None:
                from explorer_shared.experiments import experiment_slug
                if projected and experiment_slug(project,numbering_version=epoch)!=projected.get('experiment_slug'):raise ValueError('SkillCard experiment conflicts with its immutable parent lineage')
                record_runtime_identity(db,'skill',card['id'],project,numbering_version=epoch)

    def skills(self):
        with self.connect() as db:
            result=[]
            from .experiment_projection import project_document
            for row in db.execute('SELECT id,body FROM skills ORDER BY created DESC').fetchall():
                card=json.loads(row['body']);project=card.get('project_id',card.get('owner_experiment'));parent=card.get('lineage',{}).get('job_id')
                identity=self.experiment_projection.identity(db,'skill',row['id'],project,raw=row['body'].encode(),declared_version=card.get('experiment_numbering_version'))
                if identity.get('project_id') is None and parent:
                    source=db.execute('SELECT project_id,config FROM jobs WHERE id=?',(parent,)).fetchone()
                    if source:
                        identity=self.experiment_projection.identity(db,'job',parent,source['project_id'],declared_version=json.loads(source['config']).get('experiment_numbering_version'))
                        identity=dict(identity,numbering_provenance=dict(source='immutable_card_lineage_job_id',job_id=parent,parent_identity=identity))
                view=project_document(card,numbering_version=card.get('experiment_numbering_version',identity.get('original_numbering_version',1)))
                if identity.get('project_id') is not None:
                    view.update(identity)
                    view['original_artifact_sha256']=hashlib.sha256(row['body'].encode()).hexdigest()
                result.append(view)
            return result

    def backup(self, destination):
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as source, sqlite3.connect(destination) as target:
            source.backup(target)
            if target.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise ValueError('Backup integrity verification failed')
        return destination

