"""ENGINEERING_GLUE: queued selected-evidence export, no devices or training.

Browser input is terminal job IDs only. Preparation pins server-only scientific
inventory and an estimate before enqueue. Worker publishes a fixed private TAR
only after exact-copy independent restore. This is not a whole-state backup.
"""
from __future__ import annotations

from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import tarfile
import uuid

from explorer_shared.research_export import (ExportRejected, LIMITS, assert_secret_free,
    export_evidence, restore_export, verify_export, _relative, _check_text)
from explorer_shared.source_archive import canonical, sha, _write
from .research_export import registry_from_store

SCHEMA = 'explorer_research_export_request_v1'
MAX_SELECTED_JOBS = 32
ID = re.compile(r'[0-9a-f]{32}')
TERMINAL = {'completed', 'failed', 'cancelled'}
EXTENSIONS = {'.json', '.jsonl', '.ndjson', '.npz', '.npy', '.png', '.jpg', '.jpeg',
    '.webp', '.mp4', '.safetensors', '.pt', '.stl', '.obj', '.urdf', '.xml', '.py',
    '.onnx', '.hef', '.tar'}
PAYLOAD_TREES = {'artifacts', 'datasets', 'episodes', 'assets', 'qualification'}
MANIFEST_POINTERS = {'dataset_manifest', 'source_manifest_path', 'source_manifest',
    'embodied_manifest', 'model_manifest', 'manifest_path', 'source_archive_path',
    'artifact', 'archive_path'}
REQUEST_DIRECTORY = 'artifacts/research-export-requests'
DISK_OVERHEAD_BYTES = 256 * 1024**2
MAX_TAR_BYTES = LIMITS['max_total_bytes'] + 4 * LIMITS['max_manifest_bytes'] + DISK_OVERHEAD_BYTES


class ResearchExportBlocked(ValueError):
    def __init__(self, code, detail=''):
        self.code, self.detail = code, detail
        super().__init__(code + (': ' + detail if detail else ''))


def _job_ids(values):
    if (not isinstance(values, list) or not 1 <= len(values) <= MAX_SELECTED_JOBS or
            any(not isinstance(item, str) or not ID.fullmatch(item) for item in values)):
        raise ResearchExportBlocked('TERMINAL_JOB_IDS_REQUIRED', 'One to32 lowercase32hex IDs')
    return sorted(set(values))


def _json(path, limit=LIMITS['max_manifest_bytes']):
    path = Path(path)
    if path.is_symlink() or not path.is_file() or path.stat().st_size > limit:
        raise ResearchExportBlocked('EXPORT_METADATA_PATH_OR_BOUND_INVALID')
    return json.loads(path.read_bytes())


def _hash(path, *, max_bytes=LIMITS['max_file_bytes']):
    path = Path(path)
    before = path.stat()
    if not stat.S_ISREG(before.st_mode) or before.st_size > max_bytes:
        raise ResearchExportBlocked('SCIENTIFIC_FILE_BOUND_EXCEEDED')
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, 'rb') as stream:
        opened = os.fstat(stream.fileno())
        if (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns, opened.st_ctime_ns) != (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns):
            raise ResearchExportBlocked('SOURCE_FILE_CHANGED_DURING_SELECTION')
        checksum = hashlib.file_digest(stream, 'sha256').hexdigest()
        after = os.fstat(stream.fileno())
    current = path.stat()
    if any((value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns) !=
           (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns) for value in (after, current)):
        raise ResearchExportBlocked('SOURCE_FILE_CHANGED_DURING_SELECTION')
    return checksum, before.st_size


