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
from .initial_state_export import recorded_claims, inspect_recorded_bundle

SCHEMA = 'explorer_research_export_request_v1'
MAX_SELECTED_JOBS = 32
ID = re.compile(r'[0-9a-f]{32}')
TERMINAL = {'completed', 'failed', 'cancelled'}
EXTENSIONS = {'.json', '.jsonl', '.ndjson', '.npz', '.npy', '.png', '.jpg', '.jpeg',
    '.webp', '.mp4', '.safetensors', '.pt', '.stl', '.obj', '.urdf', '.xml', '.py',
    '.onnx', '.hef', '.tar', '.cdr', '.tif', '.tiff'}
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


TAR_BLOCK = 512
TAR_METADATA_BYTES = 64 * 1024
TAR_METADATA_TOTAL_BYTES = 16 * 1024**2


def _tar_number(raw):
    if raw[0] == 0x80:  # GNU positive base-256, never a negative/sparse sentinel.
        return int.from_bytes(raw[1:], 'big')
    value = raw.rstrip(b'\x00 ').lstrip(b' ')
    if not value or not re.fullmatch(b'[0-7]+', value):
        raise ExportRejected('Raw TAR malformed numeric header')
    return int(value, 8)


def _tar_string(raw):
    head, _, tail = raw.partition(b'\x00')
    if tail.strip(b'\x00'):
        raise ExportRejected('Raw TAR hidden bytes after header string')
    try:
        return head.decode('utf-8')
    except UnicodeError as error:
        raise ExportRejected('Raw TAR non-UTF8 metadata is not qualified') from error


def _tar_path(name):
    if len(name.encode('utf-8')) > 4096 or any(ord(char) < 32 or ord(char) == 127 for char in name):
        raise ExportRejected('Raw TAR path length/control characters are forbidden')
    assert_secret_free(name)
    return _relative(name.rstrip('/'))


def _tar_owner(value):
    assert_secret_free(value)
    if not re.fullmatch(r'[A-Za-z0-9_.@-]{0,64}', value):
        raise ExportRejected('Raw TAR owner metadata is not a bounded account name')


def _tar_pax(raw):
    """Parse at most64KiB ourselves; tarfile must not allocate an untrusted PAX."""
    values, offset = {}, 0
    allowed = {'path', 'size', 'mtime', 'atime', 'ctime', 'uid', 'gid', 'uname', 'gname'}
    while offset < len(raw):
        separator = raw.find(b' ', offset, offset + 12)
        length_bytes = raw[offset:separator] if separator != -1 else b''
        if not re.fullmatch(b'[1-9][0-9]{0,8}', length_bytes):
            raise ExportRejected('Raw TAR malformed PAX record length')
        end = offset + int(length_bytes)
        if end > len(raw) or end <= separator + 2 or raw[end - 1:end] != b'\n':
            raise ExportRejected('Raw TAR truncated PAX record')
        text = raw[separator + 1:end - 1].decode('utf-8', errors='strict')
        key, equals, value = text.partition('=')
        if not equals or key not in allowed or key in values or '\x00' in value or '\n' in value:
            raise ExportRejected('Raw TAR unsupported/sparse/auth PAX metadata')
        assert_secret_free({key: value})
        if key == 'path':
            _tar_path(value)
        elif key in {'size', 'uid', 'gid'}:
            if not re.fullmatch('[0-9]{1,20}', value) or int(value) > LIMITS['max_file_bytes']:
                raise ExportRejected('Raw TAR PAX numeric bound')
        elif key in {'mtime', 'atime', 'ctime'}:
            if not re.fullmatch(r'-?[0-9]{1,20}(?:\.[0-9]{1,20})?', value):
                raise ExportRejected('Raw TAR malformed PAX timestamp')
        elif key in {'uname', 'gname'}:
            _tar_owner(value)
        values[key] = value
        offset = end
    return values


def _tar_zero_padding(stream, length):
    while length:
        part = stream.read(min(length, TAR_METADATA_BYTES))
        if not part or part.strip(b'\x00'):
            raise ExportRejected('Raw TAR truncated/nonzero padding')
        length -= len(part)


