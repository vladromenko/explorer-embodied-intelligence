"""ENGINEERING_GLUE: cached COMMON permission at the original position write.

Called with the original Station RLock held. No camera, RPC, filesystem, serial
read, model, rate or controller setting. Only provider-managed native epochs are
gated; legacy unowned epochs retain the original behavior.
"""
from collections import deque
from copy import deepcopy
import threading


class StationPublicationGate:
    def __init__(self,provider):
        self.publication_lock=threading.RLock()
        self.provider=provider;self.events=deque(maxlen=64);self.last=None;self.last_measured_hold=None
    def __call__(self,control_epoch,mode,target):
        return self._decision(control_epoch,mode,target)
    def publish(self,context,write):
        """Admission after original bus-lock wait/argument preparation.

        Revoke serializes with this narrow position-write admission only. A
        write already admitted may return; it remains recorded truthfully.
        """
        with self.publication_lock:
            if self._decision(*context) is not True:return False
            write()
            return True
    def _decision(self,control_epoch,mode,target):
        provider=self.provider;station=provider.station
        with provider.lock:
            now=provider.clock()
            records=([provider.grant] if provider.grant is not None else [])+list(provider.retired.values())
            managed=[record for record in records if record['data']['control_epoch']==control_epoch]
            cooperative=getattr(provider,'cooperative_interlock',None)
            interlock=cooperative.check_publication(control_epoch,mode,target) if cooperative is not None else {'allowed':True}
            if not managed and interlock.get('allowed') is True:return True
            record=provider.grant;data=record['data'] if record is not None and record in managed else managed[-1]['data'] if managed else {}
            profile=(station.profiles.get('follower') or {})
            permission_allowed=not managed or bool(record is not None and record in managed and not record['revoked'].is_set() and
                data['native_mode']==mode and data['control_epoch']==control_epoch and data['boot_id']==station.boot and
                data['clock_domain']==station.clock_domain and data['follower_calibration_sha256']==profile.get('calibration_sha256') and
                profile.get('verified_on_connected_hardware') is True and
                (provider._manual_demo_session(data) or now<data['observation_deadline_ns']))
            if managed and data.get('binding',{}).get('purpose')=='source04_demo':
                leader=station.profiles.get('leader') or {}
                permission_allowed=bool(permission_allowed and leader.get('verified_on_connected_hardware') is True and
                    leader.get('calibration_sha256')==data.get('leader_calibration_sha256'))
            allowed=permission_allowed and interlock.get('allowed') is True
            decision={'schema':'explorer_station_publication_permission_v1','allowed':allowed,'managed_native_epoch':bool(managed),'cooperative_hold_interlock':deepcopy(interlock),
                'control_epoch':control_epoch,'native_mode':mode,'grant_id':data.get('grant_id'),'binding_sha256':data.get('binding_sha256'),
                'checked_monotonic_ns':now,'clock_domain':station.clock_domain,'boot_id':station.boot,
                'basis':'cached_explicit_USER_permission_before_original_write','write_or_attainment_claimed':False}
            if not allowed:
                decision['reason']=interlock.get('reason') if interlock.get('allowed') is not True else 'OBSERVED_NATIVE_PERMISSION_REVOKED_EXPIRED_OR_SOURCE_CHANGED'
                if record is not None and record in managed:provider._retire(record,decision['reason'])
            self.last=decision;self.events.append(decision)
            return allowed
    def measured_hold_returned(self,new_epoch,positions,measured_ns,returned_ns):
        """Metadata only AFTER the existing measured-position write returned."""
        with self.provider.lock:
            self.last_measured_hold={'schema':'explorer_station_observed_denial_measured_hold_v1',
                'denied_native_epoch':self.last['control_epoch'],'ended_native_epoch':new_epoch,
                'grant_id':self.last['grant_id'],'requested_and_dispatched':list(positions),
                'measured_monotonic_ns':measured_ns,'write_returned_monotonic_ns':returned_ns,
                'clock_domain':self.provider.station.clock_domain,'boot_id':self.provider.station.boot,
                'original_position_write_returned':True,'MCU_attainment_verified':False,
                'old_target_sample_emitted':False}
    def status(self):
        with self.provider.lock:return {'schema':'explorer_station_publication_gate_v1','installed':True,
            'last_decision':deepcopy(self.last),'events':deepcopy(list(self.events)),
            'last_measured_hold':deepcopy(self.last_measured_hold),
            'camera_network_or_serial_reads':False,'controller_settings_written':False,
            'denial_uses_original_measured_hold_and_skips_old_target_receipt':True}
