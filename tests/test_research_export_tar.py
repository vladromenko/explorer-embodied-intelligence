"""Untrusted source archives, temporary files only; no extracted/device data."""
import io
from pathlib import Path
import tarfile

import pytest

from explorer import research_export_job as worker
from explorer_shared.research_export import ExportRejected
from explorer_shared.source_archive import sha


def archive_bytes(*, name='frames/frame.json', payload=b'{"fixture":true}',
                  pax=None, format=tarfile.PAX_FORMAT):
    result = io.BytesIO()
    with tarfile.open(fileobj=result, mode='w', format=format) as archive:
        member = tarfile.TarInfo(name)
        member.size = len(payload)
        member.mtime = 1730000000.125
        member.pax_headers = pax or {}
        archive.addfile(member, io.BytesIO(payload))
    return result.getvalue()


def save(tmp_path, raw):
    path = tmp_path / 'native.tar'
    path.write_bytes(raw)
    return path


def oversized_pax_header():
    member = tarfile.TarInfo('././@PaxHeader')
    member.type, member.size = tarfile.XHDTYPE, 1024**3
    return member.tobuf(format=tarfile.USTAR_FORMAT)


def test_huge_PAX_rejected_before_tarfile_parser_is_called(tmp_path, monkeypatch):
    path = save(tmp_path, oversized_pax_header() + bytes(9728))
    calls = []
    def forbidden_parser(*args, **kwargs):
        calls.append((args, kwargs))
        pytest.fail('tarfile parser was called before the raw header bound')
    monkeypatch.setattr(worker.tarfile, 'open', forbidden_parser)
    with pytest.raises(ExportRejected, match='metadata exceeds64KiB'):
        worker._inspect_tar(path)
    assert calls == [] and path.stat().st_size == 10240


def test_actual_small_native_PAX_mtime_and_CDR_depth_bytes_are_not_rewritten(tmp_path):
    raw = archive_bytes(payload=b'{"fixture":"' + b'x' * 150000 + b'"}')
    path = save(tmp_path, raw)
    worker._inspect_tar(path, expected_sha256=sha(raw))
    assert path.read_bytes() == raw
    with tarfile.open(path) as archive:
        member = archive.next()
        assert member.pax_headers['mtime'] == '1730000000.125'
        assert len(archive.extractfile(member).read()) == 150014
    for name in ('sources/raw-scan.cdr', 'frames/depth.tiff'):
        data = archive_bytes(name=name, payload=b'\x00\x01REAL_FORMAT_SOFTWARE_FIXTURE\xff')
        path.write_bytes(data)
        worker._inspect_tar(path, expected_sha256=sha(data))
        assert path.read_bytes() == data


def test_GNU_longname_is_bounded_and_original_bytes_preserved(tmp_path):
    raw = archive_bytes(name='frames/' + 'camera-' * 30 + '.json', format=tarfile.GNU_FORMAT)
    path = save(tmp_path, raw)
    worker._inspect_tar(path)
    assert path.read_bytes() == raw


@pytest.mark.parametrize('pax', [
    {'password': 'PRIVATE_VALUE'}, {'SCHILY.xattr.user.authorization': 'PRIVATE_VALUE'},
    {'GNU.sparse.map': '0,999'}, {'path': 'credentials/state.json'},
    {'uname': 'PASSWORD=PRIVATE_VALUE'}, {'size': '99999999999999999999'},
    {'gname': '{"password":"PRIVATE_VALUE"}'},
])
def test_auth_sparse_and_unsafe_PAX_metadata_rejected_before_parser(tmp_path, monkeypatch, pax):
    path = save(tmp_path, archive_bytes(pax=pax))
    calls = []
    monkeypatch.setattr(worker.tarfile, 'open', lambda *args, **kwargs: calls.append(True))
    with pytest.raises(ExportRejected):
        worker._inspect_tar(path)
    assert calls == []


def test_in_place_mutation_after_preflight_cannot_make_parser_allocate_huge_PAX(tmp_path, monkeypatch):
    path = save(tmp_path, archive_bytes())
    original = worker._preflight_tar
    reads = []
    def mutate(stream, size):
        original(stream, size)
        with path.open('r+b') as output:
            output.write(oversized_pax_header())
    original_read = worker._BoundedTarReader.read
    def tracked_read(self, size=-1):
        reads.append(size)
        return original_read(self, size)
    monkeypatch.setattr(worker, '_preflight_tar', mutate)
    monkeypatch.setattr(worker._BoundedTarReader, 'read', tracked_read)
    with pytest.raises(ExportRejected, match='allocation bound'):
        worker._inspect_tar(path)
    assert 1024**3 in reads
    assert path.stat().st_size == 10240


