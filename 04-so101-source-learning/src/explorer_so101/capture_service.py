"""ENGINEERING_GLUE: Thin fixed-Station binding for source04 data lifecycle and COMMON permission."""
from copy import deepcopy
import hashlib
from pathlib import Path
import re
import uuid

from explorer_shared.station_observed_activation import binding
from .capture_adapter import StationCaptureAdapter, StartRejectedBeforeWriter, SCHEMA


class StationCaptureService:
    def __init__(self, station, permission, root, *, clock=None):
        self.station, self.permission = station, permission
        options = {} if clock is None else {'clock': clock}
        self.adapter = StationCaptureAdapter(root, status=station.status,
            record_start=self._start_original, record_finish=lambda body: station.command('record/finish', body),
            read_manifest=lambda ident: (station.root/'recordings'/ident/'manifest.json').read_bytes(),
            archive_original=station.archive, clock_domain=station.clock_domain, boot_id=station.boot, **options)
        self.writer, self.root, self.schema = self, self.adapter.root, SCHEMA

    def _check_start(self, body, actor_id):
        proof = (body.get('context') or {}).get('source04_demo_permission') or {}
        selected = binding(proof.get('binding'))
        if (selected['purpose'] != 'source04_demo' or
                (selected['skill_id'], selected['skill_version'], selected['spec_sha256']) !=
                (body.get('skill_id'), body.get('skill_version'), body.get('skill_spec_sha256')) or
                selected['source_session_id'] != uuid.UUID(str(body.get('session_id'))).hex):
            raise ValueError('Exact confirmed demonstration skill/session/attempt required')
        grant = self.permission.check(proof.get('grant_id'), selected, proof.get('control_epoch'))
        if grant['actor_id'] != actor_id or self.station.mode != 'teleoperation':
            raise ValueError('Capture requires its exact already-started observed demonstration owner')

    def _start_original(self, body):
        # Keep correlation through the ORIGINAL Station lock, across admission
        # and record/start. Known denial before the writer is safe to retry.
        with self.station.lock:
            try:self._check_start(body, body.get('actor'))
            except (ValueError,OSError) as error:raise StartRejectedBeforeWriter(str(error)) from error
            return self.station.command('record/start', body)

    def status(self):
        result = self.adapter.status()
        return dict(result, active_recording_id=result['recording_id'],
            ready_recordings=self.ready_recordings(),
            project_id='04', capture=dict(result['capture'], project_id='04') if result['capture'] else None)

    def capabilities(self):
        return {'schema': SCHEMA, 'projects': ['04'], 'action_dimension': 6,
                'same_original_station_writer': True, 'recording_modes': ['demonstration'],
                'recording_operations': ['start', 'stage', 'pause', 'resume', 'cancel', 'finish', 'review'],
                'requires_current_source04_demo_permission': True, 'physical_qualification': False}

    def ready_recordings(self):return self.adapter.recordings()
    def manifest(self, ident):return self.adapter.manifest(ident)
    def archive(self, ident):return self.adapter.archive(ident)
    def recording_review(self, ident):return self.adapter.recording_review(ident)

    def start(self, body, actor_id):
        if not isinstance(body, dict) or set(body) != {'record_start_body', 'permission'}:
            raise ValueError('Fixed source04 capture context and current demo permission required')
        original = deepcopy(body['record_start_body'])
        proof = deepcopy(body['permission'])
        if (not isinstance(original, dict) or not isinstance(proof, dict) or
                set(proof) != {'grant_id', 'binding', 'control_epoch'}):
            raise ValueError('Exact native grant/binding/returned epoch required')
        context = dict(original.get('context') or {})
        if 'source04_demo_permission' in context:
            raise ValueError('Demo permission provenance is inserted only by the fixed source adapter')
        context['source04_demo_permission'] = proof;original['context'] = context
        with self.station.lock:self._check_start(original,actor_id)
        return self.adapter.start(original, actor_id)

    def _owned(self, recording_id, actor_id):
        row = self.adapter.status()
        if row['recording_id'] != recording_id or (row['capture'] or {}).get('actor_id') != actor_id:
            raise ValueError('Only the exact authenticated current capture owner may label or finish it')

    def command(self, operation, body, actor_id):
        if not isinstance(body, dict):raise ValueError('Capture request object required')
        if operation == 'record/start':return self.start(body, actor_id)
        if operation == 'record/review':
            if set(body) != {'recording_id', 'decision', 'expected_archive_sha256'}:
                raise ValueError('Review the exact sealed source archive')
            return self.adapter.review(body['recording_id'], body['decision'], actor_id, body['expected_archive_sha256'])
        ident = body.get('recording_id');self._owned(ident, actor_id)
        if operation in ('record/pause', 'record/resume', 'record/cancel'):
            if set(body) != {'recording_id'}:raise ValueError('Only the exact capture ID is accepted')
            if operation == 'record/cancel':return self.adapter.cancel(ident, actor_id)
            return getattr(self.adapter, operation.split('/')[1])(ident)
        if operation == 'record/finish':
            if set(body)-{'recording_id', 'outcome'}:raise ValueError('Bounded original outcome request required')
            return self.adapter.finish(ident, body.get('outcome', 'unknown'))
        if operation == 'record/stage':
            with self.adapter.operations:
                owner = self.adapter._owned(ident, ('RECORDING', 'PAUSED'));self.adapter._source(owner)
                return self.station.record_stage(body)
        raise ValueError('Unknown source04 capture metadata operation')

    def preview_frame(self, recording_id, checksum):
        if not isinstance(checksum, str) or not re.fullmatch('[0-9a-f]{64}', checksum):
            raise ValueError('Pinned original JPEG SHA required')
        manifest = self.adapter.manifest(recording_id)
        names = [name for name, expected in manifest['original_jpegs_sha256'].items() if expected == checksum]
        if not names:raise ValueError('JPEG does not belong to this sealed source recording')
        folder = (self.station.root/'recordings'/recording_id).resolve()
        path = (folder/names[0]).resolve()
        if not path.is_relative_to(folder):raise ValueError('Pinned original frame path required')
        raw = path.read_bytes()
        if (not 0 < len(raw) <= 8*1024**2 or hashlib.sha256(raw).hexdigest() != checksum):
            raise ValueError('Original source JPEG changed or exceeds preview budget')
        return raw

    def close(self):self.adapter.close()
