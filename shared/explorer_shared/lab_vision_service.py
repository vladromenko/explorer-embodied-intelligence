"""ENGINEERING_GLUE: non-actuating Experiment06 cached vision and semantic API.

The existing observer owns camera and accelerator. This service freezes exact
cached frame/mask pairs, validates measured calibration and requests semantic
events. It contains no actuator, serial, publisher or robot executor interface.
"""
from collections import OrderedDict
from copy import deepcopy
import json
from pathlib import Path
import threading
import time
import uuid

from .tabletop_grounding import CachedFrame,TableCalibration,select_target,ground_selection
from .lab_semantics import EventSemanticWorker,HailoVLMBackend


class LabVisionService:
    def __init__(self,source,observer,*,clock_domain,boot_id,config_path,emit=None,
                 backend_factory=None,clock=time.monotonic_ns):
        self.source,self.observer=source,observer
        self.clock_domain,self.boot_id,self.clock=clock_domain,boot_id,clock
        self.config_path=Path(config_path);self.emit=emit or (lambda event:None)
        self.lock=threading.RLock();self.selections=OrderedDict();self.worker=None
        self.frozen_pairs=OrderedDict();self.selection_frames={}
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
                emit=lambda event:self.emit(dict(event,kind='lab06.semantic_result')))

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

    def close(self):
        if self.worker:self.worker.close()

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
        return dict(schema='explorer_lab06_vision_status_v1',project_id='06',camera=self.source.metadata(),
            perception=cv,segmentation_available=cv.get('available') is True and cv.get('model',{}).get('task')=='instance_segmentation',
            vlm=dict(model_staged=self.model_staged,worker=semantic,
                inference_qualified=any(row.get('outcome')=='SEMANTIC_RESULT' for row in semantic.get('receipts',[]))),
            calibration=dict(available=calibration is not None,valid=valid,calibration_id=calibration.get('calibration_id') if calibration else None,
                provenance='measured' if calibration else None,coordinate_confirmed=False,
                reason=reason),
            selections=selections,configuration_error=self.configuration_error,
            native_execution_enabled=False,physical_validation_passed=False,
            state_separation='semantic text, metric geometry, IK preview and physical execution are separate facts')

    def grid(self,cell_size_m=.03):
        if self.calibration is None:raise ValueError('Measured calibration required for a metric grid')
        current=self.source.latest()
        if current is None:raise ValueError('Current camera required for a metric grid')
        self.calibration.check_frame(self._frame(current),now_ns=self.clock(),clock_domain=self.clock_domain,
            boot_id=self.boot_id,max_age_ns=2_000_000_000)
        if type(cell_size_m) not in (int,float) or not .005<=cell_size_m<=.25:raise ValueError('Metric cell size must be between 0.005 and 0.25m')
        return self.calibration.metric_grid(cell_size_m)

    def select(self,body):
        from .hailo_segmentation import decode_mask_rle
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
        self.emit(dict(kind='lab06.selection',selection=deepcopy(selected)))
        return deepcopy(selected)

    def semantic(self,body):
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
        if operation=='select':return self.select(body)
        if operation=='grid':return self.grid(body.get('cell_size_m',.03))
        if operation=='semantic':return self.semantic(body)
        if operation=='semantic/cancel':
            if self.worker:self.worker.cancel()
            return self.snapshot()
        raise ValueError('Vision API does not expose robot execution')
