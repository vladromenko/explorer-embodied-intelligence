"""ENGINEERING_GLUE: non-actuating SO101 source learning cached vision and semantic API.

The existing observer owns camera and accelerator. This service freezes exact
cached frame/mask pairs, validates measured calibration and requests semantic
events. It contains no actuator, serial, publisher or robot executor interface.
"""
from .experiments import experiment_id,experiment_identity
from collections import OrderedDict
from contextlib import contextmanager,nullcontext
from copy import deepcopy
import json
from pathlib import Path
import threading
import time
import uuid

from .tabletop_grounding import CachedFrame,TableCalibration,select_target,ground_selection
from .lab_semantics import EventSemanticWorker,HailoVLMBackend


class SourceSemanticAdmission:
    """Serialize only source04 operation admission; never issues a command.

    The same original Station still owns its locks, publication and file writer.
    VLM starts only while idle; native commands cannot enter until its actual
    backend returns/releases, including after a cancelled result.
    """
    def __init__(self,station,vision):
        self.station,self.vision=station,vision;self.lock=threading.RLock()

    def _busy(self):
        worker=self.vision.worker
        if worker is None:return False
        check=getattr(worker,'busy',None)
        return check() if callable(check) else True

    @contextmanager
    def semantic(self):
        with self.lock:
            native=self.station.status()
            if native.get('mode')!='idle' or native.get('recording') is not None:
                raise RuntimeError('Finish recording and explicitly stop source control before semantic inference')
            if self._busy():raise RuntimeError('Previous semantic backend is still active or its release is unconfirmed')
            yield

    @contextmanager
    def control(self,operation):
        if operation=='stop':
            yield
        else:
            with self.lock:
                if self._busy():raise RuntimeError('Semantic inference is active or release is unconfirmed; request fresh observation after it finishes')
                yield

    def status(self):
        # This read grants nothing and must not invert Station→admission locks
        # in existing caller-owned status snapshots. Mutations recheck above.
        native=self.station.status();busy=self._busy()
        return dict(schema='explorer_source04_semantic_admission_v1',native_commands_blocked=busy,
            semantic_requires_idle=True,semantic_admission_allowed=not busy and native.get('mode')=='idle' and native.get('recording') is None,
            native_mode=native.get('mode'),physical_actions_emitted=False,
            reason='SEMANTIC_INFERENCE_OR_RELEASE_PENDING' if busy else None)


