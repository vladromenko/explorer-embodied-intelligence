"""ENGINEERING_GLUE: GET-only ROSMASTER cached runtime observation packets.

Same-boot original owner RGB/command9 plus OWN sensor cache. No camera/device,
ROS publisher, controller command, action target or synthetic measured q.
"""
from collections import OrderedDict
from copy import deepcopy
from io import BytesIO
import base64
import hashlib
import json
from pathlib import Path
import re
import threading
import time
from types import SimpleNamespace
from urllib.request import Request,build_opener,ProxyHandler,HTTPRedirectHandler
import uuid

from .contracts import ACTION_NAMES,ACTION_UNITS,canonical
from .native_model_views import NativeFeatureViews
from .embodied_episode import observation_channels


def sha(raw):return hashlib.sha256(raw).hexdigest()


class OriginalObservationReader:
    def __init__(self,bridge):
        self.bridge=bridge
        class NoRedirect(HTTPRedirectHandler):
            def redirect_request(self,*args,**kwargs):raise OSError('Fixed original-owner observation must not redirect')
        self.opener=build_opener(ProxyHandler({}),NoRedirect()).open
    def observation(self):
        token=(self.bridge.root/'config/access_token').read_text().strip()
        request=Request('http://127.0.0.1:8080/api/explorer/cooperative/observation',headers={'Authorization':'Bearer '+token})
        with self.opener(request,timeout=2) as response:
            raw=response.read(128*1024+1)
            if len(raw)>128*1024:raise ValueError('Original observation response exceeds bound')
        value=json.loads(raw)
        if not isinstance(value,dict):raise ValueError('Original runtime observation object required')
        jpeg,headers=self.bridge.cooperative_frame(value.get('observation_id'))
        if (sha(jpeg)!=value.get('jpeg_sha256') or headers.get('X-Frame-ID')!=value.get('frame_id') or
                headers.get('X-Boot-ID')!=value.get('boot_id')):raise ValueError('Exact original pinned RGB/clock identity differs')
        return value,raw,jpeg


