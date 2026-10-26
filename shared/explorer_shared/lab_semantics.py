"""ENGINEERING_GLUE: Event-driven Experiment06 semantics from frozen single-owner camera cache.

Importing this module opens no hardware. A started worker explicitly leases CV's
released accelerator slot; it has no actuator transport or geometry output API.
"""
from contextlib import ExitStack
from copy import deepcopy
import hashlib
from pathlib import Path
import threading
import time

from .tabletop_grounding import CachedFrame


class BackendInitializationError(RuntimeError):
    def __init__(self, message, *, resources_released):
        super().__init__(message)
        self.resources_released = resources_released


class HailoVLMBackend:
    """Lazy binding to the actually inspected Pi HailoRT5.1.1 Python VLM ABI."""
    def __init__(self, model_path, download_receipt, *, runtime_version):
        if (runtime_version != '5.1.1' or download_receipt.get('artifact_version') != '5.1.1' or
                download_receipt.get('model') != 'Qwen2-VL-2B-Instruct' or
                download_receipt.get('actual_bytes') != 2344333123 or
                download_receipt.get('source_url') != 'https://dev-public.hailo.ai/v5.1.1/blob/Qwen2-VL-2B-Instruct.hef'):
            raise BackendInitializationError('Exact audited runtime and archived official Qwen2 artifact receipt required',
                                             resources_released=True)
        path = Path(model_path)
        try:
            file_size = path.stat().st_size
            with path.open('rb') as source:
                digest = hashlib.file_digest(source, 'sha256').hexdigest()
        except OSError as exc:
            raise BackendInitializationError('Staged artifact unavailable', resources_released=True) from exc
        if file_size != download_receipt['actual_bytes'] or digest != download_receipt.get('sha256'):
            raise BackendInitializationError('Staged VLM model hash/size mismatch', resources_released=True)
        try:
            import cv2
            from hailo_platform import VDevice, HailoSchedulingAlgorithm
            from hailo_platform.genai import VLM
        except ImportError as exc:
            raise BackendInitializationError('Audited runtime imports unavailable', resources_released=True) from exc
        self._resources = ExitStack()
        try:
            params = VDevice.create_params()
            params.scheduling_algorithm = HailoSchedulingAlgorithm.ROUND_ROBIN
            self.device = VDevice(params)
            self._resources.callback(self.device.release)
            self.model = VLM(self.device, str(path))
            self._resources.callback(self.model.release)
        except BaseException as exc:
            try:
                self._resources.close()
            except BaseException as release_error:
                raise BackendInitializationError('VLM initialization and release failed', resources_released=False) from release_error
            raise BackendInitializationError('VLM initialization failed; context released', resources_released=True) from exc
        self.cv2 = cv2
        self.info = {'model': download_receipt['model'], 'model_sha256': digest,
                     'artifact_version': '5.1.1', 'runtime_version': runtime_version,
                     'source_url': download_receipt['source_url'], 'semantic_only': True}

    def infer(self, frame, selection, prompt):
        import numpy as np
        image = self.cv2.imdecode(np.frombuffer(frame.jpeg, np.uint8), self.cv2.IMREAD_COLOR)
        if image is None or image.shape[:2] != (frame.height, frame.width):
            raise ValueError('Frozen JPEG dimensions must match original cache metadata')
        # A logged square crop contains the selected region; no metric pose is
        # calculated from either the resize or the generated text.
        box = selection.get('box')
        if box is not None:
            coordinates = np.asarray(box, float)
            if coordinates.shape != (4,) or not np.isfinite(coordinates).all() or np.any(coordinates < 0) or np.any(coordinates > 1):
                raise ValueError('Normalized source-image box required')
            x1, y1, x2, y2 = coordinates * [frame.width, frame.height, frame.width, frame.height]
            if x2 <= x1 or y2 <= y1:
                raise ValueError('Nondegenerate selected box required')
            center_x, center_y = (x1+x2)/2, (y1+y2)/2
            side = min(min(frame.width, frame.height), max(x2-x1, y2-y1)*1.2)
        else:
            center_x, center_y = selection['pixel']
            side = min(frame.width, frame.height)
        side = max(1, int(side))
        left = int(max(0, min(frame.width-side, center_x-side/2)))
        top = int(max(0, min(frame.height-side, center_y-side/2)))
        rgb = self.cv2.cvtColor(self.cv2.resize(image[top:top+side, left:left+side], (336, 336)),
                               self.cv2.COLOR_BGR2RGB)
        messages = [
            {'role': 'system', 'content': [{'type': 'text', 'text':
                'Describe the selected tabletop item, receptacle or scene and uncertainty. '
                'Do not provide coordinates, contact claims or robot instructions.'}]},
            {'role': 'user', 'content': [{'type': 'image'}, {'type': 'text', 'text': prompt}]},
        ]
        response = ''
        try:
            with self.model.generate(prompt=messages, frames=[rgb], temperature=.1, seed=42,
                                     max_generated_tokens=128) as generation:
                for chunk in generation:
                    if chunk != '<|im_end|>':
                        response += str(chunk)
        finally:
            self.model.clear_context()
        return {'semantic_text': response, 'crop_box_pixels': [left, top, left+side, top+side],
                'model_input_rgb_sha256': hashlib.sha256(rgb.tobytes()).hexdigest(),
                'metric_coordinates_produced': False, 'motor_commands_produced': False}

    def close(self):
        self._resources.close()