def _source_jobs(store, ids, *, allow_missing=False, max_inline_bytes=16 * 1024**2):
    if type(max_inline_bytes) is not int or not 1<=max_inline_bytes<=32*1024**2:
        raise ResearchExportBlocked('SOURCE_JOB_METADATA_BUDGET_INVALID')
    rows = {}
    with closing(sqlite3.connect(Path(store.path).resolve().as_uri() + '?mode=ro', uri=True)) as db:
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA query_only=ON');db.execute('BEGIN')
        metadata={};total=0
        for ident in ids:
            row=db.execute('''SELECT id,kind,status,
                COALESCE(LENGTH(CAST(config AS BLOB)),0)+COALESCE(LENGTH(CAST(result AS BLOB)),0)+COALESCE(LENGTH(CAST(error AS BLOB)),0) AS body_bytes
                FROM jobs WHERE id=?''',(ident,)).fetchone()
            if row is None:
                if not allow_missing:
                    raise ResearchExportBlocked('SOURCE_JOB_NOT_FOUND', ident)
            else:
                total+=row['body_bytes']
                if total>max_inline_bytes:
                    raise ResearchExportBlocked('SOURCE_JOB_METADATA_TOTAL_BOUND_EXCEEDED')
                if row['status'] not in TERMINAL:
                    raise ResearchExportBlocked('SOURCE_JOB_NOT_TERMINAL', ident)
                if row['kind'] == 'research_export':
                    raise ResearchExportBlocked('RECURSIVE_EXPORT_NOT_ALLOWED', ident)
                metadata[ident]=row
        # No Python config/result/error value is materialized before the whole
        # requested cohort has passed the bounded SQL byte-size preflight.
        for ident in metadata:
            row=db.execute('SELECT id,kind,project_id,status,config,result,error,created,updated,progress FROM jobs WHERE id=?',(ident,)).fetchone()
            rows[ident]=dict(row)
    return rows


def _fingerprint(rows):
    return {ident: sha(canonical(body)) for ident, body in rows.items()}


def _inspect_tar(path):
    """Read-only streaming member policy; never extract supplied archives."""
    count, expanded = 0, 0
    with tarfile.open(path, mode='r:*') as archive:
        for member in archive:
            count += 1
            if count > LIMITS['max_files']:
                raise ExportRejected('Raw episode TAR member count exceeds bound')
            _relative(member.name.rstrip('/'))
            if member.isdir():
                pass
            elif not member.isfile() or member.size > LIMITS['max_file_bytes']:
                raise ExportRejected('Raw episode TAR links/devices/bounds are forbidden')
            else:
                expanded += member.size
                if expanded > LIMITS['max_total_bytes'] or Path(member.name).suffix.lower() not in EXTENSIONS - {'.tar'}:
                    raise ExportRejected('Raw episode TAR unqualified content or expansion bound')
                with archive.extractfile(member) as stream:
                    first = stream.read(16)
                    if first.startswith(b'SQLite format 3\x00'):
                        raise ExportRejected('Raw SQLite member is forbidden')
                    if Path(member.name).suffix.lower() in {'.json', '.jsonl', '.ndjson', '.py', '.xml', '.urdf'}:
                        if member.size > LIMITS['max_json_bytes']:
                            raise ExportRejected('Raw episode TAR inspected text bound')
                        _check_text(first + stream.read(), 'source_archive_member', member.name, LIMITS)


def _role(path):
    suffix = path.suffix.lower()
    if suffix in ('.json', '.jsonl', '.ndjson'):
        return 'dataset_manifest' if 'manifest' in path.name.lower() else 'review'
    if suffix in ('.safetensors', '.onnx', '.hef'):
        return 'model_weights'
    if suffix == '.pt':
        return 'checkpoint'
    if suffix in ('.stl', '.obj', '.urdf', '.xml', '.py'):
        return 'source_archive_member'
    return 'raw_episode' if suffix == '.tar' else 'dataset_payload'