class RosmasterCachedObservation:
    def __init__(self,reader,sensor_cache,source,*,clock_domain,boot_id,clock=time.monotonic_ns):
        self.reader,self.sensors,self.source=reader,sensor_cache,source
        self.domain,self.boot,self.clock=clock_domain,boot_id,clock
        self.lock=threading.RLock();self.pins=OrderedDict();self.views=NativeFeatureViews()
    def create(self):
        from PIL import Image
        observation,raw,jpeg=self.reader.observation();cutoff=self.clock()
        stamps=[observation.get('monotonic_ns'),observation.get('reference_read_native_ns')]
        values=observation.get('command_reference9');base=observation.get('base_reference_receipt') or {}
        issued=(observation.get('issued_context_receipts') or {}).get('arm') or {}
        header=observation.get('original_reference_header') or {}
        if (observation.get('boot_id')!=self.boot or observation.get('clock_domain')!=self.domain or
                observation.get('state_source')!='command_estimate' or header.get('native_guard_verified') is not True or
                any(type(stamp) is not int or not 0<=cutoff-stamp<=2_000_000_000 for stamp in stamps) or
                type(base.get('stamp_ns')) is not int or not base.get('receipt_id') or base['stamp_ns']>observation['reference_read_native_ns']):
            raise ValueError('Actual same-boot source RGB/reference/base receipt must precede packet cutoff')
        if (observation.get('command_reference9_basis')!='actual_original_publisher_and_base_request_as_of_snapshot' or
                observation.get('journal_pose_used_for_command9') is not False or issued.get('native_host_publication_returned') is not True or
                (issued.get('stamp') or {}).get('boot_id')!=self.boot or (issued.get('stamp') or {}).get('clock_domain')!=self.domain or
                type((issued.get('stamp') or {}).get('monotonic_ns')) is not int or
                issued['stamp']['monotonic_ns']>observation['reference_read_native_ns']):
            raise ValueError('Actual issued publisher receipt basis required; journal/API accepted state is insufficient')
        if (not isinstance(values,list) or len(values)!=9 or any(type(value) not in (int,float) for value in values) or
                len(base.get('velocity',[]))!=3 or values[6:]!=base['velocity'] or values[:6]!=observation.get('reference_servo_deg') or
                values[:6]!=issued.get('pose_deg')):
            raise ValueError('Actual original command9 required; unknown base is never replaced with zeros')
        import math
        if not all(math.isfinite(value) for value in values):raise ValueError('Finite original command9 required')
        with Image.open(BytesIO(jpeg)) as image:
            if image.width*image.height>1920*1080:raise ValueError('Bounded actual RGB frame required')
            width,height=image.size
        metadata=None
        if self.source is not None:
            with self.source.lock:
                match=next((frame for frame in reversed(self.source._history) if frame.jpeg==jpeg and
                    getattr(frame,'native_metadata_bytes',None) and frame.monotonic_ns<=cutoff and
                    json.loads(frame.native_metadata_bytes).get('frame_id')==observation['frame_id'] and
                    json.loads(frame.native_metadata_bytes).get('received_monotonic_ns')==observation['monotonic_ns']),None)
            if match is not None:metadata=match.native_metadata_bytes
        frame=SimpleNamespace(jpeg=jpeg,width=width,height=height,frame_id=observation['frame_id'],
            camera_id='nano-rgbd',monotonic_ns=observation['monotonic_ns'],native_metadata_bytes=metadata)
        receipt={'stamp':{'clock_domain':self.domain,'boot_id':self.boot,'monotonic_ns':cutoff},
            'call_started_monotonic_ns':cutoff,'kind':'observation_packet_created_not_action_dispatch'}
        provided=(self.sensors.observation_provider(receipt,frame) if self.sensors is not None else
            {'channels':{},'source_payloads':{},'diagnostics':{'missing':'OWN_SENSOR_CACHE_NOT_CONFIGURED'}})
        for name,field in (('camera_calibration','original_native_frame_id'),('camera_depth','original_native_color_frame_id')):
            item=(provided.get('channels') or {}).get(name) or {}
            if item.get('available') is True and (item.get('value') or {}).get(field)!=observation['frame_id']:
                provided.setdefault('diagnostics',{})[name+'_rejected_original_projection']=deepcopy(item)
                provided['channels'][name]={**item,'present':None,'available':False,'value':None,'mask':False,
                    'unavailable_reason':'CAMERA_METADATA_NOT_EXACT_SELECTED_NATIVE_FRAME'}
        provided=self.views.convert(provided)
        unavailable={'present':None,'available':False,'value':None,'mask':False,
            'stamp':{'clock_domain':self.domain,'boot_id':self.boot,'monotonic_ns':cutoff},
            'unavailable_reason':'NOT_EXPOSED_BY_EXISTING_NATIVE_CACHE'}
        channels={name:deepcopy(unavailable) for name in observation_channels()};channels.update(provided['channels'])
        payloads=provided['source_payloads']
        native_name='sources/original-runtime-observation-'+sha(raw)+'.json';image_name='frames/'+sha(jpeg)+'.jpg'
        payloads.update({native_name:raw,image_name:jpeg})
        def record(value,tick,*,available=True,mask=True):
            return {'present':True if available else None,'available':available,'value':deepcopy(value) if available else None,
                'mask':mask if available else False,'stamp':{'clock_domain':self.domain,'boot_id':self.boot,'monotonic_ns':tick},
                'source_record_origin':{'source_file':native_name,'sha256':sha(raw)},'measured_feedback_claimed':False}
        channels['camera_rgb']=record({'source_file':image_name,'sha256':sha(jpeg),'camera_id':'nano-rgbd',
            'frame_id':observation['frame_id'],'width':width,'height':height,'encoding':'jpeg',
            'timestamp_basis':observation['timestamp_basis']},observation['monotonic_ns'])
        channels['preceding_command9']=record({'values':values,'names':list(ACTION_NAMES),'units':list(ACTION_UNITS),
            'state_source':'command_estimate','actual_base_receipt_id':base['receipt_id']},observation['reference_read_native_ns'],mask=[True]*9)
        channels['joint_command']=record({'values':values[:6],'units':'deg','state_source':'command_estimate'},observation['reference_read_native_ns'],mask=[True]*6)
        channels['joint_feedback']=record(None,cutoff,available=False)
        channels['clocks']=record({'clock_domain':self.domain,'boot_id':self.boot},cutoff)
        if sum(len(item) for item in payloads.values())>8*1024**2:raise ValueError('Bounded runtime source packet budget exceeded; no source values dropped')
        ident=uuid.uuid4().hex;document={'schema':'explorer_rosmaster_runtime_channels_v1','channels':channels,
            'source_cutoff_monotonic_ns':cutoff,'clock_domain':self.domain,'boot_id':self.boot,
            'native_observation':observation,'format_views':provided.get('diagnostics',{}).get('model_format_view'),
            'hardware_commands_dispatched':False,'source_kind':'physical','source_boot_identity_verified':True}
        name='sources/runtime-channel-view-'+sha(canonical(document))+'.json';payloads[name]=canonical(document)
        for channel_name,channel in channels.items():
            channel['provenance']={'source_file':name,'sha256':sha(payloads[name]),'json_pointer':'/channels/'+channel_name}
        packet={'schema':'explorer_rosmaster_cached_observation_v1','observation_id':ident,'observation':document,
            'source_files_sha256':{name:sha(raw) for name,raw in payloads.items()},
            'source_payloads_base64':{name:base64.b64encode(raw).decode() for name,raw in payloads.items()},
            'state_profile':'ROSMASTER_ACT9_RGB_AVAILABLE_SENSOR106_V1','action_dimension':9,
            'hardware_commands_dispatched':False,'physical_execution_authorized':False}
        with self.lock:
            self.pins[ident]=(deepcopy(packet),jpeg)
            while len(self.pins)>8 or sum(len(canonical(item[0])) for item in self.pins.values())>32*1024**2:self.pins.popitem(last=False)
        return packet
    def frame(self,identity):
        if not isinstance(identity,str) or not re.fullmatch('[0-9a-f]{32}',identity):raise ValueError('Exact server-issued runtime observation ID required')
        with self.lock:item=self.pins.get(identity)
        if item is None:raise ValueError('Original runtime observation was evicted')
        packet,jpeg=item;observation=packet['observation'];cutoff=observation['source_cutoff_monotonic_ns']
        if not 0<=self.clock()-cutoff<=2_000_000_000:raise ValueError('Original runtime observation expired')
        return jpeg,{'observation_id':identity,'frame_id':observation['native_observation']['frame_id'],
            'jpeg_sha256':sha(jpeg),'boot_id':self.boot,'clock_domain':self.domain}
