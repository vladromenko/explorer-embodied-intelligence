#!/usr/bin/env python3
"""ENGINEERING_GLUE: prepare a reversible, hash-bound experiment renumber COPY.

No live apply, service restart, device access or scientific-payload rewriting.
Use the semantic registry; a historical numbering version is always explicit.
"""
from __future__ import annotations

import argparse
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys

SCHEMA = 'explorer_experiment_projection_v1'
MIGRATION_TABLE = 'experiment_numbering_migrations'
TERMINAL = frozenset(('completed', 'failed', 'cancelled', 'canceled', 'rejected'))


class MigrationError(ValueError):
    pass


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def sha(value):
    return hashlib.sha256(value).hexdigest()


def _mapping(value, version):
    from explorer_shared.experiments import canonical_experiment, experiment_slug
    canonical = canonical_experiment(value, numbering_version=version)
    return canonical, experiment_slug(canonical, numbering_version=2)


def _readonly(path):
    path = Path(path).resolve(strict=True)
    db = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=30)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA query_only=ON')
    db.execute('BEGIN')
    return db


def _tables(db):
    return sorted(row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"))


def _rows(db, table):
    if table not in _tables(db):
        return []
    # SQLite schema names are quoted, never supplied as executable identifiers.
    quoted = '"' + table.replace('"', '""') + '"'
    return sorted((dict(row) for row in db.execute('SELECT * FROM ' + quoted)), key=lambda row: encoded(row))


def database_state(db, *, exclude_migration=True):
    result = {}
    for table in _tables(db):
        if not (exclude_migration and table == MIGRATION_TABLE):
            rows = _rows(db, table)
            result[table] = dict(count=len(rows), sha256=sha(encoded(rows)))
    return dict(tables=result, logical_sha256=sha(encoded(result)))


def _completed_migration(db):
    rows = _rows(db, MIGRATION_TABLE)
    completed = [row for row in rows if row['target_numbering_version'] == 2]
    if len(completed) > 1:
        raise MigrationError('Conflicting experiment numbering migration markers')
    return completed[0] if completed else None


def _entry(kind, identity, raw_bytes, original, version, *, basis, **extra):
    canonical, slug = _mapping(original, version)
    body = dict(entity_kind=kind, entity_id=identity, original_project_id=original,
                original_numbering_version=version, canonical_project_id=canonical,
                experiment_slug=slug, raw_sha256=sha(raw_bytes), basis=basis, **extra)
    return dict(body, projection_sha256=sha(encoded(body)))


def _specs(document):
    result = []
    def visit(value, pointer):
        if isinstance(value, dict):
            for key, child in value.items():
                path = pointer + '/' + key.replace('~', '~0').replace('/', '~1')
                if key in ('spec', 'skill_spec', 'original_spec') and isinstance(child, dict):
                    result.append(dict(pointer=path, original_spec=child, original_spec_sha256=sha(encoded(child))))
                else:
                    visit(child, path)
        elif isinstance(value, list):
            for index, child in enumerate(value):
                visit(child, pointer + '/' + str(index))
    visit(document, '')
    return result


def _registry_plan(db, version):
    jobs = _rows(db, 'jobs'); events = _rows(db, 'events'); skills = _rows(db, 'skills')
    entries, updates = [], []
    jobs_by_id = {row['id']: row for row in jobs}
    for row in jobs:
        original = row['project_id']; canonical, unused = _mapping(original, version)
        if canonical != original:
            active = row['status'].lower() not in TERMINAL
            entries.append(_entry('job', row['id'], encoded(row), original, version,
                basis='registry_project_id', deferred_active_pin=active,
                original_status=row['status'], original_execution_commit=json.loads(row['config']).get('_execution_commit')))
            if not active:
                updates.append(dict(id=row['id'], original_project_id=original, canonical_project_id=canonical))
    for row in events:
        document = json.loads(row['body']); original = document.get('project_id')
        if original is not None:
            canonical, unused = _mapping(original, version)
            if canonical != original:
                if document.get('sha256') != row['sha256'] or sha(encoded(document.get('payload'))) != row['sha256']:
                    raise MigrationError('Original event payload SHA mismatch: ' + row['event_id'])
                entries.append(_entry('event', row['event_id'], row['body'].encode(), original, version,
                    basis='immutable_event_envelope', original_event_id=document.get('event_id'),
                    original_payload_sha256=row['sha256'], original_specs=_specs(document)))
    for row in skills:
        card = json.loads(row['body']); owner = card.get('project_id')
        source_job = card.get('lineage', {}).get('job_id')
        if owner is None and source_job in jobs_by_id:
            owner = jobs_by_id[source_job]['project_id']; basis = 'immutable_card_lineage_job_id'
        else:
            basis = 'explicit_card_project_id'
        if owner is not None:
            canonical, unused = _mapping(owner, version)
            if canonical != owner:
                entries.append(_entry('skill', row['id'], row['body'].encode(), owner, version,
                    basis=basis, lineage_job_id=source_job, original_spec=card, original_spec_sha256=sha(encoded(card))))
    return entries, updates


def _outbox_plan(path, version, identity):
    with closing(_readonly(path)) as db:
        rows = _rows(db, 'outbox'); state = database_state(db)
    entries = []
    for row in rows:
        document = json.loads(row['payload']); original = document.get('project_id')
        if original is not None:
            canonical, unused = _mapping(original, version)
            if canonical != original:
                if document.get('sha256') is not None and sha(encoded(document.get('payload'))) != document['sha256']:
                    raise MigrationError('Original outbox payload SHA mismatch: ' + row['event_id'])
                entries.append(_entry('outbox_event', identity + ':' + row['event_id'], row['payload'].encode(), original, version,
                    basis='immutable_outbox_envelope', original_event_id=row['event_id'], delivered=row['delivered'],
                    outbox_identity=identity, original_specs=_specs(document)))
    return dict(identity=identity, source_path=str(Path(path).resolve()), database=state, entries=entries,
                total_rows=len(rows), pending_rows=sum(row['delivered'] == 0 for row in rows))


def _json_pointer(document, pointer):
    value = document
    for part in pointer.split('/')[1:]:
        key = part.replace('~1', '/').replace('~0', '~')
        value = value[int(key)] if isinstance(value, list) else value[key]
    return value


def _artifact_plan(audit_path, state, version):
    if audit_path is None:
        return [], dict(available=False, status='NOT_INVENTORIED', artifact_payloads_rewritten=False)
    audit_raw = Path(audit_path).read_bytes(); audit = json.loads(audit_raw)
    if Path(audit['managed_root']).resolve() != state.resolve():
        raise MigrationError('Lineage inventory belongs to a different managed state')
    entries, unresolved = [], []
    for file in audit['files']:
        path = Path(file['path'])
        try:
            relative = path.resolve().relative_to(state.resolve()).as_posix()
        except ValueError as error:
            raise MigrationError('Inventory artifact escapes managed state') from error
        try:
            raw = path.read_bytes()
            if sha(raw) != file['sha256']:
                raise MigrationError('Inventory bytes changed')
            document = json.loads(raw)
            for reference in file['references']:
                if _json_pointer(document, reference['pointer']) != reference['value']:
                    raise MigrationError('Inventory pointer changed')
                original = reference['value']
                if original in ('04', '06', 'personal'):
                    entries.append(_entry('artifact_reference', relative + '#' + reference['pointer'], raw, original, version,
                        basis='verified_inventory_json_pointer', path=relative, pointer=reference['pointer'], original_reference=original,
                        immutable_payload_rewritten=False))
                else:
                    unresolved.append(dict(path=relative, pointer=reference['pointer'], value=original, reason='NON_PROJECT_REFERENCE_RETAINED_NO_TEXT_REPLACEMENT'))
        except (OSError, ValueError, KeyError, IndexError, TypeError) as error:
            unresolved.append(dict(path=relative, reason='INVENTORY_SOURCE_CHANGED_OR_MISSING', error_type=type(error).__name__))
    return entries, dict(available=True, audit_sha256=sha(audit_raw), original_summary=audit.get('summary'), original_scan=audit.get('scan'),
                        verified_projection_references=len(entries), unresolved=unresolved, artifact_payloads_rewritten=False)


def dry_run(state, *, source_numbering_version=1, outboxes=(), lineage_inventory=None):
    state = Path(state).resolve(strict=True)
    with closing(_readonly(state / 'research.sqlite3')) as db:
        database = database_state(db); marker = _completed_migration(db)
        if marker:
            return dict(schema=SCHEMA, no_op=True, existing_migration=marker, database=database, live_state_modified=False)
        entries, updates = _registry_plan(db, source_numbering_version)
    outbox_reports = [_outbox_plan(path, source_numbering_version, 'outbox-' + str(index)) for index, path in enumerate(outboxes)]
    artifacts, inventory = _artifact_plan(lineage_inventory, state, source_numbering_version)
    for report in outbox_reports:
        entries.extend(report.pop('entries'))
    entries.extend(artifacts); entries.sort(key=lambda row: (row['entity_kind'], row['entity_id']))
    kinds = sorted({row['entity_kind'] for row in entries})
    core = dict(schema=SCHEMA, source_numbering_version=source_numbering_version, target_numbering_version=2,
                source_state=str(state), source_database=database, entries=entries, job_updates=updates,
                outboxes=outbox_reports, artifact_inventory=inventory)
    migration_id = 'experiment-numbers-' + sha(encoded(core))[:24]
    return dict(core, migration_id=migration_id, entries_sha256=sha(encoded(entries)),
                counts=dict(projected_by_kind={kind:sum(row['entity_kind']==kind for row in entries) for kind in kinds},
                    terminal_job_updates=len(updates), deferred_active_jobs=sum(row.get('deferred_active_pin',False) for row in entries),
                    outbox_rows=sum(row['total_rows'] for row in outbox_reports), outbox_pending=sum(row['pending_rows'] for row in outbox_reports)),
                no_op=False, live_state_modified=False, raw_scientific_artifacts_modified=False,
                scope='COPIED_DATABASE_AND_SEMANTIC_PROJECTIONS_ONLY', cross_database_atomic_snapshot=False)


def _write(path, raw):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with path.open('xb') as stream:
        stream.write(raw); stream.flush(); os.fsync(stream.fileno())
    os.chmod(path, 0o600)
    descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _snapshot(source, target):
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if target.exists() or target.is_symlink():raise MigrationError('Snapshot target already exists')
    with closing(_readonly(source)) as db, closing(sqlite3.connect(target)) as copy:
        db.backup(copy)
        if copy.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise MigrationError('Copied database failed integrity_check')
    os.chmod(target, 0o600)
    with target.open('rb') as stream:os.fsync(stream.fileno())
    descriptor=os.open(target.parent,os.O_RDONLY|os.O_DIRECTORY)
    try:os.fsync(descriptor)
    finally:os.close(descriptor)


def prepare(state, destination, *, source_numbering_version=1, outboxes=(), lineage_inventory=None):
    state, destination = Path(state).resolve(strict=True), Path(destination).resolve()
    if destination.is_symlink() or state == destination or state in destination.parents:
        raise MigrationError('Preparation destination must be outside live managed state')
    if destination.exists():
        result = verify(destination)
        with closing(_readonly(state / 'research.sqlite3')) as db:
            actual = database_state(db)['logical_sha256']
        if actual not in (result['source_database_logical_sha256'], result['candidate_database_logical_sha256']):
            raise MigrationError('Existing preparation belongs to different database contents')
        return dict(result, no_op=True)
    with closing(_readonly(state / 'research.sqlite3')) as db:
        marker = _completed_migration(db)
        if marker:
            return dict(no_op=True, existing_migration=marker, live_state_modified=False)
    destination.mkdir(mode=0o700, parents=False)
    original = destination / 'original' / 'research.sqlite3'; candidate = destination / 'candidate' / 'research.sqlite3'
    _snapshot(state / 'research.sqlite3', original)
    # Plan from the consistent COPY, with original artifacts resolved through the
    # explicit live root. The source is never opened writable.
    with closing(_readonly(original)) as db:
        entries, updates = _registry_plan(db, source_numbering_version); database = database_state(db)
    reports=[]
    for index,path in enumerate(outboxes):
        identity='outbox-'+str(index); snapshot=destination/'original'/'outboxes'/(identity+'.sqlite3')
        _snapshot(path,snapshot); report=_outbox_plan(snapshot,source_numbering_version,identity)
        report['source_path']=str(Path(path).resolve());entries.extend(report.pop('entries'));reports.append(report)
    artifact_entries, inventory = _artifact_plan(lineage_inventory, state, source_numbering_version);entries.extend(artifact_entries)
    entries.sort(key=lambda row:(row['entity_kind'],row['entity_id']))
    core=dict(schema=SCHEMA,source_numbering_version=source_numbering_version,target_numbering_version=2,
              source_state=str(state),source_database=database,entries=entries,job_updates=updates,outboxes=reports,artifact_inventory=inventory)
    migration_id='experiment-numbers-'+sha(encoded(core))[:24]
    index=dict(core,migration_id=migration_id,entries_sha256=sha(encoded(entries)),live_state_modified=False,
               raw_scientific_artifacts_modified=False,cross_database_atomic_snapshot=False)
    index_raw=encoded(index);_write(destination/'experiment-projection.json',index_raw)
    _snapshot(original,candidate)
    with closing(sqlite3.connect(candidate)) as db:
        db.execute('BEGIN IMMEDIATE')
        # A temporary namespace also works with uniqueness constraints and never
        # overwrites old04/06 rows. Config/result/source pins are untouched.
        for update in updates:
            changed=db.execute('UPDATE jobs SET project_id=? WHERE id=? AND project_id=?',
                ('migration:'+migration_id+':'+update['canonical_project_id'],update['id'],update['original_project_id'])).rowcount
            if changed!=1:raise MigrationError('Candidate job changed during transactional swap')
        for update in updates:
            db.execute('UPDATE jobs SET project_id=? WHERE id=?',(update['canonical_project_id'],update['id']))
        db.execute('CREATE TABLE '+MIGRATION_TABLE+' (migration_id TEXT PRIMARY KEY,source_numbering_version INTEGER NOT NULL,target_numbering_version INTEGER NOT NULL,source_database_logical_sha256 TEXT NOT NULL,index_sha256 TEXT NOT NULL,terminal_job_updates INTEGER NOT NULL,deferred_active_jobs TEXT NOT NULL)')
        deferred=[row['entity_id'] for row in entries if row.get('deferred_active_pin')]
        db.execute('INSERT INTO '+MIGRATION_TABLE+' VALUES (?,?,?,?,?,?,?)',(migration_id,source_numbering_version,2,database['logical_sha256'],sha(index_raw),len(updates),encoded(deferred).decode()))
        db.commit()
    for report in reports:
        _snapshot(destination/'original'/'outboxes'/(report['identity']+'.sqlite3'),destination/'candidate'/'outboxes'/(report['identity']+'.sqlite3'))
    files=[]
    for path in sorted(destination.rglob('*')):
        if path.is_file():files.append(dict(path=path.relative_to(destination).as_posix(),bytes=path.stat().st_size,sha256=sha(path.read_bytes())))
    _write(destination/'manifest.json',encoded(dict(schema='explorer_experiment_numbering_preparation_v1',migration_id=migration_id,
        files=files,live_apply_implemented=False,rollback='restore original snapshot to a NEW copy; activation is a separate controlled operation')))
    return verify(destination)


def verify(destination):
    destination=Path(destination).resolve(strict=True);manifest=json.loads((destination/'manifest.json').read_bytes())
    if manifest['schema']!='explorer_experiment_numbering_preparation_v1':raise MigrationError('Unknown preparation manifest')
    for file in manifest['files']:
        path=(destination/file['path']).resolve()
        if destination not in path.parents or sha(path.read_bytes())!=file['sha256']:raise MigrationError('Preparation file SHA mismatch: '+file['path'])
    index_raw=(destination/'experiment-projection.json').read_bytes();index=json.loads(index_raw)
    if index['schema']!=SCHEMA or sha(encoded(index['entries']))!=index['entries_sha256']:raise MigrationError('Projection index SHA mismatch')
    for entry in index['entries']:
        core={key:value for key,value in entry.items() if key!='projection_sha256'}
        if sha(encoded(core))!=entry['projection_sha256']:raise MigrationError('Projection entry SHA mismatch')
    with closing(_readonly(destination/'original'/'research.sqlite3')) as original, closing(_readonly(destination/'candidate'/'research.sqlite3')) as candidate:
        before,after=database_state(original),database_state(candidate);marker=_completed_migration(candidate)
        if before!=index['source_database'] or marker['index_sha256']!=sha(index_raw):raise MigrationError('Database/index binding mismatch')
        updates={row['id']:row for row in index['job_updates']}
        previous=_rows(original,'jobs');expected=[]
        for row in previous:
            revised=dict(row)
            if row['id'] in updates:revised['project_id']=updates[row['id']]['canonical_project_id']
            expected.append(revised)
        if sorted(expected,key=encoded)!=_rows(candidate,'jobs'):raise MigrationError('Unexpected candidate job/config/result/pin mutation')
        for table in before['tables']:
            if table!='jobs' and before['tables'][table]!=after['tables'].get(table):raise MigrationError('Immutable table changed: '+table)
        if set(after['tables'])!=set(before['tables']):raise MigrationError('Unexpected candidate schema tables')
        for entry in index['entries']:
            if entry['entity_kind'] in ('event','skill','job'):
                table,identity,field={'event':('events','event_id','body'),'skill':('skills','id','body'),'job':('jobs','id',None)}[entry['entity_kind']]
                row=next((row for row in _rows(original,table) if row[identity]==entry['entity_id']),None)
                if row is None or sha(row[field].encode() if field else encoded(row))!=entry['raw_sha256']:raise MigrationError('Projection original raw SHA mismatch')
    for report in index['outboxes']:
        for subfolder in ('original','candidate'):
            with closing(_readonly(destination/subfolder/'outboxes'/(report['identity']+'.sqlite3'))) as db:
                if database_state(db)!=report['database']:raise MigrationError('Outbox payload/delivery/id changed')
    counts=dict(projected_by_kind={kind:sum(row['entity_kind']==kind for row in index['entries']) for kind in sorted({row['entity_kind'] for row in index['entries']})},
        terminal_job_updates=len(index['job_updates']),deferred_active_jobs=sum(row.get('deferred_active_pin',False) for row in index['entries']))
    return dict(ok=True,schema='explorer_experiment_numbering_verification_v1',migration_id=index['migration_id'],preparation=str(destination),
        source_database_logical_sha256=before['logical_sha256'],candidate_database_logical_sha256=after['logical_sha256'],
        index_sha256=sha(index_raw),counts=counts,immutable_tables_preserved=True,outbox_payloads_preserved=True,
        live_state_modified=False,live_apply_implemented=False)


def check_source(preparation, state=None):
    """Read-only activation precondition; never swaps a live database."""
    receipt=verify(preparation);index=json.loads((Path(preparation)/'experiment-projection.json').read_bytes())
    state=Path(state or index['source_state']).resolve(strict=True)
    with closing(_readonly(state/'research.sqlite3')) as db:actual=database_state(db)
    failures=[]
    if actual['logical_sha256']!=receipt['source_database_logical_sha256']:
        failures.append(dict(code='LIVE_DATABASE_CHANGED_REPREPARE_AFTER_QUIESCENCE',tables=[table for table in actual['tables'] if actual['tables'][table]!=index['source_database']['tables'].get(table)]))
    for report in index['outboxes']:
        with closing(_readonly(report['source_path'])) as db:current=database_state(db)
        if current!=report['database']:failures.append(dict(code='LIVE_OUTBOX_CHANGED_REPREPARE',outbox_identity=report['identity']))
    files={row['path']:row['raw_sha256'] for row in index['entries'] if row['entity_kind']=='artifact_reference'}
    for relative,expected in files.items():
        path=(state/relative).resolve()
        if state not in path.parents or not path.is_file() or sha(path.read_bytes())!=expected:
            failures.append(dict(code='LIVE_SOURCE_ARTIFACT_CHANGED_REINVENTORY',path=relative))
    return dict(ok=not failures,migration_id=index['migration_id'],failures=failures,
                live_source_matches_preparation=not failures,live_apply_implemented=False,live_state_modified=False)


def rollback_copy(preparation,destination):
    receipt=verify(preparation);preparation=Path(preparation).resolve();destination=Path(destination).resolve()
    source_state=Path(json.loads((preparation/'experiment-projection.json').read_bytes())['source_state']).resolve()
    if destination.exists() or destination.is_symlink() or preparation==destination or preparation in destination.parents or source_state==destination or source_state in destination.parents:
        raise MigrationError('Rollback destination must be a NEW external directory')
    destination.mkdir(mode=0o700)
    _snapshot(preparation/'original'/'research.sqlite3',destination/'research.sqlite3')
    outboxes=preparation/'original'/'outboxes'
    if outboxes.exists():
        for path in sorted(outboxes.glob('*.sqlite3')):_snapshot(path,destination/'outboxes'/path.name)
    with closing(_readonly(destination/'research.sqlite3')) as db:
        if database_state(db)['logical_sha256']!=receipt['source_database_logical_sha256']:raise MigrationError('Rollback logical SHA mismatch')
    return dict(ok=True,restored_copy=str(destination),source_database_logical_sha256=receipt['source_database_logical_sha256'],live_state_modified=False)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation',choices=('dry-run','prepare','verify','check-source','rollback-copy'))
    parser.add_argument('--state',type=Path,default=Path.home()/'.local/share/explorer/state')
    parser.add_argument('--destination',type=Path)
    parser.add_argument('--preparation',type=Path)
    parser.add_argument('--source-numbering-version',type=int,choices=(1,2),default=1)
    parser.add_argument('--outbox',type=Path,action='append',default=[])
    parser.add_argument('--lineage-inventory',type=Path)
    args=parser.parse_args()
    try:
        if args.operation=='dry-run':result=dry_run(args.state,source_numbering_version=args.source_numbering_version,outboxes=args.outbox,lineage_inventory=args.lineage_inventory)
        elif args.operation=='prepare':
            if args.destination is None:raise MigrationError('--destination is required')
            result=prepare(args.state,args.destination,source_numbering_version=args.source_numbering_version,outboxes=args.outbox,lineage_inventory=args.lineage_inventory)
        elif args.operation=='verify':result=verify(args.preparation)
        elif args.operation=='check-source':result=check_source(args.preparation,args.state)
        else:result=rollback_copy(args.preparation,args.destination)
        print(json.dumps(result,ensure_ascii=False,indent=2))
    except (MigrationError,OSError,sqlite3.Error,ValueError,KeyError,TypeError) as error:
        print(json.dumps(dict(ok=False,error_type=type(error).__name__,detail=str(error),live_state_modified=False),ensure_ascii=False))
        sys.exit(1)


if __name__=='__main__':
    main()
