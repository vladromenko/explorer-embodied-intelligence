"""ENGINEERING_GLUE: immutable observation packets from existing SO101 caches.

No camera/serial owner, control endpoint or actuator request. The source packet
binds an original JPEG preceding the cached measured follower state, then the
same-host packet cutoff. Network freshness is separately checked by the worker.
"""
from collections import OrderedDict
from copy import deepcopy
import hashlib
from io import BytesIO
import math
import re
import threading
import time
import uuid

from .so101_station import NAMES,UNITS,LOW,HIGH


class SO101CachedObservation:
    def __init__(self,source,station,*,clock_domain,boot_id,clock=time.monotonic_ns):
        self.source,self.station=source,station
        self.domain,self.boot,self.clock=clock_domain,boot_id,clock
        self.lock=threading.RLock();self.pins=OrderedDict()

    def create(self):
        from PIL import Image
        import numpy as np
        status=self.station.status();measurement=status.get('measurements') or {}
        follower=(status.get('roles') or {}).get('follower') or {}
        measured=measurement.get('follower');stamp=measurement.get('monotonic_ns');now=self.clock()
        if (status.get('available') is not True or follower.get('verified_on_connected_hardware') is not True or
                not re.fullmatch('[0-9a-f]{64}',str(follower.get('calibration_sha256'))) or
                self.station.boot!=self.boot or self.station.clock_domain!=self.domain or
                measurement.get('clock_domain')!=self.domain or type(stamp) is not int or not 0<=now-stamp<=500_000_000):
            raise ValueError('Fresh same-boot verified follower measurement is unavailable')
        if (not isinstance(measured,list) or len(measured)!=6 or any(type(v) not in (int,float) or
                not math.isfinite(v) or not LOW[i]<=v<=HIGH[i] for i,v in enumerate(measured))):
            raise ValueError('Actual finite measured follower6 within calibrated ranges is required')
        frame=self.source.before(stamp,max_age_ns=2_000_000_000)
        if frame is None or len(frame.jpeg)>2*1024**2:raise ValueError('Original cached JPEG preceding follower measurement is unavailable')
        with Image.open(BytesIO(frame.jpeg)) as image:
            if image.size!=(frame.width,frame.height) or image.width*image.height>1920*1080:raise ValueError('Cached JPEG dimensions differ')
            rgb=np.asarray(image.convert('RGB'))
        cutoff=self.clock()
        if not frame.monotonic_ns<=stamp<=cutoff or cutoff-frame.monotonic_ns>2_000_000_000:raise ValueError('Cached observation became stale or noncausal')
        ident=uuid.uuid4().hex
        observation=dict(observation_id=ident,camera_id=self.source.camera_id,frame_id=frame.frame_id,
            frame_sha256=hashlib.sha256(frame.jpeg).hexdigest(),decoded_rgb_sha256=hashlib.sha256(rgb.tobytes()).hexdigest(),
            width=frame.width,height=frame.height,follower_calibration_sha256=follower['calibration_sha256'],state_basis='measured_state',
            frame_clock_domain=self.domain,state_clock_domain=self.domain,source_cutoff_clock_domain=self.domain,
            frame_boot_id=self.boot,state_boot_id=self.boot,source_cutoff_boot_id=self.boot,
            frame_monotonic_ns=frame.monotonic_ns,state_monotonic_ns=stamp,source_cutoff_monotonic_ns=cutoff,
            source_cutoff_basis='native_observation_packet_created',timestamp_basis=frame.timestamp_basis,
            exposure_synchronization_verified=False,physical_actions_emitted=False)
        packet=dict(schema='explorer_so101_cached_observation_v1',observation=observation,
            measured_native6=deepcopy(measured),valid_mask=[True]*6,names=list(NAMES),units=list(UNITS),hardware_commands_dispatched=False)
        with self.lock:
            self.pins[ident]=(deepcopy(packet),frame.jpeg)
            while len(self.pins)>8 or sum(len(row[1]) for row in self.pins.values())>16*1024**2:self.pins.popitem(last=False)
        return packet

    def frame(self,ident):
        if not isinstance(ident,str) or not re.fullmatch('[0-9a-f]{32}',ident):raise ValueError('Exact SO101 observation ID is required')
        with self.lock:pair=self.pins.get(ident)
        if pair is None:raise ValueError('SO101 observation was evicted; request a new packet')
        packet,jpeg=pair;observation=packet['observation']
        if not 0<=self.clock()-observation['source_cutoff_monotonic_ns']<=2_000_000_000:raise ValueError('SO101 observation expired; request a fresh packet')
        return jpeg,dict(observation)