class LabVisionService:
    def __init__(self,source,observer,*,clock_domain,boot_id,config_path,emit=None,
                 backend_factory=None,clock=time.monotonic_ns,calibration_dir=None,calibration_context_provider=None):
        self.source,self.observer=source,observer
        self.clock_domain,self.boot_id,self.clock=clock_domain,boot_id,clock
        self.config_path=Path(config_path);self.emit=emit or (lambda event:None)
        self.lock=threading.RLock();self.selections=OrderedDict();self.worker=None
        self.frozen_pairs=OrderedDict();self.selection_frames={};self.calibration_frames=OrderedDict()
        self.semantic_admission=lambda:nullcontext()
        self.runtime_admission_status=lambda:{}
        self.calibration_wizard=None;self.calibration_wizard_error=None;self.accepted_calibration_sha=None
        self.calibration=None;self.config={};self.configuration_error=None
        self.model_staged=False;self.backend_factory=backend_factory
        try:
            if self.config_path.is_file():
                if self.config_path.stat().st_size>65536:raise ValueError('Vision configuration exceeds metadata limit')
                self.config=json.loads(self.config_path.read_text())
                if not isinstance(self.config,dict):raise ValueError('Vision configuration must be an object')
                if self.config.get('calibration_path'):self._load_calibration(Path(self.config['calibration_path']))
                if self.config.get('vlm_model_path') and self.config.get('vlm_receipt_path'):
                    receipt_path=Path(self.config['vlm_receipt_path'])
                    if receipt_path.stat().st_size>65536:raise ValueError('Model receipt exceeds metadata limit')
                    receipt=json.loads(receipt_path.read_text());model_path=Path(self.config['vlm_model_path'])
                    self.model_staged=model_path.is_file() and model_path.stat().st_size==receipt.get('actual_bytes')
                    if self.model_staged and backend_factory is None:
                        def backend():
                            import hailo_platform
                            return HailoVLMBackend(model_path,receipt,runtime_version=hailo_platform.__version__)
                        self.backend_factory=backend
        except (OSError,ValueError,KeyError,TypeError) as error:
            self.configuration_error=str(error)
        if observer is not None and self.backend_factory is not None:
            self.worker=EventSemanticWorker(observer,self.backend_factory,clock_domain=clock_domain,
                boot_id=boot_id,max_frame_age_ns=120_000_000_000,monotonic_ns=clock,
                emit=lambda event:self.emit(dict(event,kind='so101_source.semantic_result',**experiment_identity('so101_source_learning'))))

        if calibration_dir is not None:
            from .tabletop_calibration_service import TabletopCalibrationService
            try:
                self.calibration_wizard=TabletopCalibrationService(calibration_dir,self._calibration_frame,
                    profile_provider=self._calibration_profile,
                    context_provider=calibration_context_provider or (lambda:self.config.get('calibration_context',{})),
                    clock_domain=clock_domain,boot_id=boot_id,clock=clock,
                    current_calibration_sha_provider=lambda:self.calibration.sha256 if self.calibration else None,
                    emit=lambda event:self.emit(dict(event,**experiment_identity('so101_source_learning'))))
                self._sync_calibration()
            except (OSError,ValueError,KeyError,TypeError) as error:self.calibration_wizard_error=str(error)

    def _calibration_profile(self):
        # Existing camera configuration, excluding changing frame/counter/availability.
        profile=dict(camera_id=self.source.camera_id,source_class=type(self.source).__module__+'.'+type(self.source).__name__)
        for name in ('device','width','height','fps'):
            if hasattr(self.source,name):profile[name]=str(getattr(self.source,name))
        return profile

    def _calibration_frame(self,identity=None):
        with self.lock:pinned=self.calibration_frames.get(identity)
        if pinned is not None:return pinned
        lookup=getattr(self.source,'frame_by_id',None)
        original=lookup(identity) if identity is not None and lookup else self.source.latest()
        if original is None or (identity is not None and original.frame_id!=identity):raise ValueError('Exact existing camera JPEG is missing or evicted')
        frame=self._frame(original)
        with self.lock:
            self.calibration_frames[frame.frame_id]=frame
            while len(self.calibration_frames)>16 or sum(len(item.jpeg) for item in self.calibration_frames.values())>16*1024**2:
                self.calibration_frames.popitem(last=False)
        return frame

    def _sync_calibration(self):
        if self.calibration_wizard is None:return
        with self.lock:
            accepted=self.calibration_wizard.accepted()
            if accepted and accepted['artifact_sha256']!=self.accepted_calibration_sha:
                # The original optional config and calibration files are never rewritten.
                self._load_calibration(Path(accepted['path']));self.accepted_calibration_sha=accepted['artifact_sha256']
            self.calibration_wizard_error=None

    def calibration_status(self,session_id=None):
        if self.calibration_wizard is None:raise ValueError('Durable calibration wizard directory is not configured')
        self._sync_calibration();return self.calibration_wizard.status(session_id)

    def calibration_board(self,format):
        if self.calibration_wizard is None:raise ValueError('Durable calibration wizard directory is not configured')
        return self.calibration_wizard.board(format)

    def calibration_media(self,session_id,capture_id):
        if self.calibration_wizard is None:raise ValueError('Durable calibration wizard directory is not configured')
        return self.calibration_wizard.media(session_id,capture_id)

    def calibration_preview(self,frame_id):
        if self.calibration_wizard is None:raise ValueError('Durable calibration wizard directory is not configured')
        frame=self.calibration_wizard._frame(frame_id)
        return frame.jpeg,frame.metadata()

    def calibration_reference(self):
        self._sync_calibration()
        if self.calibration is None:return dict(available=False,reason='measured_calibration_not_available')
        frame=self._calibration_frame()
        self.calibration.check_frame(frame,now_ns=self.clock(),clock_domain=self.clock_domain,boot_id=self.boot_id,max_age_ns=2_000_000_000)
        return dict(available=True,calibration_id=self.calibration.body['calibration_id'],calibration_sha256=self.calibration.sha256,
            provenance=self.calibration.body['provenance'],acceptance=self.calibration_wizard.accepted() if self.calibration_wizard else None,
            source_frame=frame.metadata(),physical_execution_allowed=False)

    def _load_calibration(self,path):
        if path.stat().st_size>2*1024**2:raise ValueError('Calibration metadata is too large')
        raw=path.read_bytes();body=json.loads(raw);payloads={};size=0
        for name in (body.get('source_files_sha256') or {}):
            if not isinstance(name,str) or Path(name).is_absolute() or '..' in Path(name).parts:raise ValueError('Calibration source must remain relative')
            member=(path.parent/name).resolve()
            if not member.is_relative_to(path.parent.resolve()):raise ValueError('Calibration source escaped managed folder')
            size+=member.stat().st_size
            if size>32*1024**2:raise ValueError('Calibration source bytes exceed bound')
            payloads[name]=member.read_bytes()
        calibrated=TableCalibration(raw,payloads)
        if calibrated.body['provenance']!='measured':raise ValueError('Synthetic calibration cannot qualify a physical Pi camera')
        self.calibration=calibrated

    def start(self):
        if self.worker:self.worker.start()
        if self.calibration_wizard:self.calibration_wizard.start()

    def close(self):
        if self.worker:self.worker.close()
        if self.calibration_wizard:self.calibration_wizard.close()

    def _frame(self,original):
        return CachedFrame(self.source.camera_id,original.frame_id,original.jpeg,original.width,original.height,
            self.clock_domain,self.boot_id,original.monotonic_ns,original.timestamp_basis)

    def frozen(self,frame_id=None):
        """Never pair the latest JPEG with an older inference result."""
        if self.observer is None:raise ValueError('Vision observer is not configured')
        if frame_id is None:frame_id=self.observer.snapshot().get('frame_id')
        with self.lock:
            pinned=self.frozen_pairs.get(frame_id)
            if pinned:return pinned[0],deepcopy(pinned[1])
        pair=self.observer.cached_observation_pair(frame_id)
        if pair is None:raise ValueError('Exact processed frame/mask pair is missing or evicted; freeze a new frame')
        original,observation=pair
        frame=self._frame(original)
        if observation.get('frame_sha256')!=frame.sha256:raise ValueError('Cached vision and JPEG source SHA differ')
        with self.lock:
            self.frozen_pairs[frame.frame_id]=(frame,deepcopy(observation))
            while len(self.frozen_pairs)>8 or sum(len(pair[0].jpeg)+len(json.dumps(pair[1])) for pair in self.frozen_pairs.values())>32*1024**2:
                self.frozen_pairs.popitem(last=False)
        return frame,observation

    def snapshot(self):
        wizard=None
        if self.calibration_wizard:
            try:wizard=self.calibration_status()
            except (OSError,ValueError,KeyError,TypeError) as error:self.calibration_wizard_error=str(error)
        cv=self.observer.snapshot() if self.observer else {'available':False,'state':'not_configured'}
        semantic=self.worker.snapshot() if self.worker else {'state':'NOT_CONFIGURED','physical_actions_emitted':False}
        calibration=self.calibration.body if self.calibration else None
        valid=False;reason='Measured tabletop/camera/base registration is not available'
        if calibration:
            try:
                current=self.source.latest()
                if current is None:raise ValueError('Current camera frame is unavailable')
                self.calibration.check_frame(self._frame(current),now_ns=self.clock(),clock_domain=self.clock_domain,
                    boot_id=self.boot_id,max_age_ns=2_000_000_000)
                valid=True;reason=None
            except ValueError as error:reason=str(error)
        with self.lock:selections=list(deepcopy(self.selections).values())
        return dict(schema='explorer_so101_vision_status_v2',**experiment_identity('so101_source_learning'),camera=self.source.metadata(),
            perception=cv,segmentation_available=cv.get('available') is True and cv.get('model',{}).get('task')=='instance_segmentation',
            vlm=dict(model_staged=self.model_staged,worker=semantic,
                inference_qualified=any(row.get('outcome')=='SEMANTIC_RESULT' for row in semantic.get('receipts',[]))),
            calibration=dict(available=calibration is not None,valid=valid,calibration_id=calibration.get('calibration_id') if calibration else None,
                provenance='measured' if calibration else None,coordinate_confirmed=False,
                reason=reason),
            selections=selections,configuration_error=self.configuration_error,runtime_admission=self.runtime_admission_status(),
            calibration_wizard=wizard,calibration_wizard_error=self.calibration_wizard_error,
            native_execution_enabled=False,physical_validation_passed=False,
            state_separation='semantic text, metric geometry, IK preview and physical execution are separate facts')

    def grid(self,cell_size_m=.03):
        self._sync_calibration()
        if self.calibration is None:raise ValueError('Measured calibration required for a metric grid')
        current=self.source.latest()
        if current is None:raise ValueError('Current camera required for a metric grid')
        self.calibration.check_frame(self._frame(current),now_ns=self.clock(),clock_domain=self.clock_domain,
            boot_id=self.boot_id,max_age_ns=2_000_000_000)
        if type(cell_size_m) not in (int,float) or not .005<=cell_size_m<=.25:raise ValueError('Metric cell size must be between 0.005 and 0.25m')
        return self.calibration.metric_grid(cell_size_m)

    def select(self,body):
        from .hailo_segmentation import decode_mask_rle
        self._sync_calibration()
        frame,observation=self.frozen(body.get('frame_id'))
        if body.get('frame_sha256')!=frame.sha256:raise ValueError('Click must identify the exact displayed JPEG SHA')
        if observation.get('model',{}).get('task')!='instance_segmentation':raise ValueError('Actual instance segmentation masks are not available')
        values=deepcopy(observation)
        for detection in values.get('detections',[]):
            if not detection.get('mask_available'):raise ValueError('An instance mask is unavailable; selection remains ambiguous')
            detection['mask']=decode_mask_rle(detection.get('mask_rle'))
        selected=select_target(frame,values,body.get('pixel'),purpose=body.get('purpose','object'),track_id=body.get('track_id'))
        selected.update(selection_id=uuid.uuid4().hex,selected_monotonic_ns=self.clock(),coordinate_confirmed=False)
        if self.calibration:
            try:
                selected['grounding']=ground_selection(self.calibration,frame,selected,now_ns=self.clock(),
                    clock_domain=self.clock_domain,boot_id=self.boot_id,max_age_ns=2_000_000_000)
                selected['coordinate_confirmed']=True
            except ValueError as error:selected['grounding_blocker']=str(error)
        else:selected['grounding_blocker']='Measured tabletop calibration is unavailable; no metric coordinate inferred'
        with self.lock:
            self.selections[selected['selection_id']]=selected
            self.selection_frames[selected['selection_id']]=frame
            while len(self.selections)>32 or sum(len(value.jpeg) for value in self.selection_frames.values())>32*1024**2:
                removed,_=self.selections.popitem(last=False);self.selection_frames.pop(removed,None)
        self.emit(dict(kind='so101_source.selection',selection=deepcopy(selected),**experiment_identity('so101_source_learning')))
        return deepcopy(selected)

    def semantic(self,body):
        with self.semantic_admission():return self._semantic(body)

    def _semantic(self,body):
        if self.worker is None:raise ValueError('A compatible staged Hailo VLM is not configured')
        trigger=body.get('trigger','user_selection')
        if trigger not in ('user_selection','before_attempt','after_attempt','ambiguity','lost_track'):
            raise ValueError('A named explicit semantic event is required')
        identity=body.get('selection_id')
        if identity:
            with self.lock:selected=deepcopy(self.selections.get(identity));frame=self.selection_frames.get(identity)
            if not selected:raise ValueError('Unknown source-bound selection')
            if frame is None:raise ValueError('Selected original JPEG was evicted')
        else:
            original=self.source.latest()
            if original is None:raise ValueError('Fresh cached Pi camera frame required')
            frame=self._frame(original)
            selected=dict(frame.metadata(),pixel=[frame.width/2,frame.height/2],purpose='scene_semantics',box=None)
        selected['semantic_trigger']=trigger
        request_id=self.worker.submit(frame,selected,body.get('prompt','Describe this tabletop scene and uncertainty. Do not give coordinates or robot commands.'))
        return dict(request_id=request_id,state='QUEUED',source_frame=frame.metadata(),physical_actions_emitted=False)

    def command(self,operation,body):
        if not isinstance(body,dict):raise ValueError('Vision request must be an object')
        if operation.startswith('calibration/'):
            if self.calibration_wizard is None:raise ValueError('Durable calibration wizard directory is not configured')
            if operation=='calibration/status':
                if set(body)-{'session_id'}:raise ValueError('Calibration status takes only a session ID')
                return self.calibration_status(body.get('session_id'))
            actions={'calibration/capture':self.calibration_wizard.capture,'calibration/solve':self.calibration_wizard.solve,'calibration/accept':self.calibration_wizard.accept}
            if operation not in actions:raise ValueError('Unknown calibration operation')
            result=actions[operation](body);self._sync_calibration();return result
        if operation=='select':return self.select(body)
        if operation=='grid':return self.grid(body.get('cell_size_m',.03))
        if operation=='semantic':return self.semantic(body)
        if operation=='semantic/cancel':
            if self.worker:self.worker.cancel()
            return self.snapshot()
        raise ValueError('Vision API does not expose robot execution')
