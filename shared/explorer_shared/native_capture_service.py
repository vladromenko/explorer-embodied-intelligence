"""ENGINEERING_GLUE: Node-local GET-only native-source fetch plus shared02/03 capture lifecycle."""
from copy import deepcopy
import json
import threading
import time

from .native_capture import NativeCaptureWriter
from .native_stream03_client import OriginalNativeStreamReader


from .native_capture_identity import normalize_capture_context


class NativeCaptureService:
    def __init__(self,writer,client,*,clock=time.monotonic_ns,poll_s=.05,observation_reader=None):
        self.writer,self.client,self.clock,self.poll_s=writer,client,clock,poll_s
        self.lock=threading.RLock();self.wake=threading.Event();self.closed=threading.Event()
        self.active_id=None;self.cursor=0;self.inflight=False;self.finish_pending=None
        self.failure_count=0
        self.observation_reader=observation_reader
        self.last_observation_frame=None;self.observation_error=None
        self.last_error=None;self.native_ready=False;self.fetch_enabled=False
        self.thread=threading.Thread(target=self._work,name='native-capture-source-fetch',daemon=True)
        self.thread.start()

    @classmethod
    def from_agent(cls,agent):
        boot=agent.delivery.clock_domain.rsplit(':',1)[-1]
        from .native_sensor_cache import NativeSensorCache
        agent.sensor_cache=NativeSensorCache(agent.state_dir/'nano-camera-cache'/'sensors',
            clock_domain=agent.delivery.clock_domain,boot_id=boot,camera_root=agent.legacy_bridge.root/'data')
        writer=NativeCaptureWriter(agent.state_dir/'native-captures',agent.sources.get('nano-rgbd'),
            clock_domain=agent.delivery.clock_domain,boot_id=boot,emit=agent.delivery.emit,
            observation_provider=agent.sensor_cache.observation_provider)
        try:
            client=OriginalNativeStreamReader(agent.legacy_bridge.root)
        except (OSError,ValueError):
            client=None
        return cls(writer,client,observation_reader=agent.sensor_cache.observation_at)

    def capabilities(self):
        try:
            if self.client is None:
                raise RuntimeError('Original owner source not configured')
            native=self.client.status()
            self.native_ready=native.get('schema')=='explorer_native_retarget_mux_v1'
            self.last_error=None if self.native_ready else 'NATIVE_HOST_DISPATCH_TAP_NOT_MOUNTED'
        except (OSError,ValueError,RuntimeError) as error:
            self.native_ready=False;self.last_error=type(error).__name__
        return {'schema':'explorer_native_capture_service_v1','metadata_skill_context_supported':True,
                'native_owner_ready':self.native_ready,'native_source_error':self.last_error,
                'projects':['02','03'],'action_dimension':9,'same_target_producer':True,
                'native_source_method':'GET_ONLY','controller_settings_written':False,
                'recording_operations':['start','pause','resume','cancel','finish','review'],
                'recording_modes':['demonstration','observations_only'],
                'physical_qualification':False}

    def status(self):
        with self.lock:
            return {'schema':'explorer_native_capture_service_v1','native_owner_ready':self.native_ready,
                    'capture':self.writer.status(),'active_recording_id':self.active_id,
                    'fetch_inflight':self.inflight,'finish_pending':self.finish_pending is not None,
                    'source_error':self.last_error,'ready_recordings':self.writer.ready_recordings(),
                    'observation_error':self.observation_error,
                    'legacy_ready_recording_ids_modified':False}

    def start(self,skill_context,*,observation_only=False):
        context=normalize_capture_context(skill_context,observation_only=observation_only)
        with self.lock:
            if self.active_id or self.inflight or self.finish_pending:
                raise RuntimeError('Finish current native capture first')
        if not self.capabilities()['native_owner_ready']:
            raise RuntimeError('NATIVE_HOST_DISPATCH_TAP_NOT_MOUNTED')
        # GET only, through the original guarded reference reader; no prepare,
        # HOME, readback, resume, movement or recording-native kwargs are called.
        if not observation_only:self.client.reference()
        packet=self.client.dispatch_source_packet(0)
        with self.lock:
            result=self.writer.start(context,packet['source_bytes'])
            self.active_id=result['recording_id'];self.cursor=result['cursor'];self.fetch_enabled=True
            self.failure_count=packet['payload'].get('capture_failures',0)
            self.finish_pending=None;self.last_error=None
            self.last_observation_frame=None;self.observation_error=None
        self.wake.set()
        return {**result,'capability_schema':'explorer_native_capture_service_v1'}

    def stage(self,recording_id,phase_id,*,reason='explicit_operator_annotation',recovery_of=None):
        with self.lock:
            if recording_id!=self.active_id or self.finish_pending is not None:
                raise ValueError('Recording changed or finish pending')
            return self.writer.stage(recording_id,phase_id,reason=reason,recovery_of=recovery_of)

    def pause(self,recording_id):
        with self.lock:
            if recording_id!=self.active_id or self.finish_pending is not None:raise ValueError('Recording changed or finish pending')
            return self.writer.pause(recording_id)

    def resume(self,recording_id):
        with self.lock:
            if recording_id!=self.active_id or self.finish_pending is not None:raise ValueError('Recording changed or finish pending')
            return self.writer.resume(recording_id)

    def cancel(self,recording_id,actor_id):
        if not isinstance(actor_id,str) or not actor_id:raise ValueError('Authenticated operator required')
        return self.finish(recording_id,{'recording_id':recording_id,'operator_outcome':'rejected',
            'recording_cancelled':True,'actor_id':actor_id,'outcomes':{}})

    def review(self,recording_id,decision,actor_id,expected_archive_sha256):
        return self.writer.review(recording_id,decision,actor_id,expected_archive_sha256)

    def observe_operator_input(self,request,response,http_status,started_ns,declared_metadata=None):
        """Passive bounded source journal; its failure never changes native control."""
        identity=self.active_id
        if identity is None:return
        try:
            self.writer.submit_operator_event(identity,{'schema':'explorer_operator_input_receipt_v1',
                'recording_id':identity,'clock_domain':self.writer.domain,'boot_id':self.writer.boot,
                'request_started_monotonic_ns':started_ns,'response_returned_monotonic_ns':self.clock(),
                'operator_input':deepcopy(request),'requested_action':deepcopy(request),
                'native_response':deepcopy(response),'http_status':http_status,
                'accepted_action':None,'accepted_action_basis':'raw_native_response_retained_no_action_acceptance_inferred',
                'dispatched_action_basis':'separate_native_host_publication_receipts_not_this_http_response',
                'client_declared_metadata':deepcopy(declared_metadata),
                'physical_attainment_inferred':False})
        except Exception:
            self.writer.operator_evidence_gap=True

    def finish(self,recording_id,outcome_evidence=None):
        if outcome_evidence is not None and not isinstance(outcome_evidence,dict):
            raise ValueError('Exact source outcome object required')
        with self.lock:
            if recording_id!=self.active_id or self.finish_pending is not None:
                raise ValueError('Recording changed or finish already pending')
            self.fetch_enabled=False
            self.writer.finish_boundary(recording_id,self.clock())
            self.finish_pending={'recording_id':recording_id,
                                 'outcome':json.dumps(outcome_evidence or {},sort_keys=True,separators=(',',':')).encode()}
            if not self.inflight:
                self._finish_locked()
        self.wake.set();return self.status()

    def _finish_locked(self):
        pending=self.finish_pending
        self.writer.finish(pending['recording_id'],pending['outcome'])
        self.active_id=None;self.finish_pending=None

    def close(self):
        self.closed.set();self.wake.set();self.thread.join(timeout=2)
        self.writer.close()

    def _work(self):
        while not self.closed.is_set():
            self.wake.wait(self.poll_s);self.wake.clear()
            with self.lock:
                if self.fetch_enabled and self.active_id is not None:
                    ident,cursor=self.active_id,self.cursor;self.inflight=True
                else:
                    ident=None
            if ident is not None:
                try:
                    packet=self.client.dispatch_source_packet(cursor)
                    page=packet['payload']
                    cutoff=self.clock();frame=self.writer.camera.latest() if self.writer.camera is not None else None
                    if frame is not None and frame.frame_id!=self.last_observation_frame:
                        try:
                            observation=self.observation_reader(cutoff,frame) if self.observation_reader else {}
                            self.observation_error=None
                        except Exception as error:
                            self.observation_error=type(error).__name__
                            observation={'diagnostics':{'sensor_snapshot_error':self.observation_error,'no_sensor_values_inferred':True}}
                        if self.writer.submit_observation(ident,frame,observation,cutoff):self.last_observation_frame=frame.frame_id
                    if page.get('receipts') or page.get('gap') or page.get('capture_failures',0)>self.failure_count:
                        if self.writer.submit(ident,packet['source_bytes']):
                            with self.lock:
                                if self.active_id==ident:
                                    self.cursor=page['next_sequence']
                                    self.failure_count=page.get('capture_failures',0)
                    self.last_error=None
                except (OSError,ValueError,RuntimeError) as error:
                    self.last_error=type(error).__name__
                    with self.writer.lock:
                        self.writer.blockers.add('NATIVE_SOURCE_FETCH_FAILED')
                finally:
                    with self.lock:
                        self.inflight=False
                        if self.finish_pending is not None:
                            self._finish_locked()