def _preflight_tar(stream, file_size):
    """Check raw512-byte headers before any tarfile extension-header parser."""
    count, expanded, metadata_bytes = 0, 0, 0
    global_pax, local_pax, longname, names = {}, {}, None, set()
    extension_chain, local_pending = 0, False
    while stream.tell() < file_size:
        header = stream.read(TAR_BLOCK)
        if len(header) != TAR_BLOCK:
            raise ExportRejected('Raw TAR truncated header')
        if header == bytes(TAR_BLOCK):
            if stream.read(TAR_BLOCK) != bytes(TAR_BLOCK) or local_pending or longname is not None:
                raise ExportRejected('Raw TAR incomplete terminator or orphan metadata')
            _tar_zero_padding(stream, file_size - stream.tell())
            return
        count += 1
        if count > LIMITS['max_files']:
            raise ExportRejected('Raw TAR header count exceeds bound')
        checksum = _tar_number(header[148:156])
        if checksum != sum(header[:148]) + 8 * 32 + sum(header[156:]):
            raise ExportRejected('Raw TAR header checksum is malformed')
        if header[257:265] not in (b'ustar\x0000', b'ustar  \x00', bytes(8)):
            raise ExportRejected('Raw TAR unsupported header format')
        kind, size = header[156:157], _tar_number(header[124:136])
        if kind not in (b'0', b'\x00', b'5', b'x', b'g', b'L'):
            raise ExportRejected('Raw TAR links/devices/sparse/unknown types are forbidden')
        if _tar_string(header[157:257]):
            raise ExportRejected('Raw TAR link metadata is forbidden')
        for field in (header[265:297], header[297:329]):
            _tar_owner(_tar_string(field))
        name = _tar_string(header[:100])
        prefix = _tar_string(header[345:500]) if header[257:263] == b'ustar\x00' else ''
        name = prefix + '/' + name if prefix else name
        if kind in (b'x', b'g', b'L'):
            extension_chain += 1
            if extension_chain > 8:
                raise ExportRejected('Raw TAR recursive extension chain exceeds bound')
            if name not in ('././@PaxHeader', '././@LongLink'):
                _tar_path(name)
            metadata_bytes += size
            if size > TAR_METADATA_BYTES or metadata_bytes > TAR_METADATA_TOTAL_BYTES:
                raise ExportRejected('Raw TAR extension metadata exceeds64KiB/total bound')
            if size > file_size - stream.tell():
                raise ExportRejected('Raw TAR truncated metadata')
            raw = stream.read(size)
            if kind == b'L':
                if longname is not None or local_pending or not raw.endswith(b'\x00'):
                    raise ExportRejected('Raw TAR ambiguous GNU longname')
                longname = _tar_string(raw)
                _tar_path(longname)
            else:
                values = _tar_pax(raw)
                if kind == b'g':
                    global_pax.update(values)
                else:
                    if local_pending or longname is not None:
                        raise ExportRejected('Raw TAR ambiguous local PAX metadata')
                    local_pax = values
                    local_pending = True
        else:
            effective = dict(global_pax, **local_pax)
            name = effective.get('path', longname if longname is not None else name)
            _tar_path(name)
            if name.rstrip('/') in names:
                raise ExportRejected('Raw TAR duplicate member path')
            names.add(name.rstrip('/'))
            size = int(effective.get('size', size))
            if kind == b'5' and size != 0:
                raise ExportRejected('Raw TAR directory payload is forbidden')
            if size > LIMITS['max_file_bytes'] or size > file_size - stream.tell():
                raise ExportRejected('Raw TAR truncated/member size exceeds bound')
            expanded += size
            if expanded > LIMITS['max_total_bytes']:
                raise ExportRejected('Raw TAR expansion bound')
            if kind != b'5' and Path(name).suffix.lower() not in EXTENSIONS - {'.tar'}:
                raise ExportRejected('Raw TAR unqualified member format')
            stream.seek(size, os.SEEK_CUR)
            local_pax, longname = {}, None
            extension_chain, local_pending = 0, False
        _tar_zero_padding(stream, (-size) % TAR_BLOCK)
    raise ExportRejected('Raw TAR missing complete terminator')


