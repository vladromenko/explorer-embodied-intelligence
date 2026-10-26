"""ENGINEERING_GLUE: selected scientific evidence graph, immutable copy and restore.

No database/device/model imports or archive extraction. This is a scoped export,
not a state backup, anonymizer, admission validator or scientific success claim.
Secret-bearing JSON is rejected intact; originals are never sanitized in place.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import uuid

from .source_archive import canonical, sha, _write

SCHEMA = 'explorer_research_export_manifest_v1'
REGISTRY_SCHEMA = 'explorer_research_export_registry_v1'
ROLES = frozenset(('raw_episode', 'raw_event', 'raw_job_config', 'raw_job_result',
    'raw_job_error', 'raw_skill_card', 'profile', 'spec', 'dataset_manifest', 'dataset_payload',
    'model_weights', 'processor', 'checkpoint', 'protocol', 'trial_records',
    'derived_metrics', 'implementation', 'environment_receipt',
    'canonical_projection', 'projection_index', 'review', 'source_archive_manifest',
    'source_archive_member', 'job_metadata'))
JSON_ROLES = ROLES - {'raw_episode', 'dataset_payload', 'model_weights', 'checkpoint',
    'raw_job_error', 'implementation', 'trial_records', 'source_archive_member'}
LIMITS = dict(max_files=20000, max_file_bytes=8 * 1024**3,
              max_total_bytes=64 * 1024**3, max_manifest_bytes=32 * 1024**2,
              max_json_bytes=16 * 1024**2)
_ID = re.compile(r'[A-Za-z0-9_.:-]{1,180}')
_SHA = re.compile(r'[0-9a-f]{64}')
_SECRET_KEYS = frozenset(('password', 'passwd', 'secret', 'credentials', 'credential',
    'authorization', 'auth', 'bearer', 'api_key', 'api_token', 'token', 'access_token', 'refresh_token', 'node_token',
    'node_tokens', 'token_hash', 'csrf', 'csrf_token', 'login_session', 'login_sessions',
    'login_ticket', 'login_tickets', 'ssh_key', 'ssh_keys', 'ssh_private_key',
    'private_key', 'wifi_settings', 'wifi_password', 'session_token', 'auth_token'))
_FORBIDDEN_PARTS = frozenset(('sessions', 'tickets', 'credentials', '.ssh', '.env',
    'wifi', 'wifi_settings', 'node-tokens', 'auth'))


class ExportRejected(ValueError):
    pass


def _id(value):
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise ExportRejected('Bounded explicit evidence identity required')
    return value


def _digest(value):
    if not isinstance(value, str) or not _SHA.fullmatch(value):
        raise ExportRejected('Explicit SHA256 required')
    return value


def _relative(value):
    if not isinstance(value, str) or not value or '\\' in value:
        raise ExportRejected('Safe relative evidence path required')
    path = Path(value)
    if path.is_absolute() or path.as_posix() != value or any(p in ('..', '.', '') for p in path.parts):
        raise ExportRejected('Safe relative evidence path required')
    if any(p.lower() in _FORBIDDEN_PARTS for p in path.parts) or path.suffix.lower() in ('.pem', '.key', '.sqlite3', '.db'):
        raise ExportRejected('Credential/database path is forbidden in scientific export')
    return path


def assert_secret_free(value):
    """Reject, rather than rewrite, forbidden structured keys at every depth."""
    if isinstance(value, dict):
        for key, child in value.items():
            normalized = re.sub(r'(?<!^)(?=[A-Z])', '_', str(key)).lower().replace('-', '_')
            if normalized in _SECRET_KEYS or normalized.endswith(('_password', '_secret', '_access_token', '_node_token')):
                raise ExportRejected('Secret/authentication field is forbidden: ' + normalized)
            assert_secret_free(child)
    elif isinstance(value, list):
        for child in value:
            assert_secret_free(child)
    elif isinstance(value, str) and (re.search(r'-----BEGIN (?:[A-Z ]+ )?PRIVATE KEY-----', value) or
            re.search(r'\bBearer\s+[A-Za-z0-9._~+/-]{8,}', value, re.I) or
            re.search(r'[?&](?:token|password|api_key|secret)=', value, re.I) or
            re.search(r'\b\w+://[^/\s:]+:[^/\s@]+@', value) or
            re.search(r'^\s*(?:export\s+)?(?:[A-Z0-9_]*(?:PASSWORD|PASSWD|SECRET|ACCESS_TOKEN|NODE_TOKEN|API_KEY))\s*=\s*\S+', value, re.I | re.M)):
        raise ExportRejected('Embedded credential value is forbidden')


def inline_node(identity, role, source_id, raw, *, dependencies=(), **metadata):
    if isinstance(raw, bytes):
        raw = raw.decode('utf-8')
    return dict(id=identity, role=role, source_id=source_id, inline_utf8=raw,
                sha256=sha(raw.encode()), dependencies=list(dependencies), **metadata)


def _check_text(raw, role, name, limits):
    is_json = role in JSON_ROLES or role == 'trial_records' or str(name).lower().endswith(('.json', '.jsonl', '.ndjson'))
    if is_json:
        if len(raw) > limits['max_json_bytes']:
            raise ExportRejected('Scientific JSON exceeds inspection bound')
        try:
            text = raw.decode('utf-8')
            values = [json.loads(line) for line in text.splitlines() if line.strip()] if str(name).endswith(('.jsonl', '.ndjson')) or role == 'trial_records' else [json.loads(text)]
        except (ValueError, UnicodeError) as error:
            raise ExportRejected('Scientific JSON cannot be inspected') from error
        for value in values:
            assert_secret_free(value)
    elif len(raw) <= limits['max_json_bytes']:
        try:
            text = raw.decode('utf-8')
        except UnicodeError:
            text = ''
        assert_secret_free(text)
        if text.lstrip().startswith(('{', '[')):
            try:
                structured = json.loads(text)
            except ValueError:
                structured = None
            assert_secret_free(structured)


def _bound_limits(overrides):
    limits = dict(LIMITS)
    for key, value in (overrides or {}).items():
        if key not in limits or type(value) is not int or value <= 0 or value > LIMITS[key]:
            raise ExportRejected('Only tighter positive export bounds are allowed')
        limits[key] = value
    return limits


def _source(node, roots):
    key = node.get('root')
    if key not in roots:
        raise ExportRejected('Artifact must belong to an explicit managed root')
    relative = _relative(node.get('relative_path'))
    locator = roots[key] / relative
    resolved = locator.resolve(strict=True)
    matches = [(name, root) for name, root in roots.items() if resolved.is_relative_to(root)]
    if not matches or not stat.S_ISREG(resolved.stat().st_mode):
        raise ExportRejected('Artifact symlink escapes managed roots or is not a regular file')
    target_key, target_root = max(matches, key=lambda item: len(item[1].parts))
    resolved_relative = _relative(resolved.relative_to(target_root).as_posix())
    return locator, resolved, dict(root=key, relative_path=relative.as_posix(),
        resolved_root=target_key, resolved_relative_path=resolved_relative.as_posix(),
        original_was_symlink=locator.is_symlink())


def _identity(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _sync_directories(folder):
    directories = [folder] + [path for path in folder.rglob('*') if path.is_dir()]
    for path in sorted(directories, key=lambda item: len(item.parts), reverse=True):
        descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def _copy_source(source, destination, expected, *, size_bound):
    """No-follow pinned regular source, streamed SHA and before/after identity."""
    before = source.stat()
    if before.st_size > size_bound:
        raise ExportRejected('Scientific artifact exceeds byte bound')
    checksum = hashlib.sha256()
    count = 0
    descriptor = os.open(source, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        with os.fdopen(descriptor, 'rb') as original:
            if not stat.S_ISREG(os.fstat(original.fileno()).st_mode) or _identity(os.fstat(original.fileno())) != _identity(before):
                raise ExportRejected('Scientific artifact changed before copying')
            destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            with destination.open('xb') as copied:
                block = original.read(1024 * 1024)
                while block:
                    copied.write(block)
                    checksum.update(block)
                    count += len(block)
                    if count > size_bound:
                        raise ExportRejected('Scientific artifact grew beyond byte bound')
                    block = original.read(1024 * 1024)
                copied.flush()
                os.fsync(copied.fileno())
            if _identity(os.fstat(original.fileno())) != _identity(before) or _identity(source.stat()) != _identity(before):
                raise ExportRejected('Scientific artifact changed during copying')
    except OSError as error:
        raise ExportRejected('Scientific artifact cannot be copied safely') from error
    if checksum.hexdigest() != expected or count != before.st_size:
        raise ExportRejected('Scientific artifact SHA differs from selected registry')
    destination.chmod(0o600)
    directory = os.open(destination.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
    return count


def _graph(registry, limits):
    if registry.get('schema') != REGISTRY_SCHEMA:
        raise ExportRejected('Explicit scientific artifact registry required')
    nodes = {}
    rows = registry.get('nodes') or []
    if len(rows) > limits['max_files']:
        raise ExportRejected('Scientific graph exceeds file bound')
    for row in rows:
        ident = _id(row.get('id'))
        if ident in nodes:
            raise ExportRejected('Duplicate scientific artifact identity')
        if row.get('role') not in ROLES:
            raise ExportRejected('Unknown scientific artifact role')
        _id(row.get('source_id'))
        _digest(row.get('sha256'))
        deps = row.get('dependencies', [])
        if not isinstance(deps, list) or len(deps) != len(set(deps)):
            raise ExportRejected('Unique dependency IDs required')
        for value in deps:
            _id(value)
        if row.get('usage', 'provenance') not in ('provenance', 'fitting', 'normalization', 'checkpoint_selection', 'evaluation'):
            raise ExportRejected('Explicit supported scientific usage required')
        if row.get('split') == 'sealed_test' and row.get('usage') in ('fitting', 'normalization', 'checkpoint_selection'):
            raise ExportRejected('Sealed evidence cannot be used for fitting or selection')
        nodes[ident] = row
    roots = registry.get('root_ids')
    if not isinstance(roots, list) or not roots:
        raise ExportRejected('Selected scientific root identities required')
    visited, active, ordered, missing = set(), set(), [], []
    def visit(ident, parent=None):
        _id(ident)
        if ident in active:
            raise ExportRejected('Scientific dependency cycle')
        if ident not in nodes:
            missing.append(dict(artifact_id=ident, required_by=parent, reason='DEPENDENCY_NOT_REGISTERED'))
        elif ident not in visited:
            active.add(ident)
            for dep in nodes[ident].get('dependencies', []):
                visit(dep, ident)
            active.remove(ident)
            visited.add(ident)
            ordered.append(nodes[ident])
    for ident in roots:
        visit(ident)
    return ordered, missing


def export_evidence(destination, registry, managed_roots, *, protocol_path, protocol_sha256,
                    export_id=None, limits=None):
    """Copy only the selected transitive graph. Incomplete graphs remain PARTIAL."""
    limits = _bound_limits(limits)
    folder = Path(destination).absolute()
    if folder.exists() or folder.is_symlink() or not folder.parent.is_dir():
        raise ExportRejected('New export destination with existing parent required')
    roots = {str(key): Path(value).resolve(strict=True) for key, value in managed_roots.items()}
    if not roots or any(not root.is_dir() for root in roots.values()):
        raise ExportRejected('Existing explicit managed directories required')
    folder = folder.resolve()
    if any(folder.is_relative_to(root) or root.is_relative_to(folder) for root in roots.values()):
        raise ExportRejected('Export destination overlaps managed sources')
    protocol_raw = Path(protocol_path).read_bytes()
    if sha(protocol_raw) != _digest(protocol_sha256):
        raise ExportRejected('Collection export protocol SHA differs')
    protocol = json.loads(protocol_raw)
    if protocol.get('schema') != 'explorer_collection_export_protocol_v1':
        raise ExportRejected('Collection export protocol schema differs')
    _check_text(protocol_raw, 'protocol', 'protocol.json', limits)
    ordered, missing = _graph(registry, limits)
    missing += list(registry.get('missing_dependencies') or [])
    if registry.get('dependency_inventory_complete') is not True:
        missing.append(dict(reason='DEPENDENCY_INVENTORY_NOT_QUALIFIED'))
    assert_secret_free(missing)
    metadata = {key: registry.get(key, []) for key in ('run_ids', 'episode_ids', 'job_ids', 'source_commits', 'episodes', 'metric_checks')}
    assert_secret_free(metadata)
    if not metadata['source_commits']:
        missing.append(dict(reason='SOURCE_COMMIT_NOT_DECLARED'))
    if any(not isinstance(value, str) or not re.fullmatch('[0-9a-f]{40}', value) for value in metadata['source_commits']):
        raise ExportRejected('Complete source commit identity required')
    required = protocol.get('episode_required') or []
    episode_records = {row.get('episode_id'): row for row in metadata['episodes']}
    for ident in metadata['episode_ids']:
        episode = episode_records.get(ident, {})
        fields = [key for key in required if key not in episode]
        if fields:
            missing.append(dict(episode_id=ident, reason='EPISODE_METADATA_INCOMPLETE', fields=fields))
    folder.mkdir(mode=0o700)
    files, total = [], 0
    try:
        _write(folder / 'collection-protocol.json', protocol_raw)
        for node in ordered:
            ident = node['id']
            member = 'objects/' + node['sha256'] + '/' + sha(ident.encode())[:16] + '.bin'
            target = folder / member
            locator = None
            if 'inline_utf8' in node:
                raw = node['inline_utf8'].encode('utf-8')
                if len(raw) > limits['max_json_bytes'] or sha(raw) != node['sha256']:
                    raise ExportRejected('Inline scientific original differs from SHA or bound')
                _check_text(raw, node['role'], node.get('relative_path', ''), limits)
                _write(target, raw)
                size = len(raw)
            else:
                original, resolved, locator = _source(node, roots)
                size = _copy_source(resolved, target, node['sha256'], size_bound=limits['max_file_bytes'])
                if original.resolve(strict=True) != resolved:
                    raise ExportRejected('Source symlink changed during copying')
                if node['role'] in JSON_ROLES or str(original).lower().endswith(('.json', '.jsonl', '.ndjson')):
                    if size > limits['max_json_bytes']:
                        raise ExportRejected('Scientific JSON exceeds inspection bound')
                    _check_text(target.read_bytes(), node['role'], original.name, limits)
                elif size <= limits['max_json_bytes']:
                    _check_text(target.read_bytes(), node['role'], original.name, limits)
            total += size
            if total > limits['max_total_bytes']:
                raise ExportRejected('Scientific export total byte bound exceeded')
            record = dict(id=ident, relative_path=member, size_bytes=size, sha256=node['sha256'],
                role=node['role'], source_id=node['source_id'], dependencies=node.get('dependencies', []),
                split=node.get('split'), usage=node.get('usage', 'provenance'),
                media_name=node.get('relative_path', ''), source_locator=locator)
            if node.get('projection_of') is not None:
                record['projection_of'] = node['projection_of']
            files.append(record)
        body = dict(schema=SCHEMA, export_id=_id(export_id or uuid.uuid4().hex),
            created_at=datetime.now(timezone.utc).isoformat(), protocol_sha256=protocol_sha256,
            protocol_file='collection-protocol.json', root_ids=registry['root_ids'], **metadata,
            parents=[dict(child=row['id'], parent=dep) for row in ordered for dep in row.get('dependencies', [])],
            file_manifest=files, missing_dependencies=missing,
            verification='PARTIAL' if missing else 'COMPLETE_UNVERIFIED',
            scope='explicit_selected_dependency_graph_not_whole_state_or_scientific_success',
            raw_originals_rewritten=False, canonical_projections_are_separate=True,
            originals_deleted_after_export=False, physical_actions_authorized=False,
            fitting_performed=False, cross_database_filesystem_atomic_snapshot=False,
            limits=limits, total_payload_bytes=total)
        assert_secret_free(body)
        raw = canonical(body)
        if len(raw) > limits['max_manifest_bytes']:
            raise ExportRejected('Scientific export manifest bound exceeded')
        _write(folder / 'manifest.json', raw)
        _sync_directories(folder)
        return dict(manifest_path=str(folder / 'manifest.json'), manifest_sha256=sha(raw),
                    verification=body['verification'], files=len(files), total_payload_bytes=total)
    except Exception as error:
        # A rejected copy never leaves forbidden bytes in the destination.
        for path in folder.rglob('*'):
            if path.is_file() and path.name != 'export-rejected.json':
                path.unlink()
        _write(folder / 'export-rejected.json', canonical(dict(schema='explorer_research_export_rejection_v1',
            reason=str(error), error_type=type(error).__name__, original_sources_modified=False)))
        raise


def _read_manifest(path, expected):
    path = Path(path).resolve(strict=True)
    if path.stat().st_size > LIMITS['max_manifest_bytes']:
        raise ExportRejected('Export manifest bound exceeded')
    raw = path.read_bytes()
    if sha(raw) != _digest(expected):
        raise ExportRejected('Export manifest SHA changed')
    body = json.loads(raw)
    if body.get('schema') != SCHEMA:
        raise ExportRejected('Export manifest schema differs')
    assert_secret_free(body)
    return path, body


def verify_export(manifest_path, expected_sha256, *, metric_verifier=None):
    """Verify selected bytes/edges without executing any retained implementation."""
    path, body = _read_manifest(manifest_path, expected_sha256)
    limits = _bound_limits(body.get('limits'))
    rows = body.get('file_manifest') or []
    if len(rows) > limits['max_files']:
        raise ExportRejected('Export inventory bound exceeded')
    ids, members, total, records = set(), set(), 0, {}
    for row in rows:
        ident = _id(row['id'])
        relative = _relative(row['relative_path'])
        if ident in ids or relative.as_posix() in members or row.get('role') not in ROLES:
            raise ExportRejected('Duplicate/invalid export inventory')
        member = path.parent / relative
        if member.is_symlink() or not member.resolve().is_relative_to(path.parent) or not member.is_file():
            raise ExportRejected('Export member escapes bundle or is missing')
        checksum = hashlib.sha256()
        size = 0
        before = member.stat()
        descriptor = os.open(member, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, 'rb') as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode) or _identity(os.fstat(stream.fileno())) != _identity(before):
                raise ExportRejected('Export member changed before verification')
            block = stream.read(1024 * 1024)
            while block:
                checksum.update(block)
                size += len(block)
                if size > limits['max_file_bytes']:
                    raise ExportRejected('Export member size bound exceeded')
                block = stream.read(1024 * 1024)
            if _identity(os.fstat(stream.fileno())) != _identity(before) or _identity(member.stat()) != _identity(before):
                raise ExportRejected('Export member changed during verification')
        if size != row['size_bytes'] or checksum.hexdigest() != _digest(row['sha256']):
            raise ExportRejected('Export member size/SHA changed')
        if row.get('split') == 'sealed_test' and row.get('usage') in ('fitting', 'normalization', 'checkpoint_selection'):
            raise ExportRejected('Sealed export fitting role is forbidden')
        if size <= limits['max_json_bytes'] or row['role'] in JSON_ROLES:
            if size > limits['max_json_bytes']:
                raise ExportRejected('Scientific JSON exceeds inspection bound')
            _check_text(member.read_bytes(), row['role'], row.get('media_name', ''), limits)
        records[ident] = (row, member)
        ids.add(ident)
        members.add(relative.as_posix())
        total += size
    if total != body.get('total_payload_bytes') or total > limits['max_total_bytes']:
        raise ExportRejected('Export total payload size differs')
    missing = body.get('missing_dependencies') or []
    missing_ids = {row.get('artifact_id') for row in missing}
    ordered, graph_missing = _graph(dict(schema=REGISTRY_SCHEMA, nodes=rows, root_ids=body.get('root_ids')), limits)
    if {row['id'] for row in ordered} != ids or any(row['artifact_id'] not in missing_ids for row in graph_missing):
        raise ExportRejected('Export graph has unselected files or unreported dependencies')
    parents = {(row['id'], dep) for row in rows for dep in row.get('dependencies', [])}
    declared = body.get('parents') or []
    if len(declared) != len(parents) or {(row.get('child'), row.get('parent')) for row in declared} != parents:
        raise ExportRejected('Export parent edge table differs from artifact graph')
    for row, member in records.values():
        for dep in row.get('dependencies', []):
            if dep not in ids and dep not in missing_ids:
                raise ExportRejected('Unreported missing export dependency')
        projection = row.get('projection_of')
        if row['role'] == 'canonical_projection' and not projection:
            raise ExportRejected('Canonical projection requires separate raw binding')
        if projection:
            original = records.get(projection.get('id'))
            if not original or original[0]['sha256'] != projection.get('sha256') or projection['id'] not in row.get('dependencies', []):
                raise ExportRejected('Canonical projection lost exact raw binding')
            document = json.loads(member.read_bytes())
            if document.get('schema') == 'explorer_scientific_identity_projection_v1' and document.get('raw_sha256') != original[0]['sha256']:
                raise ExportRejected('Canonical projection document raw SHA differs')
    for ident in body['root_ids']:
        if ident not in ids and ident not in missing_ids:
            raise ExportRejected('Export root lost from inventory')
    protocol_path = path.parent / _relative(body.get('protocol_file'))
    if protocol_path.is_symlink() or not protocol_path.resolve().is_relative_to(path.parent) or sha(protocol_path.read_bytes()) != body['protocol_sha256']:
        raise ExportRejected('Export protocol binding changed')
    actual = {item.relative_to(path.parent).as_posix() for item in path.parent.rglob('*') if item.is_file() or item.is_symlink()}
    allowed = members | {'manifest.json', body['protocol_file'], 'restore-verification.json'}
    if actual - allowed:
        raise ExportRejected('Unregistered extra files are forbidden in scientific bundle')
    metrics = []
    for check in body.get('metric_checks') or []:
        required = ('protocol', 'trials', 'report', 'implementation')
        if any(check.get(key) not in records for key in required):
            raise ExportRejected('Metric recomputation dependencies are incomplete')
        if metric_verifier is None:
            metrics.append(dict(report=check['report'], status='NOT_RECOMPUTED'))
        else:
            blobs = {key: records[check[key]][1].read_bytes() for key in required}
            computed = metric_verifier(blobs, {key: records[check[key]][0]['sha256'] for key in required})
            if canonical(computed) != canonical(json.loads(blobs['report'])):
                raise ExportRejected('Independent metric recomputation differs from retained report')
            metrics.append(dict(report=check['report'], status='RECOMPUTED_MATCH'))
    return dict(manifest_sha256=expected_sha256, files=len(rows), total_payload_bytes=total,
        verification='PARTIAL' if missing else 'COMPLETE_UNVERIFIED', metric_checks=metrics,
        originals_required=False, scientific_success_claimed=False)


def restore_export(manifest_path, expected_sha256, destination, *, metric_verifier=None):
    """Independent new copy and verification; immutable manifest is not rewritten."""
    source, body = _read_manifest(manifest_path, expected_sha256)
    verify_export(source, expected_sha256)
    target = Path(destination).absolute()
    if target.exists() or target.is_symlink() or not target.parent.is_dir():
        raise ExportRejected('Independent restore destination must be new')
    target = target.resolve()
    if target.is_relative_to(source.parent) or source.parent.is_relative_to(target):
        raise ExportRejected('Independent restore must be outside export bundle')
    target.mkdir(mode=0o700)
    names = ['manifest.json', body['protocol_file']] + [row['relative_path'] for row in body['file_manifest']]
    for name in names:
        relative = _relative(name)
        original = source.parent / relative
        expected = expected_sha256 if name == 'manifest.json' else body['protocol_sha256'] if name == body['protocol_file'] else next(row['sha256'] for row in body['file_manifest'] if row['relative_path'] == name)
        _copy_source(original, target / relative, expected, size_bound=LIMITS['max_file_bytes'])
    result = verify_export(target / 'manifest.json', expected_sha256, metric_verifier=metric_verifier)
    complete = not body['missing_dependencies'] and all(row['status'] == 'RECOMPUTED_MATCH' for row in result['metric_checks'])
    receipt = dict(result, schema='explorer_research_export_restore_v1',
        verification='VERIFIED_COMPLETE' if complete else result['verification'],
        independent_destination=True, source_manifest_rewritten=False,
        scope=body['scope'], physical_actions_authorized=False)
    _write(target / 'restore-verification.json', canonical(receipt))
    _sync_directories(target)
    return receipt
