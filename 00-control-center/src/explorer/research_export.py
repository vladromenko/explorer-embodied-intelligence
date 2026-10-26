"""ENGINEERING_GLUE: read-only selected scientific registry for research export.

Queries only jobs/events/skills through a SQLite read transaction. Authentication,
node inventories, sessions, tickets, outboxes and scheduler controls are not read.
Explicit artifact inventory is required: unknown file dependencies remain PARTIAL.
This adapter never constructs Store, changes SQL, copies directories or fits data.
"""
from __future__ import annotations

import json
from pathlib import Path
import sqlite3

from explorer_shared.research_export import (ExportRejected, REGISTRY_SCHEMA,
    assert_secret_free, inline_node, _id)
from explorer_shared.source_archive import canonical, sha

_PARENT_KEYS = {'source_job_id', 'parent_job_id', 'import_job_id', 'retry_of_job_id',
                'training_job_id', 'teacher_job_id'}
_PARENT_ARRAY_KEYS = {'source_job_ids', 'parent_job_ids', 'import_job_ids'}


def _parent_ids(value):
    result = set()
    if isinstance(value, dict):
        for key, child in value.items():
            if key in _PARENT_KEYS and isinstance(child, str):
                result.add(_id(child))
            elif key in _PARENT_ARRAY_KEYS and isinstance(child, list):
                result.update(_id(item) for item in child)
            elif isinstance(child, (dict, list)):
                result.update(_parent_ids(child))
    elif isinstance(value, list):
        for child in value:
            result.update(_parent_ids(child))
    return result


def _related(value, job_ids):
    if isinstance(value, dict):
        if any(value.get(key) in job_ids for key in ('job_id', 'import_job_id', 'training_job_id') if isinstance(value.get(key), str)):
            return True
        return any(_related(child, job_ids) for child in value.values() if isinstance(child, (dict, list)))
    if isinstance(value, list):
        return any(_related(child, job_ids) for child in value)
    return False