def _references(value, base):
    """Explicit scientific formats, never arbitrary strings/paths in metadata."""
    result = []
    if isinstance(value, dict):
        for key, child in value.items():
            if key == 'source_files_sha256' and isinstance(child, dict):
                for name, expected in child.items():
                    if isinstance(name, str) and isinstance(expected, str):
                        result.append((Path(name) if Path(name).is_absolute() else base / name, expected))
            elif key in MANIFEST_POINTERS and isinstance(child, str):
                candidate = Path(child)
                if candidate.suffix.lower() in EXTENSIONS:
                    expected = value.get(key + '_sha256')
                    if key == 'artifact':
                        expected = expected or value.get('artifact_sha256') or value.get('sha256')
                    if key == 'dataset_manifest':
                        expected = expected or (value.get('skill_workflow') or {}).get('aggregate_sha256') or value.get('source_manifest_sha256')
                    result.append((candidate if candidate.is_absolute() else base / candidate, expected))
            if key in ('episodes', 'source_artifacts', 'embodied_source_artifacts', 'files') and isinstance(child, list):
                for descriptor in child:
                    if isinstance(descriptor, dict):
                        name = descriptor.get('file') or descriptor.get('path') or descriptor.get('relative_path')
                        expected = descriptor.get('sha256')
                        if isinstance(name, str) and isinstance(expected, str):
                            candidate = Path(name)
                            result.append((candidate if candidate.is_absolute() else base / candidate, expected))
            if isinstance(child, (dict, list)):
                result.extend(_references(child, base))
    elif isinstance(value, list):
        for child in value:
            result.extend(_references(child, base))
    return result