class _BoundedTarReader:
    """Same pinned descriptor; cap reads even if bytes mutate after preflight."""
    def __init__(self, stream, size):
        self.stream, self.size = stream, size

    def read(self, size=-1):
        if type(size) is not int or not 0 <= size <= TAR_METADATA_BYTES:
            raise ExportRejected('Raw TAR parser read exceeds64KiB allocation bound')
        return self.stream.read(min(size, self.size - self.tell()))

    def seek(self, offset, whence=os.SEEK_SET):
        target = offset if whence == os.SEEK_SET else self.tell() + offset if whence == os.SEEK_CUR else self.size + offset if whence == os.SEEK_END else -1
        if not 0 <= target <= self.size:
            raise ExportRejected('Raw TAR parser seek outside pinned file')
        return self.stream.seek(target)

    def tell(self):
        return self.stream.tell()


def _inspect_tar_members(reader):
    count, expanded = 0, 0
    with tarfile.open(fileobj=reader, mode='r:') as archive:
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
                        parts = [first]
                        remaining = member.size - len(first)
                        while remaining:
                            part = stream.read(min(remaining, TAR_METADATA_BYTES))
                            if not part:
                                raise ExportRejected('Raw TAR truncated inspected text')
                            parts.append(part)
                            remaining -= len(part)
                        _check_text(b''.join(parts), 'source_archive_member', member.name, LIMITS)


