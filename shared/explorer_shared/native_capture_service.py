"""ENGINEERING_GLUE: Node-local GET-only native-source fetch plus shared02/03 capture lifecycle."""
from copy import deepcopy
import json
import threading
import time

from .native_capture import NativeCaptureWriter
from .native_stream03_client import OriginalNativeStreamReader


class NativeCaptureService:
    def __init__(self,writer,client,*,clock=time.monotonic_ns,poll_s=.05):
        self.writer,self.client,self.clock,self.poll_s=writer,client,clock,poll_s
        self.lock=threading.RLock();self.wake=threading.Event();self.closed=threading.Event()
        self.active_id=None;self.cursor=0;self.inflight=False;self.finish_pending=None
        self.failure_count=0
        self.last_error=None;self.native_ready=False;self.fetch_enabled=False
        self.thread=threading.Thread(target=self._work,name='native-capture-source-fetch',daemon=True)
        self.thread.start()

    @classmethod
    def from_agent(cls,agent):
        boot=agent.delivery.clock_domain.rsplit(':',1)[-1]
        writer=NativeCaptureWriter(agent.state_dir/'native-captures',agent.sources.get('nano-rgbd'),
            clock_domain=agent.delivery.clock_domain,boot_id=boot,emit=agent.delivery.emit)
        try:
            client=OriginalNativeStreamReader(agent.legacy_bridge.root)
        except (OSError,ValueError):
            client=None
        return cls(writer,client)

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
                'physical_qualification':False}

    def status(self):
        with self.lock:
            return {'schema':'explorer_native_capture_service_v1','native_owner_ready':self.native_ready,
                    'capture':self.writer.status(),'active_recording_id':self.active_id,
                    'fetch_inflight':self.inflight,'finish_pending':self.finish_pending is not None,
                    'source_error':self.last_error,'ready_recordings':self.writer.ready_recordings(),
                    'legacy_ready_recording_ids_modified':False}

    def start(self,skill_context):
        if not isinstance(skill_context,dict) or skill_context.get('project_id') not in ('02','03'):
            raise ValueError('Validated source project02/03 skill_context required')
        with self.lock:
            if self.active_id or self.inflight or self.finish_pending:
                raise RuntimeError('Finish current native capture first')
        if not self.capabilities()['native_owner_ready']:
            raise RuntimeError('NATIVE_HOST_DISPATCH_TAP_NOT_MOUNTED')
        # GET only, through the original guarded reference reader; no prepare,
        # HOME, readback, resume, movement or recording-native kwargs are called.
        self.client.reference()
        packet=self.client.dispatch_source_packet(0)
        with self.lock:
            result=self.writer.start(deepcopy(skill_context),packet['source_bytes'])
            self.active_id=result['recording_id'];self.cursor=result['cursor'];self.fetch_enabled=True
            self.failure_count=packet['payload'].get('capture_failures',0)
            self.finish_pending=None;self.last_error=None
        self.wake.set()
        return {**result,'capability_schema':'explorer_native_capture_service_v1'}

    def stage(self,recording_id,phase_id,*,reason='explicit_operator_annotation',recovery_of=None):
        with self.lock:
            if recording_id!=self.active_id or self.finish_pending is not None:
                raise ValueError('Recording changed or finish pending')
            return self.writer.stage(recording_id,phase_id,reason=reason,recovery_of=recovery_of)

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