def _inventory(store, registry, source_rows):
    state = Path(store.root).resolve()
    nodes, roots, missing, pending, seen, accepted, attempted, folders = [], {}, [], [], set(), set(), set(), {}
    selected = set(source_rows)
    total = 0
    def issue(path, reason):
        try:locator = str(Path(path).absolute().relative_to(state))
        except ValueError:locator = 'OUTSIDE_MANAGED_ROOT_SHA_' + sha(str(path).encode())
        missing.append(dict(reason=reason, source_locator=locator))
    def managed(path):
        locator = Path(path).absolute()
        resolved = locator.resolve(strict=True)
        logical_state = Path(store.root).absolute()
        if not resolved.is_relative_to(state) or not (locator.is_relative_to(state) or locator.is_relative_to(logical_state)):
            raise ExportRejected('Outside managed roots')
        relative = _relative(locator.relative_to(state if locator.is_relative_to(state) else logical_state).as_posix())
        actual = _relative(resolved.relative_to(state).as_posix())
        if actual.parts[0] == 'jobs':
            if len(actual.parts) < 3 or actual.parts[1] not in selected:
                raise ExportRejected('Undeclared parent job directory')
            key = 'job_' + actual.parts[1]
            root = state / 'jobs' / actual.parts[1]
        elif actual.parts[0] in PAYLOAD_TREES:
            key = 'payload_' + actual.parts[0]
            root = state / actual.parts[0]
        else:
            raise ExportRejected('Non-scientific managed tree')
        if relative.parts[0] != actual.parts[0]:
            raise ExportRejected('Scientific alias changes managed tree')
        roots[key] = str(root)
        return locator, resolved, key, resolved.relative_to(root).as_posix()
    for ident, row in source_rows.items():
        directory = state / 'jobs' / ident
        paths = []
        if directory.is_dir() and not directory.is_symlink():
            def visit(current):
                for child in sorted(current.iterdir()):
                    if child.is_symlink() and child.resolve().is_dir():
                        paths.append(child.relative_to(directory).as_posix())
                        issue(child, 'DIRECTORY_SYMLINK_NOT_TRAVERSED')
                    elif child.is_dir():
                        visit(child)
                    else:
                        paths.append(child.relative_to(directory).as_posix())
                        pending.append((child, None, ident))
                    if len(paths) > LIMITS['max_files']:
                        raise ResearchExportBlocked('SOURCE_INVENTORY_FILE_BOUND_EXCEEDED')
            visit(directory)
        elif directory.exists() or directory.is_symlink():
            issue(directory, 'SELECTED_JOB_DIRECTORY_NOT_REGULAR_PRIVATE_TREE')
        else:
            issue(directory, 'SELECTED_JOB_DIRECTORY_ABSENT')
        folders[ident] = paths
        for field in ('config', 'result'):
            if row.get(field):
                pending.extend((path, expected, ident) for path, expected in _references(json.loads(row[field]), directory))
    while pending:
        path, expected, owner = pending.pop(0)
        try:
            locator, resolved, key, relative = managed(path)
            if expected is not None and (not isinstance(expected, str) or not re.fullmatch('[0-9a-f]{64}', expected)):
                raise ExportRejected('Scientific source SHA declaration is malformed')
            attempted_key = (str(resolved), expected)
            if attempted_key not in attempted:
                attempted.add(attempted_key)
                seen.add(str(resolved))
                if len(seen) > LIMITS['max_files'] or resolved.suffix.lower() not in EXTENSIONS:
                    raise ExportRejected('Unknown scientific format or file count bound')
                checksum, size = _hash(resolved)
                if expected is not None and expected != checksum:
                    raise ResearchExportBlocked('SOURCE_EXPECTED_SHA_MISMATCH', str(path))
                if key.startswith('payload_') and expected is None and str(resolved) not in accepted:
                    raise ExportRejected('External scientific payload has no explicit source SHA')
                if size > LIMITS['max_json_bytes'] and resolved.suffix.lower() in ('.json', '.jsonl', '.ndjson'):
                    raise ExportRejected('Scientific JSON inspection bound exceeded')
                if resolved.suffix.lower() == '.tar':
                    _inspect_tar(resolved)
                elif size <= LIMITS['max_json_bytes']:
                    _check_text(resolved.read_bytes(), _role(resolved), resolved.name, LIMITS)
                if str(resolved) not in accepted:
                    accepted.add(str(resolved))
                    total += size
                    if total > LIMITS['max_total_bytes']:
                        raise ResearchExportBlocked('SOURCE_INVENTORY_TOTAL_BOUND_EXCEEDED')
                    node_id = 'file:' + sha((key + '/' + relative).encode())
                    nodes.append(dict(id=node_id, role=_role(resolved), source_id=owner, root=key,
                        relative_path=relative, sha256=checksum, size_bytes=size, dependencies=[]))
                    if resolved.suffix.lower() == '.json':
                        document = json.loads(resolved.read_bytes())
                        pending.extend((child, digest, owner) for child, digest in _references(document, resolved.parent))
        except ResearchExportBlocked:
            raise
        except (OSError, ValueError, tarfile.TarError) as error:
            issue(path, 'DEPENDENCY_WITHHELD_' + type(error).__name__.upper())
    # Preserve all selected files as explicit roots; no directory is handed to TAR.
    registry['nodes'].extend(nodes)
    registry['root_ids'].extend(node['id'] for node in nodes)
    registry['missing_dependencies'].extend(missing)
    registry['missing_dependencies'].append(dict(reason='ENVIRONMENT_AND_EXTERNAL_DEPENDENCY_INVENTORY_NOT_EXHAUSTIVELY_QUALIFIED'))
    return registry, roots, total, folders