def _inspect_tar(path, *, expected_sha256=None):
    """No extraction/decompression; preflight and parser share one no-follow FD."""
    path = Path(path)
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, 'rb') as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_size > LIMITS['max_file_bytes']:
            raise ExportRejected('Raw TAR source is not a bounded regular file')
        identity = lambda item: (item.st_dev, item.st_ino, item.st_size, item.st_mtime_ns, item.st_ctime_ns)
        checksum = hashlib.file_digest(stream, 'sha256').hexdigest()
        if expected_sha256 is not None and checksum != expected_sha256:
            raise ExportRejected('Raw TAR source SHA changed before inspection')
        stream.seek(0)
        _preflight_tar(stream, before.st_size)
        stream.seek(0)
        _inspect_tar_members(_BoundedTarReader(stream, before.st_size))
        stream.seek(0)
        final_checksum = hashlib.file_digest(stream, 'sha256').hexdigest()
        if (final_checksum != checksum or identity(os.fstat(stream.fileno())) != identity(before)
                or identity(path.stat(follow_symlinks=False)) != identity(before)):
            raise ExportRejected('Raw TAR source changed during inspection')


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
    initial_permissions, deferred_binary, initial_claims_seen, initial_receipts = {}, [], set(), set()
    metadata_complete = False
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
    def initial_claims(value, base, owner):
        for record in recorded_claims(value):
            identity = sha(canonical(record))
            if (owner, identity) not in initial_claims_seen:
                initial_claims_seen.add((owner, identity))
                locator = record.get('manifest_path')
                try:
                    admitted = inspect_recorded_bundle(record, base=base, managed_file=managed,
                        hash_file=_hash, limits=LIMITS)
                    manifest = admitted['manifest_path']
                    _, _, manifest_key, manifest_relative = managed(manifest)
                    manifest_node = 'file:' + sha((manifest_key + '/' + manifest_relative).encode())
                    admission_node = 'initial-state:' + sha((manifest_node + admitted['manifest_sha256']).encode())
                    permissions = {}
                    for payload in admitted['payloads']:
                        path = payload['path']
                        permission = dict(admitted['admission'], manifest_node_id=manifest_node, admission_node_id=admission_node,
                            payload_sha256=payload['sha256'], payload_size_bytes=payload['size_bytes'])
                        if str(path) in initial_permissions and initial_permissions[str(path)] != permission:
                            raise ExportRejected('Initial-state payload has conflicting bundle bindings')
                        permissions[str(path)] = permission
                    upgrades = []
                    for node in nodes:
                        if node.get('root') in roots:
                            existing_path = str(Path(roots[node['root']]) / node['relative_path'])
                            permission = permissions.get(existing_path)
                            if permission is not None:
                                if (node['sha256'], node['size_bytes']) != (permission['payload_sha256'], permission['payload_size_bytes']):
                                    raise ExportRejected('Initial-state previously selected payload changed')
                                upgrades.append((node, permission))
                    # Grant the whole validated inventory together, never a
                    # prefix left behind by a later descriptor rejection.
                    initial_permissions.update(permissions)
                    for node, permission in upgrades:
                        node['format_admission'] = permission
                        node['dependencies'] = [permission['admission_node_id']]
                    if admission_node not in initial_receipts:
                        initial_receipts.add(admission_node)
                        receipt = dict(admitted['admission'], manifest_root=manifest_key,
                            manifest_relative_path=manifest_relative,
                            payload_inventory={payload['path'].relative_to(manifest.parent).as_posix():
                                dict(sha256=payload['sha256'], size_bytes=payload['size_bytes']) for payload in admitted['payloads']})
                        raw_receipt = canonical(receipt)
                        nodes.append(dict(id=admission_node, role='profile', source_id=owner,
                            inline_utf8=raw_receipt.decode(), sha256=sha(raw_receipt), dependencies=[manifest_node]))
                    pending.append((manifest, admitted['manifest_sha256'], owner))
                    pending.extend((payload['path'], payload['sha256'], owner) for payload in admitted['payloads'])
                except (OSError, ValueError) as error:
                    issue(locator if isinstance(locator, str) else base,
                        'INITIAL_STATE_BUNDLE_WITHHELD_' + type(error).__name__.upper())
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
                document = json.loads(row[field])
                initial_claims(document, directory, ident)
                pending.extend((path, expected, ident) for path, expected in _references(document, directory))
    while pending or deferred_binary:
        if not pending:
            pending, deferred_binary = deferred_binary, []
            metadata_complete = True
        path, expected, owner = pending.pop(0)
        if Path(path).suffix.lower() in ('.mjb', '.bin') and not metadata_complete:
            deferred_binary.append((path, expected, owner))
        else:
            try:
                locator, resolved, key, relative = managed(path)
                if expected is not None and (not isinstance(expected, str) or not re.fullmatch('[0-9a-f]{64}', expected)):
                    raise ExportRejected('Scientific source SHA declaration is malformed')
                attempted_key = (str(resolved), expected)
                if attempted_key not in attempted:
                    attempted.add(attempted_key)
                    seen.add(str(resolved))
                    if len(seen) > LIMITS['max_files'] or (resolved.suffix.lower() not in EXTENSIONS and str(resolved) not in initial_permissions):
                        raise ExportRejected('Unknown scientific format or file count bound')
                    checksum, size = _hash(resolved)
                    permission = initial_permissions.get(str(resolved))
                    if permission is not None and (checksum, size) != (permission['payload_sha256'], permission['payload_size_bytes']):
                        raise ResearchExportBlocked('INITIAL_STATE_ADMITTED_PAYLOAD_CHANGED', str(path))
                    if expected is not None and expected != checksum:
                        raise ResearchExportBlocked('SOURCE_EXPECTED_SHA_MISMATCH', str(path))
                    if key.startswith('payload_') and expected is None and str(resolved) not in accepted:
                        raise ExportRejected('External scientific payload has no explicit source SHA')
                    if size > LIMITS['max_json_bytes'] and resolved.suffix.lower() in ('.json', '.jsonl', '.ndjson'):
                        raise ExportRejected('Scientific JSON inspection bound exceeded')
                    if resolved.suffix.lower() == '.tar':
                        _inspect_tar(resolved, expected_sha256=checksum)
                    elif size <= LIMITS['max_json_bytes']:
                        _check_text(resolved.read_bytes(), _role(resolved), resolved.name, LIMITS)
                    if str(resolved) not in accepted:
                        accepted.add(str(resolved))
                        total += size
                        if total > LIMITS['max_total_bytes']:
                            raise ResearchExportBlocked('SOURCE_INVENTORY_TOTAL_BOUND_EXCEEDED')
                        node_id = 'file:' + sha((key + '/' + relative).encode())
                        nodes.append(dict(id=node_id, role=_role(resolved), source_id=owner, root=key,
                            relative_path=relative, sha256=checksum, size_bytes=size,
                            dependencies=[] if permission is None else [permission['admission_node_id']],
                            **({} if permission is None else dict(format_admission=permission))))
                        if resolved.suffix.lower() == '.json':
                            document = json.loads(resolved.read_bytes())
                            initial_claims(document, resolved.parent, owner)
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
