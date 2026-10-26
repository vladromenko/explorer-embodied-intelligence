#!/usr/bin/env python3
"""ENGINEERING_GLUE: quiesced Homebrain-only activation of a verified v2 COPY.

Only Explorer control-center/worker units may stop/start. Robot services,
controllers, calibrations, payloads and remote outboxes are never modified.
Rollback copies stay recoverable; new acknowledged events are never discarded
by an automatic database rollback after the new server has started.
"""
import argparse
from contextlib import closing
import fcntl
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'shared'))
sys.path.insert(0,str(ROOT/'scripts'))
import migrate_experiment_numbers as migration

UNITS=('explorer-control-center.service','explorer-worker.service')


def active_jobs(state):
    with closing(migration._readonly(Path(state)/'research.sqlite3')) as db:
        return [dict(row) for row in db.execute("SELECT id,kind,status FROM jobs WHERE status NOT IN ('completed','failed','cancelled','canceled','rejected')")]


def _sync_directory(path):
    descriptor=os.open(path,os.O_RDONLY|os.O_DIRECTORY)
    try:os.fsync(descriptor)
    finally:os.close(descriptor)


def _copy_atomic(source,destination):
    source,destination=Path(source),Path(destination);temporary=destination.with_name(destination.name+'.numbering-v2-next')
    if temporary.exists():raise ValueError('Retained prior activation temporary file requires review')
    with source.open('rb') as reader,temporary.open('xb') as writer:
        shutil.copyfileobj(reader,writer);writer.flush();os.fsync(writer.fileno())
    temporary.chmod(0o600)
    if migration.sha(temporary.read_bytes())!=migration.sha(source.read_bytes()):raise ValueError('Activation copy checksum mismatch')
    os.replace(temporary,destination);_sync_directory(destination.parent)


def install_quiesced_copy(preparation,state,backup_receipt,*,services_stopped):
    """No service actions here; useful for independent filesystem qualification."""
    if services_stopped is not True:raise ValueError('Both own services must be stopped before replacing SQLite')
    state=Path(state).resolve(strict=True);preparation=Path(preparation).resolve(strict=True)
    backup=json.loads(Path(backup_receipt).read_bytes())
    if not all(backup.get(key) is True for key in ('completed','test_restore_passed','all_restored_file_sha256_verified')):raise ValueError('A fully verified payload backup and independent restore are required')
    if active_jobs(state):raise ValueError('Active jobs must finish before canonical database activation')
    verification=migration.verify(preparation)
    if not migration.check_source(preparation,state)['ok']:raise ValueError('Live state changed after preparation; reprepare while quiesced')
    for suffix in ('-wal','-shm'):
        candidate=preparation/'candidate'/('research.sqlite3'+suffix)
        if candidate.exists() and suffix=='-wal' and candidate.stat().st_size:raise ValueError('Prepared candidate has uncheckpointed WAL')
    with closing(sqlite3.connect(state/'research.sqlite3')) as db:
        if db.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchone()[0]!=0:raise ValueError('Existing SQLite writers did not quiesce')
    # Index first is harmless for an old database without the migration marker.
    # Both are complete and fsynced before either own service starts again.
    _copy_atomic(preparation/'candidate'/'experiment-projection.json',state/'experiment-projection.json')
    for suffix in ('-wal','-shm'):
        sidecar=state/('research.sqlite3'+suffix)
        if sidecar.exists():sidecar.unlink()
    _copy_atomic(preparation/'candidate'/'research.sqlite3',state/'research.sqlite3')
    with closing(migration._readonly(state/'research.sqlite3')) as db:
        if db.execute('PRAGMA integrity_check').fetchone()[0]!='ok' or migration.database_state(db)['logical_sha256']!=verification['candidate_database_logical_sha256']:raise ValueError('Activated database differs from the verified candidate')
    return dict(ok=True,migration_id=verification['migration_id'],index_sha256=verification['index_sha256'],
        candidate_database_logical_sha256=verification['candidate_database_logical_sha256'],raw_scientific_payloads_rewritten=False,
        original_snapshot=str(preparation/'original'),independent_verified_backup_receipt=str(Path(backup_receipt)),
        remote_outboxes_modified=False,controller_settings_modified=False,physical_commands=0)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state',type=Path,default=Path.home()/'.local/share/explorer/state')
    parser.add_argument('--destination',type=Path,required=True)
    parser.add_argument('--backup-receipt',type=Path,required=True)
    parser.add_argument('--lineage-inventory',type=Path)
    parser.add_argument('--release',type=Path,required=True)
    args=parser.parse_args();release=args.release.resolve(strict=True)
    if len(release.name)!=40 or (release/'RELEASE_COMMIT').read_text().strip()!=release.name:raise ValueError('A staged commit-addressed source release is required')
    registry=json.loads((release/'_registry/projects.json').read_bytes())
    roles={row['id']:row['slug'] for row in registry['projects']}
    if registry.get('experiment_numbering_version')!=2 or roles.get('04')!='so101_source_learning' or roles.get('06')!='cross_embodiment_transfer':raise ValueError('The staged source does not implement the canonical registry')
    if active_jobs(args.state):raise ValueError('Live jobs are active; no services stopped')
    current=release.parent.parent/'current';previous=current.resolve(strict=True)
    started=time.time();changed=False;stopped=False
    lock_path=args.state/'experiment-numbering-activation.lock'
    with lock_path.open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        try:
            subprocess.run(['systemctl','--user','stop',*UNITS],check=True);stopped=True
            for unit in UNITS:
                if subprocess.run(['systemctl','--user','is-active','--quiet',unit]).returncode==0:raise ValueError('Own service remained active: '+unit)
            preparation=migration.prepare(args.state,args.destination,lineage_inventory=args.lineage_inventory)
            if preparation.get('no_op'):raise ValueError('State already has a numbering migration; do not reactivate')
            # Stage has already passed source checks; switch only these stopped units.
            temporary=current.with_name('current.numbering-v2-next')
            if temporary.is_symlink():temporary.unlink()
            temporary.symlink_to(release,target_is_directory=True);os.replace(temporary,current);_sync_directory(current.parent)
            receipt=install_quiesced_copy(args.destination,args.state,args.backup_receipt,services_stopped=True);changed=True
            receipt.update(execution_commit=release.name,previous_release=str(previous),started_wall_utc=started,
                activation_wall_utc=time.time(),own_units=list(UNITS))
            (args.destination/'activation-receipt.json').write_bytes(migration.encoded(receipt))
        except BaseException:
            if not changed:
                with closing(migration._readonly(args.state/'research.sqlite3')) as db:migrated=migration._completed_migration(db) is not None
                if not migrated:
                    temporary=current.with_name('current.numbering-v2-next')
                    if temporary.is_symlink():temporary.unlink()
                    temporary.symlink_to(previous,target_is_directory=True);os.replace(temporary,current)
            raise
        finally:
            if stopped:subprocess.run(['systemctl','--user','start',*UNITS],check=True)
    print(json.dumps(receipt,indent=2))


if __name__=='__main__':main()