def registry_from_store(store, job_ids, *, artifact_nodes=(), artifact_roots=None,
                        event_ids=(), skill_ids=(), episodes=(), metric_checks=(),
                        dependency_inventory_complete=False, max_jobs=512,
                        max_event_rows=100000, max_body_bytes=16 * 1024**2):
    """Select immutable originals and separate current canonical identity views.

    `artifact_nodes`/`artifact_roots` are SERVER-generated scientific dependencies,
    never browser paths. artifact_roots maps job ID to artifact root IDs. Completion
    is a caller-qualified dependency assertion, not inferred from directory names.
    Existing Store supplies its verified canonical projection, without opening its
    write-capable connect(). No live Store initialization occurs here.
    """
    selected = list(dict.fromkeys(_id(value) for value in job_ids))
    if not selected or len(selected) > max_jobs:
        raise ExportRejected('Bounded nonempty selected job IDs required')
    nodes, missing, jobs, roots = list(artifact_nodes), [], {}, []
    source_commits, run_ids, episode_ids = set(), set(), set()
    requested_events, requested_skills = set(event_ids), set(skill_ids)
    for ident in requested_events | requested_skills:
        _id(ident)
    path = Path(store.path).resolve(strict=True)
    uri = path.as_uri() + '?mode=ro'
    db = sqlite3.connect(uri, uri=True, timeout=5)
    db.row_factory = sqlite3.Row

    def append_raw(ident, role, source_id, raw):
        if len(raw.encode()) > max_body_bytes:
            missing.append(dict(artifact_id=ident, reason='SCIENTIFIC_BODY_BOUND_EXCEEDED'))
        else:
            try:
                assert_secret_free(json.loads(raw))
            except ExportRejected:
                missing.append(dict(artifact_id=ident, reason='SECRET_BEARING_ORIGINAL_WITHHELD',
                                    original_sha256=sha(raw.encode())))
            else:
                nodes.append(inline_node(ident, role, source_id, raw))

    def projection(kind, ident, original_project, raw, dependencies, declared_version=None):
        key = kind + ':' + ident + ':projection'
        if not any(node['id'] == dependencies[0] for node in nodes):
            missing.append(dict(artifact_id=key, reason='CANONICAL_PROJECTION_WITHHELD_WITH_ORIGINAL'))
            return key
        try:
            view = store.experiment_projection.identity(db, kind, ident, original_project,
                raw=raw if kind in ('event', 'skill') else None, declared_version=declared_version)
            if kind == 'skill' and view.get('project_id') is None:
                parent = (json.loads(raw).get('lineage') or {}).get('job_id')
                source = db.execute('SELECT project_id,config FROM jobs WHERE id=?', (parent,)).fetchone() if parent else None
                if source is not None:
                    parent_identity = store.experiment_projection.identity(db, 'job', parent,
                        source['project_id'], declared_version=json.loads(source['config']).get('experiment_numbering_version'))
                    view = dict(parent_identity, numbering_provenance=dict(source='immutable_card_lineage_job_id',
                                job_id=parent, parent_identity=parent_identity))
            assert_secret_free(view)
        except (ValueError, OSError):
            missing.append(dict(artifact_id=key, reason='CANONICAL_PROJECTION_NOT_VERIFIED'))
        else:
            binding = dict(schema='explorer_scientific_identity_projection_v1', entity_kind=kind,
                entity_id=ident, identity=view, raw_sha256=sha(raw),
                original_bytes_rewritten=False, verification_scope='selected_raw_binding_and_captured_Store_identity_not_original_SQLite_backup')
            nodes.append(inline_node(key, 'canonical_projection', ident, canonical(binding),
                dependencies=dependencies, projection_of=dict(id=dependencies[0], sha256=sha(raw))))
        return key

    try:
        db.execute('PRAGMA query_only=ON')
        db.execute('BEGIN')
        pending = list(selected)
        while pending:
            ident = pending.pop(0)
            if ident not in jobs:
                if len(jobs) >= max_jobs:
                    raise ExportRejected('Selected parent job graph exceeds bound')
                row = db.execute('SELECT id,kind,project_id,status,created,updated,config,result,progress,error FROM jobs WHERE id=?', (ident,)).fetchone()
                if row is None:
                    missing.append(dict(artifact_id='job:' + ident, reason='SELECTED_JOB_NOT_FOUND'))
                    jobs[ident] = None
                else:
                    job = dict(row)
                    jobs[ident] = job
                    config = json.loads(job['config'])
                    result = json.loads(job['result']) if job['result'] else {}
                    parents = _parent_ids(config) | _parent_ids(result)
                    pending.extend(sorted(parents - jobs.keys()))
                    config_id = 'job:' + ident + ':config'
                    append_raw(config_id, 'raw_job_config', ident, job['config'])
                    deps = [config_id] + ['job:' + parent for parent in sorted(parents)]
                    if job['result'] is not None:
                        result_id = 'job:' + ident + ':result'
                        append_raw(result_id, 'raw_job_result', ident, job['result'])
                        deps.append(result_id)
                    if job['status'] not in ('completed', 'failed', 'cancelled'):
                        missing.append(dict(artifact_id='job:' + ident, reason='JOB_NOT_TERMINAL_SNAPSHOT_ONLY'))
                    commit = config.get('_execution_commit') or config.get('source_commit')
                    if commit:
                        source_commits.add(commit)
                    for body in (config, result):
                        for key, collection in (('run_id', run_ids), ('episode_id', episode_ids), ('recording_id', episode_ids)):
                            if isinstance(body.get(key), str):
                                collection.add(body[key])
                    meta = {key: job[key] for key in ('id', 'kind', 'project_id', 'status', 'created', 'updated', 'progress')}
                    meta.update(config_original_sha256=sha(job['config'].encode()),
                        result_original_sha256=sha(job['result'].encode()) if job['result'] else None)
                    if job['error']:
                        meta['error_original_sha256'] = sha(job['error'].encode())
                        error_id = 'job:' + ident + ':error'
                        deps.append(error_id)
                        try:
                            assert_secret_free(job['error'])
                        except ExportRejected:
                            missing.append(dict(artifact_id=error_id, reason='SECRET_BEARING_ORIGINAL_WITHHELD',
                                                original_sha256=meta['error_original_sha256']))
                        else:
                            if len(job['error'].encode()) > max_body_bytes:
                                missing.append(dict(artifact_id=error_id, reason='SCIENTIFIC_BODY_BOUND_EXCEEDED'))
                            else:
                                nodes.append(inline_node(error_id, 'raw_job_error', ident, job['error']))
                    view_id = projection('job', ident, job['project_id'], job['config'].encode(), [config_id], config.get('experiment_numbering_version'))
                    deps.append(view_id)
                    deps.extend((artifact_roots or {}).get(ident, []))
                    nodes.append(inline_node('job:' + ident, 'job_metadata', ident, canonical(meta), dependencies=deps))
        present = {ident for ident, row in jobs.items() if row is not None}
        scanned = 0
        found_events, found_skills = set(), set()
        for row in db.execute('SELECT event_id,body FROM events ORDER BY event_id'):
            scanned += 1
            if scanned > max_event_rows:
                raise ExportRejected('Scientific event scan exceeds explicit bound')
            raw = row['body']
            if len(raw.encode()) > max_body_bytes:
                if row['event_id'] in requested_events:
                    missing.append(dict(artifact_id='event:' + row['event_id'], reason='SCIENTIFIC_BODY_BOUND_EXCEEDED'))
            else:
                body = json.loads(raw)
                if row['event_id'] in requested_events or _related(body.get('payload') or {}, present):
                    ident = row['event_id']
                    _id(ident)
                    found_events.add(ident)
                    key = 'event:' + ident
                    append_raw(key, 'raw_event', ident, raw)
                    view = projection('event', ident, body.get('project_id'), raw.encode(), [key], body.get('experiment_numbering_version'))
                    roots.extend((key, view))
        for row in db.execute('SELECT id,body FROM skills ORDER BY id'):
            raw = row['body']
            if len(raw.encode()) <= max_body_bytes:
                body = json.loads(raw)
                if row['id'] in requested_skills or _related(body.get('lineage') or {}, present):
                    ident = _id(row['id'])
                    found_skills.add(ident)
                    key = 'skill:' + ident
                    append_raw(key, 'raw_skill_card', ident, raw)
                    view = projection('skill', ident, body.get('project_id') or body.get('owner_experiment'), raw.encode(), [key], body.get('experiment_numbering_version'))
                    roots.extend((key, view))
            elif row['id'] in requested_skills:
                missing.append(dict(artifact_id='skill:' + row['id'], reason='SCIENTIFIC_BODY_BOUND_EXCEEDED'))
        for ident in requested_events - found_events:
            missing.append(dict(artifact_id='event:' + ident, reason='SELECTED_EVENT_NOT_FOUND'))
        for ident in requested_skills - found_skills:
            missing.append(dict(artifact_id='skill:' + ident, reason='SELECTED_SKILL_NOT_FOUND'))
    finally:
        db.close()
    roots = ['job:' + ident for ident in selected] + roots
    for episode in episodes:
        if isinstance(episode.get('episode_id'), str):
            episode_ids.add(episode['episode_id'])
        if isinstance(episode.get('run_id'), str):
            run_ids.add(episode['run_id'])
    return dict(schema=REGISTRY_SCHEMA, root_ids=roots, nodes=nodes,
        job_ids=sorted(jobs), run_ids=sorted(run_ids), episode_ids=sorted(episode_ids),
        source_commits=sorted(source_commits), episodes=list(episodes), metric_checks=list(metric_checks),
        missing_dependencies=missing, dependency_inventory_complete=dependency_inventory_complete,
        snapshot_basis='read_only_SQLite_transaction_selected_scientific_rows_not_atomic_with_artifact_files')