def request_settings(store, job_ids):
    """Server-only preparation before create_job; caller supplies IDs, never paths."""
    selected = _job_ids(job_ids)
    initial = _source_jobs(store, selected)
    registry = registry_from_store(store, selected)
    commits = registry['source_commits']
    registry['source_commits'] = [value for value in commits if isinstance(value, str) and re.fullmatch('[0-9a-f]{40}', value)]
    if len(registry['source_commits']) != len(commits):
        registry['missing_dependencies'].append(dict(reason='SOURCE_COMMIT_DECLARATION_NOT_VERIFIABLE'))
    included = registry['job_ids']
    if len(included) > 512 or any(not ID.fullmatch(ident) for ident in included):
        raise ResearchExportBlocked('SOURCE_PARENT_GRAPH_INVALID')
    rows = _source_jobs(store, included, allow_missing=True)
    if any(sha(canonical(rows[ident])) != sha(canonical(body)) for ident, body in initial.items()):
        raise ResearchExportBlocked('SOURCE_JOBS_CHANGED_DURING_SELECTION')
    registry, roots, payload_bytes, folders = _inventory(store, registry, rows)
    protocol = Path(__file__).resolve().parents[3] / 'research/protocols/collection-export-v1.json'
    # Installed editable layouts may need the repository four parents above src.
    if not protocol.is_file():
        protocol = Path(__file__).resolve().parents[4] / 'research/protocols/collection-export-v1.json'
    raw_protocol = protocol.read_bytes()
    assert_secret_free(json.loads(raw_protocol))
    inline_bytes = sum(len(node.get('inline_utf8', '').encode()) for node in registry['nodes'])
    estimate = payload_bytes + inline_bytes + len(raw_protocol) + len(registry['nodes']) * 4096 + 1024 * 1024
    state = Path(store.root).resolve()
    request_root = state / REQUEST_DIRECTORY
    if request_root.resolve() != request_root:
        raise ResearchExportBlocked('REQUEST_ARTIFACT_DIRECTORY_ALIAS_FORBIDDEN')
    request_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    if not roots:
        roots['research_requests'] = str(request_root)
    included = sorted(rows)
    body = dict(schema=SCHEMA, selected_job_ids=selected, included_job_ids=included,
        source_job_fingerprints=_fingerprint(rows), registry=registry, managed_roots=roots,
        source_folder_entries=folders, protocol_utf8=raw_protocol.decode(), protocol_sha256=sha(raw_protocol),
        payload_bytes=payload_bytes, estimated_payload_bytes=estimate,
        disk_budget_bytes=3 * estimate + DISK_OVERHEAD_BYTES,
        scope='selected_terminal_scientific_sources_no_whole_state_or_auth',
        physical_io=False, source_deletion_allowed=False)
    raw = canonical(body)
    assert_secret_free(body)
    if len(raw) > LIMITS['max_manifest_bytes']:
        raise ResearchExportBlocked('REQUEST_MANIFEST_BOUND_EXCEEDED')
    checksum = sha(raw)
    destination = request_root / checksum / 'request.json'
    if destination.resolve() != destination:
        raise ResearchExportBlocked('REQUEST_ARTIFACT_DIRECTORY_ALIAS_FORBIDDEN')
    _write(destination, raw)
    return dict(schema=SCHEMA, job_ids=selected, source_job_ids=included,
        request_manifest=REQUEST_DIRECTORY + '/' + checksum + '/request.json',
        request_sha256=checksum, estimated_payload_bytes=estimate)


def _load_request(store, config):
    checksum = config.get('request_sha256')
    if not isinstance(checksum, str) or not re.fullmatch('[0-9a-f]{64}', checksum):
        raise ResearchExportBlocked('SERVER_PREPARED_REQUEST_REQUIRED')
    expected = REQUEST_DIRECTORY + '/' + checksum + '/request.json'
    if config.get('schema') != SCHEMA or config.get('request_manifest') != expected:
        raise ResearchExportBlocked('SERVER_PREPARED_REQUEST_REQUIRED')
    path = Path(store.root).resolve() / expected
    if path.resolve() != path or path.is_symlink():
        raise ResearchExportBlocked('REQUEST_PATH_ALIAS_FORBIDDEN')
    body = _json(path)
    if sha(path.read_bytes()) != checksum or body.get('schema') != SCHEMA:
        raise ResearchExportBlocked('REQUEST_SHA_MISMATCH')
    if (config.get('job_ids') != body['selected_job_ids'] or config.get('source_job_ids') != body['included_job_ids'] or
            type(config.get('estimated_payload_bytes')) is not int or config['estimated_payload_bytes'] != body['estimated_payload_bytes']):
        raise ResearchExportBlocked('QUEUED_SOURCE_SELECTION_CHANGED')
    _job_ids(config['job_ids'])
    return body