def test_path_replacement_after_preflight_cannot_swap_the_pinned_descriptor(tmp_path, monkeypatch):
    raw = archive_bytes()
    path = save(tmp_path, raw)
    original = worker._preflight_tar
    replacement = tmp_path / 'replacement.tar'
    replacement.write_bytes(oversized_pax_header() + bytes(9728))
    def replace(stream, size):
        original(stream, size)
        replacement.replace(path)
    monkeypatch.setattr(worker, '_preflight_tar', replace)
    with pytest.raises(ExportRejected, match='changed during inspection'):
        worker._inspect_tar(path, expected_sha256=sha(raw))


@pytest.mark.parametrize('kind', [tarfile.SYMTYPE, tarfile.LNKTYPE, tarfile.CHRTYPE,
                                  tarfile.BLKTYPE, tarfile.FIFOTYPE, tarfile.GNUTYPE_SPARSE])
def test_raw_nonregular_types_rejected_before_parser(tmp_path, monkeypatch, kind):
    member = tarfile.TarInfo('frames/sensor.json')
    member.type = kind
    path = save(tmp_path, member.tobuf(format=tarfile.GNU_FORMAT) + bytes(9728))
    calls = []
    monkeypatch.setattr(worker.tarfile, 'open', lambda *args, **kwargs: calls.append(True))
    with pytest.raises(ExportRejected, match='links/devices/sparse'):
        worker._inspect_tar(path)
    assert calls == []


@pytest.mark.parametrize('corrupt', ['checksum', 'truncated_header', 'truncated_payload',
                                   'missing_end', 'hidden_end', 'hidden_padding'])
def test_malformed_truncated_or_hidden_bytes_rejected(tmp_path, corrupt):
    raw = archive_bytes(format=tarfile.USTAR_FORMAT)
    if corrupt == 'checksum':
        raw = b'X' + raw[1:]
    elif corrupt == 'truncated_header':
        raw = raw[:100]
    elif corrupt == 'truncated_payload':
        raw = raw[:520]
    elif corrupt == 'missing_end':
        raw = raw[:1024]
    elif corrupt == 'hidden_end':
        raw = raw[:-1] + b'X'
    elif corrupt == 'hidden_padding':
        raw = raw[:550] + b'X' + raw[551:]
    path = save(tmp_path, raw)
    with pytest.raises(ExportRejected):
        worker._inspect_tar(path)


def test_header_count_and_total_expansion_are_checked_before_parser(tmp_path, monkeypatch):
    path = save(tmp_path, archive_bytes())
    monkeypatch.setitem(worker.LIMITS, 'max_files', 1)
    with pytest.raises(ExportRejected, match='header count'):
        worker._inspect_tar(path)
    monkeypatch.setitem(worker.LIMITS, 'max_files', 20000)
    monkeypatch.setitem(worker.LIMITS, 'max_total_bytes', 1)
    with pytest.raises(ExportRejected, match='expansion bound'):
        worker._inspect_tar(path)


def test_recursive_global_metadata_chain_and_orphan_local_metadata_are_rejected(tmp_path, monkeypatch):
    global_header = tarfile.TarInfo('global-header')
    global_header.type = tarfile.XGLTYPE
    global_header.size = 0
    path = save(tmp_path, global_header.tobuf(format=tarfile.USTAR_FORMAT) * 9 + bytes(1024))
    calls = []
    monkeypatch.setattr(worker.tarfile, 'open', lambda *args, **kwargs: calls.append(True))
    with pytest.raises(ExportRejected, match='recursive extension chain'):
        worker._inspect_tar(path)
    global_header.type = tarfile.XHDTYPE
    path.write_bytes(global_header.tobuf(format=tarfile.USTAR_FORMAT) + bytes(1024))
    with pytest.raises(ExportRejected, match='orphan metadata'):
        worker._inspect_tar(path)
    assert calls == []


def test_selected_SHA_and_no_follow_are_enforced(tmp_path):
    path = save(tmp_path, archive_bytes())
    with pytest.raises(ExportRejected, match='SHA changed'):
        worker._inspect_tar(path, expected_sha256='0' * 64)
    link = tmp_path / 'link.tar'
    link.symlink_to(path)
    with pytest.raises(OSError):
        worker._inspect_tar(link)