class EventSemanticWorker:
    """One active event and one coalesced pending event; no frame-rate VLM loop."""
    def __init__(self, observer, backend_factory, *, clock_domain, boot_id,
                 max_frame_age_ns=2_000_000_000, monotonic_ns=time.monotonic_ns, emit=None):
        self.observer, self.backend_factory = observer, backend_factory
        self.clock_domain, self.boot_id = clock_domain, boot_id
        self.max_age, self.clock, self.emit = max_frame_age_ns, monotonic_ns, emit
        self.condition = threading.Condition()
        self.pending, self.thread, self.stopping = None, None, False
        self.next_id, self.epoch = 0, 0
        self.state = {'schema': 'explorer_lab_semantic_worker_v1', 'project_id': '06',
                      'state': 'NOT_STARTED', 'physical_actions_emitted': False}
        self.receipts = []

    def start(self):
        with self.condition:
            if self.thread is not None or self.stopping:
                raise RuntimeError('Worker cannot be started twice or resumed after close')
            self.thread = threading.Thread(target=self._work, name='lab06-semantic-event', daemon=True)
            self.thread.start()

    def submit(self, frame, selection, prompt):
        if (not isinstance(frame, CachedFrame) or frame.clock_domain != self.clock_domain or
                frame.boot_id != self.boot_id or not 0 <= self.clock() - frame.monotonic_ns <= self.max_age or
                not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 4096 or
                any(selection.get(key) != frame.metadata()[key] for key in ('camera_id','frame_id','frame_sha256'))):
            raise ValueError('Fresh exact selected source frame and bounded semantic prompt required')
        with self.condition:
            if self.stopping or self.thread is None:
                raise RuntimeError('Worker is not active')
            self.next_id += 1
            self.epoch += 1
            if self.pending:
                self.receipts.append({'request_id': self.pending['request_id'], 'outcome': 'COALESCED_BEFORE_START'})
            self.pending = {'request_id': self.next_id, 'epoch': self.epoch, 'frame': frame,
                            'selection': deepcopy(selection), 'prompt': prompt,
                            'requested_monotonic_ns': self.clock()}
            self.state.update(state='QUEUED', request_id=self.next_id)
            self.condition.notify_all()
            return self.next_id

    def cancel(self):
        with self.condition:
            self.epoch += 1
            if self.pending:
                self.receipts.append({'request_id': self.pending['request_id'], 'outcome': 'CANCELLED_BEFORE_START'})
            self.pending = None
            self.state.update(state='CANCELLED', result=None)
            self.condition.notify_all()

    def snapshot(self):
        with self.condition:
            return {**deepcopy(self.state), 'pending_count': int(self.pending is not None),
                    'receipts': deepcopy(self.receipts[-100:])}

    def close(self):
        with self.condition:
            self.stopping = True
            self.epoch += 1
            self.pending = None
            self.state.update(state='CLOSING', result=None)
            self.condition.notify_all()
        if self.thread:
            self.thread.join(timeout=2.)

    def _work(self):
        while True:
            with self.condition:
                self.condition.wait_for(lambda: self.stopping or self.pending is not None)
                if self.stopping:
                    self.state['state'] = 'CLOSED'
                    return
                request, self.pending = self.pending, None
                self.state.update(state='RUNNING', request_id=request['request_id'])
            frame = request['frame']
            receipt = {'request_id': request['request_id'], 'source_frame': frame.metadata(),
                       'selection': deepcopy(request['selection']), 'prompt': request['prompt'],
                       'prompt_sha256': hashlib.sha256(request['prompt'].encode()).hexdigest(),
                       'requested_monotonic_ns': request['requested_monotonic_ns'],
                       'started_monotonic_ns': self.clock(), 'clock_domain': self.clock_domain,
                       'boot_id': self.boot_id, 'physical_actions_emitted': False}
            backend = None
            try:
                if not 0 <= self.clock() - frame.monotonic_ns <= self.max_age:
                    raise ValueError('Queued frame became stale before inference')
                with self.observer.semantic_slot() as slot:
                    slot['backend_created'] = True
                    try:
                        try:
                            with self.condition:
                                if self.stopping or request['epoch'] != self.epoch:
                                    raise BackendInitializationError('Request cancelled before backend creation', resources_released=True)
                            backend = self.backend_factory()
                        except BackendInitializationError as exc:
                            slot['backend_released'] = exc.resources_released
                            raise
                        receipt['model'] = deepcopy(backend.info)
                        receipt['result'] = backend.infer(frame, request['selection'], request['prompt'])
                    finally:
                        if backend is not None:
                            backend.close()
                            slot['backend_released'] = True
                receipt['outcome'] = 'SEMANTIC_RESULT'
            except Exception as exc:
                receipt.update(outcome='FAILED', error=type(exc).__name__)
            receipt['finished_monotonic_ns'] = self.clock()
            with self.condition:
                active = not self.stopping and request['epoch'] == self.epoch
                if not active:
                    receipt['outcome'] = 'LATE_OR_CANCELLED_RESULT_DISCARDED'
                    receipt.pop('result', None)
                self.receipts.append(receipt)
                if active:
                    self.state.update(state=receipt['outcome'], result=deepcopy(receipt))
            if self.emit:
                try:
                    self.emit(deepcopy(receipt))
                except Exception:
                    with self.condition:
                        self.state['delivery_error'] = True
