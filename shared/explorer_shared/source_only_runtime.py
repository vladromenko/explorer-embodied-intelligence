"""ENGINEERING_GLUE: Explicit04 runtime boundary; no peer config/client/leader worker is created."""
import uuid
import threading

from .pi_native_retarget import LegacyRawView


class SourceColdStartGuard:
    """Reject original bus writes until a validated current-user start enters.

    Original idle worker exceptions can attempt a measured hold during startup.
    Guard the SAME bus objects before that worker starts; never change targets,
    calibration, register values or the frozen Station. After observed admission
    the existing controller's holds/publication remain unchanged.
    """
    def __init__(self):
        self.lock=threading.RLock();self.unlocked=False;self.blocked_writes=0
        self.forwarded_writes=0;self.first_observed_grant=None;self.buses=None

    def install(self,station):
        if self.buses is not None:raise ValueError('Startup bus admission already has its original owner')
        self.buses=_ColdBusMap(self)
        for role,bus in getattr(station,'buses',{}).items():self.buses[role]=bus
        station.buses=self.buses

    def validated_start(self,record,operation):
        if operation not in ('teleop/start','policy/start'):raise ValueError('Only validated original start may unlock cold publication')
        data=record.get('data',{})
        if (not hasattr(record.get('revoked'),'is_set') or record['revoked'].is_set() or
                data.get('execution_permission') is not True or data.get('native_mode')!='idle' or
                not data.get('grant_id') or not data.get('actor_id') or not data.get('control_epoch')):
            raise ValueError('Current original COMMON record is required before cold publication')
        with self.lock:
            self.unlocked=True
            if self.first_observed_grant is None:self.first_observed_grant=data['grant_id']

    def write(self,callback,*args,**kwargs):
        with self.lock:
            if not self.unlocked:
                self.blocked_writes+=1
                raise RuntimeError('Source startup is disarmed until a validated observed start; no motor write dispatched')
            self.forwarded_writes+=1
            return callback(*args,**kwargs)

    def status(self):
        with self.lock:
            return dict(schema='explorer_source04_cold_publication_admission_v1',
                disarmed_until_observed_start=not self.unlocked,blocked_write_attempts=self.blocked_writes,
                forwarded_write_calls=self.forwarded_writes,guarded_bus_roles=sorted(self.buses or {}),
                original_bus_owners_preserved=True,controller_configuration_written=False)


class _ColdBus:
    def __init__(self,bus,guard):self._bus,self._guard=bus,guard
    def __getattr__(self,name):return getattr(self._bus,name)
    def sync_write(self,*args,**kwargs):return self._guard.write(self._bus.sync_write,*args,**kwargs)


class _ColdBusMap(dict):
    def __init__(self,guard):super().__init__();self.guard=guard
    def __setitem__(self,role,bus):
        if isinstance(bus,_ColdBus):raise ValueError('Another startup proxy cannot replace the original bus owner')
        super().__setitem__(role,_ColdBus(bus,self.guard))


class SourceOnlyRetarget:
    def __init__(self, state_dir):
        from pathlib import Path
        self.epoch = uuid.uuid4().hex
        self.recorder = LegacyRawView(Path(state_dir) / 'retarget-recordings')

    def status(self):
        return {'schema': 'explorer_disabled_direct03_runtime_v1',
                'state': 'SOURCE04_ONLY', 'enabled': False,
                'reason': 'CONTINUITY_QUALIFICATION_REQUIRED',
                'runtime_profile': 'so101_source_only', 'control_epoch': self.epoch,
                'peer_configured': False, 'source_cache_claimed': False,
                'lifecycle_inflight': False, 'step_inflight': False, 'inflight': False,
                'pending_stops': 0, 'stop_delivery_uncertain': False,
                'target_control_epoch': None, 'calibration_wizard': None,
                'recording': self.recorder.status(), 'worker_started': False,
                'native_control_calls': 0, 'native_STOP_delivered': False,
                'controller_configuration_writes': 0, 'physical_acceptance_confirmed': False}

    def command(self, operation, body):
        raise RuntimeError('CONTINUITY_QUALIFICATION_REQUIRED: direct03 disabled by source04 runtime profile')

    def start_worker(self):
        pass

    def stop(self, *args, **kwargs):
        # Explicit global Station STOP is handled by the original Station owner.
        # There is no retained03 target epoch or client to stop in this profile.
        return self.status()

    def close(self):
        pass