def _job_folder(store, ident):
    if not isinstance(ident, str) or not ID.fullmatch(ident):
        raise ResearchExportBlocked('EXPORT_JOB_ID_INVALID')
    folder = Path(store.root).resolve() / 'jobs' / ident
    if folder.resolve() != folder:
        raise ResearchExportBlocked('EXPORT_JOB_FOLDER_ALIAS_FORBIDDEN')
    return folder


def _tar(bundle, restore, path, manifest_sha):
    verify_export(bundle / 'manifest.json', manifest_sha)
    manifest = _json(bundle / 'manifest.json')
    selected = [('bundle/manifest.json', bundle / 'manifest.json'),
        ('bundle/' + manifest['protocol_file'], bundle / manifest['protocol_file']),
        ('restore-verification.json', restore / 'restore-verification.json')]
    selected.extend(('bundle/' + row['relative_path'], bundle / row['relative_path']) for row in manifest['file_manifest'])
    temporary = path.with_name('.evidence-' + uuid.uuid4().hex + '.tar')
    try:
        with temporary.open('xb') as output:
            with tarfile.open(fileobj=output, mode='w', format=tarfile.PAX_FORMAT) as archive:
                for name, source in selected:
                    _relative(name)
                    before, size = _hash(source)
                    descriptor = os.open(source, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
                    with os.fdopen(descriptor, 'rb') as stream:
                        info = tarfile.TarInfo(name)
                        info.size=size;info.mode=0o600;info.mtime=0
                        archive.addfile(info, stream)
                    if _hash(source) != (before, size):
                        raise ResearchExportBlocked('BUNDLE_CHANGED_WHILE_PACKING')
            output.flush();os.fsync(output.fileno())
        verify_export(bundle / 'manifest.json', manifest_sha)
        if path.exists():
            raise ResearchExportBlocked('IMMUTABLE_EXPORT_TAR_ALREADY_EXISTS')
        os.rename(temporary, path);path.chmod(0o600)
        descriptor=os.open(path.parent,os.O_RDONLY | os.O_DIRECTORY)
        try:os.fsync(descriptor)
        finally:os.close(descriptor)
    finally:
        temporary.unlink(missing_ok=True)


def run_research_export(store, job, config):
    if job.get('kind') != 'research_export':
        raise ResearchExportBlocked('RESEARCH_EXPORT_JOB_REQUIRED')
    if (job.get('config') or {}).get('request_sha256') != config.get('request_sha256'):
        raise ResearchExportBlocked('JOB_SOURCE_REQUEST_MISMATCH')
    request = _load_request(store, config)
    folder = _job_folder(store, job.get('id'))
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    saved = folder / 'export-result.json'
    if saved.is_file():
        result = _json(saved)
        if result.get('source_request_sha256') != config['request_sha256']:
            raise ResearchExportBlocked('CACHED_EXPORT_SOURCE_CHANGED')
        if _hash(folder / 'evidence.tar', max_bytes=MAX_TAR_BYTES) != (result['evidence_tar_sha256'], result['evidence_tar_bytes']):
            raise ResearchExportBlocked('CACHED_EXPORT_TAR_CHANGED')
        return result
    current = _source_jobs(store, request['included_job_ids'])
    if _fingerprint(current) != request['source_job_fingerprints']:
        raise ResearchExportBlocked('SOURCE_JOBS_CHANGED_AFTER_ENQUEUE')
    for ident, expected in request['source_folder_entries'].items():
        source = Path(store.root).resolve() / 'jobs' / ident
        actual = sorted(path.relative_to(source).as_posix() for path in source.rglob('*') if path.is_file() or path.is_symlink()) if source.is_dir() and not source.is_symlink() else []
        if sorted(expected) != actual:
            raise ResearchExportBlocked('SOURCE_FOLDER_CHANGED_AFTER_ENQUEUE', ident)
    for key, value in request['managed_roots'].items():
        path = Path(value)
        if not path.resolve().is_relative_to(Path(store.root).resolve()):
            raise ResearchExportBlocked('QUEUED_MANAGED_ROOT_ESCAPES_STATE')
    _write(folder / 'request-context.json', canonical(dict(request_sha256=config['request_sha256'], physical_io=False)))
    _write(folder / 'collection-protocol-original.json', request['protocol_utf8'].encode())
    if (folder/'bundle').exists() or (folder/'restore').exists():
        raise ResearchExportBlocked('INTERRUPTED_EXPORT_RETAINED_USE_NEW_JOB')
    export = export_evidence(folder / 'bundle', request['registry'], request['managed_roots'],
        protocol_path=folder/'collection-protocol-original.json', protocol_sha256=request['protocol_sha256'], export_id=job['id'])
    restored = restore_export(export['manifest_path'], export['manifest_sha256'], folder/'restore')
    _tar(folder/'bundle', folder/'restore', folder/'evidence.tar', export['manifest_sha256'])
    checksum, size = _hash(folder/'evidence.tar', max_bytes=MAX_TAR_BYTES)
    manifest = _json(export['manifest_path'])
    result = dict(schema='explorer_research_export_job_result_v1', source_request_sha256=config['request_sha256'],
        selected_job_ids=request['selected_job_ids'], included_job_ids=request['included_job_ids'],
        export_manifest_path=export['manifest_path'], export_manifest_sha256=export['manifest_sha256'],
        restore_verification_path=str(folder/'restore/restore-verification.json'),
        restore_verification_sha256=sha((folder/'restore/restore-verification.json').read_bytes()),
        verification=restored['verification'], missing_dependencies=manifest['missing_dependencies'],
        missing_dependency_count=len(manifest['missing_dependencies']), files=export['files'],
        evidence_tar_path=str(folder/'evidence.tar'), evidence_tar_sha256=checksum, evidence_tar_bytes=size,
        archive_sha256=checksum, archive_bytes=size, total_payload_bytes=export['total_payload_bytes'],
        independent_restore_verified=True, manifest_sha256=export['manifest_sha256'],
        missing_dependencies_count=len(manifest['missing_dependencies']),
        estimated_payload_bytes=request['estimated_payload_bytes'], actual_payload_bytes=export['total_payload_bytes'],
        disk_budget_bytes=request['disk_budget_bytes'], originals_deleted=False, physical_io=False,
        scientific_success_claimed=False, anonymization_performed=False)
    _write(saved, canonical(result))
    return result


def download_validation(store, job_id):
    """Fixed completed export only; no caller-provided filesystem path is accepted."""
    if not isinstance(job_id, str) or not ID.fullmatch(job_id):
        raise ResearchExportBlocked('EXPORT_JOB_ID_INVALID')
    job = store.job(job_id)
    if not job or job.get('kind') != 'research_export' or job.get('status') != 'completed':
        raise ResearchExportBlocked('COMPLETED_RESEARCH_EXPORT_REQUIRED')
    result = job.get('result') or {}
    path = _job_folder(store, job_id) / 'evidence.tar'
    if result.get('evidence_tar_path') != str(path) or path.is_symlink():
        raise ResearchExportBlocked('EXPORT_DOWNLOAD_PATH_INVALID')
    checksum, size = _hash(path, max_bytes=MAX_TAR_BYTES)
    if checksum != result.get('evidence_tar_sha256') or size != result.get('evidence_tar_bytes'):
        raise ResearchExportBlocked('EXPORT_DOWNLOAD_SHA_OR_SIZE_MISMATCH')
    return dict(path=path, size_bytes=size, sha256=checksum, media_type='application/x-tar',
        filename='explorer-evidence-' + job_id + '.tar', verification=result.get('verification'))
