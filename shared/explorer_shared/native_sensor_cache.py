"""ENGINEERING_GLUE: bounded, read-only causal native sensor history.

The optional sink is called by the existing OWN ROS cache subscriptions. This
module opens no device, ROS node, publisher, motor, firmware or feedback channel.
A snapshot proves received source data, never localization/contact/physical skill.
"""
from __future__ import annotations

import base64
from copy import deepcopy
import hashlib
import gzip
import io
import json
import math
import os
from pathlib import Path
import threading
import time
import uuid
import zipfile

SCHEMA='explorer_native_sensor_batch_v1'
INDEX_SCHEMA='explorer_native_sensor_index_v1'
TOPICS={'/odom_raw':'nav_msgs/msg/Odometry','/imu/data_raw':'sensor_msgs/msg/Imu',
        '/scan0':'sensor_msgs/msg/LaserScan','/scan1':'sensor_msgs/msg/LaserScan',
        '/battery':'std_msgs/msg/Float32','/camera/depth/camera_info':'sensor_msgs/msg/CameraInfo'}
MAX_BATCH_BYTES=2*1024**2
MAX_INDEX_BYTES=1024**2
MAX_CDR_BYTES=512*1024


def encoded(value):return json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()
def sha(value):return hashlib.sha256(value).hexdigest()


def ros_value(value):
    """Lossless finite fields and explicit nonfinite tags; CDR remains original."""
    if isinstance(value,float) and not math.isfinite(value):return {'nonfinite':'nan' if math.isnan(value) else '+inf' if value>0 else '-inf'}
    if value is None or type(value) in (str,int,bool,float):return value
    if isinstance(value,dict):return {str(key):ros_value(item) for key,item in value.items()}
    if isinstance(value,(tuple,list)):return [ros_value(item) for item in value]
    if hasattr(value,'get_fields_and_field_types'):return {key:ros_value(getattr(value,key)) for key in value.get_fields_and_field_types()}
    if hasattr(value,'tolist'):return ros_value(value.tolist())
    raise ValueError('Unsupported ROS message value; no guessed projection')


def atomic(path,raw):
    path=Path(path);temporary=path.with_name('.'+path.name+'.'+uuid.uuid4().hex+'.tmp')
    try:
        with temporary.open('xb') as stream:stream.write(raw);stream.flush();os.fsync(stream.fileno())
        os.chmod(temporary,0o600);temporary.replace(path)
    finally:temporary.unlink(missing_ok=True)


def finite(value):return type(value) in (int,float) and math.isfinite(value)
def vector(value,n):return isinstance(value,list) and len(value)==n and all(finite(item) for item in value)
def xyz(value):return [value.get(key) for key in ('x','y','z')] if isinstance(value,dict) else []
def quat(value):return [value.get(key) for key in ('x','y','z','w')] if isinstance(value,dict) else []


class SensorCacheSink:
    """One bounded journal inside the existing OWN camera-cache process.

    Default10Hz per topic,64 samples/topic,8MiB files; explicit counters retain
    throttling/drop facts. No wall/ROS timestamp subtraction or interpolation.
    """
    def __init__(self,root,*,clock_domain,boot_id,clock=time.monotonic_ns,wall=time.time,
                 minimum_period_ns=100_000_000,history_per_topic=64,max_bytes=8*1024**2):
        if type(minimum_period_ns) is not int or not 0<=minimum_period_ns<=1_000_000_000 or not clock_domain or not boot_id or type(history_per_topic) is not int or not 1<=history_per_topic<=256 or type(max_bytes) is not int or not MAX_BATCH_BYTES<=max_bytes<=64*1024**2:raise ValueError('Explicit bounded native sensor context required')
        self.root=Path(root);self.root.mkdir(parents=True,exist_ok=True,mode=0o700)
        self.domain,self.boot,self.clock,self.wall=clock_domain,boot_id,clock,wall
        self.period,self.history_limit,self.max_bytes=minimum_period_ns,history_per_topic,max_bytes
        self.lock=threading.RLock();self.pending=[];self.history=[];self.files={};self.last={};self.seen={};self.throttled={};self.errors={};self.instance=uuid.uuid4().hex
        # Old process files are never interpreted as new-boot measurements.
        self.flush(force=True)
        for orphan in self.root.glob('batch-*.json'):
            name=orphan.name
            if len(name)==75 and all(char in '0123456789abcdef' for char in name[6:-5]):orphan.unlink(missing_ok=True)

    def ingest(self,topic,message,serialized_message,*,received_monotonic_ns=None):
        received=self.clock() if received_monotonic_ns is None else received_monotonic_ns
        if topic not in TOPICS or type(received) is not int or not 0<=received<=self.clock():raise ValueError('Known source topic and actual same-host callback receive time required')
        if not isinstance(serialized_message,bytes) or not 0<len(serialized_message)<=MAX_CDR_BYTES:raise ValueError('Bounded original ROS CDR bytes required')
        with self.lock:
            self.seen[topic]=self.seen.get(topic,0)+1
            previous=self.last.get(topic)
            if previous is not None and received<previous:raise ValueError('Sensor callback monotonic clock regressed')
            if previous is not None and received-previous<self.period:
                self.throttled[topic]=self.throttled.get(topic,0)+1;return False
            raw=ros_value(message)
            if not isinstance(raw,dict):raise ValueError('Original ROS message object required')
            record=dict(topic=topic,ros_type=TOPICS[topic],ros_message=raw,ros_cdr_base64=base64.b64encode(serialized_message).decode(),ros_cdr_sha256=sha(serialized_message),
                received_monotonic_ns=received,received_wall_unix=self.wall(),clock_domain=self.domain,boot_id=self.boot,
                receive_time_basis='host_callback_receive_estimate_not_sensor_exposure',ros_header_clock_alignment_verified=False)
            if len(encoded(record))>MAX_BATCH_BYTES//4:raise ValueError('Sensor record exceeds bounded history')
            if len(self.pending)>=len(TOPICS)*self.history_limit or sum(len(encoded(item)) for item in self.pending)+len(encoded(record))>MAX_BATCH_BYTES-4096:raise ValueError('Sensor flush stalled; bounded pending queue exceeded')
            self.pending.append(record);self.last[topic]=received
            return True

    def flush(self,force=False):
        with self.lock:
            if not self.pending and not force:return
            records=list(self.pending)
            if records:
                payload=encoded(dict(schema=SCHEMA,writer_instance=self.instance,clock_domain=self.domain,boot_id=self.boot,records=records,physical_io=False))
                if len(payload)>MAX_BATCH_BYTES:raise ValueError('Sensor batch exceeds bound')
                name='batch-'+sha(payload)+'.json';atomic(self.root/name,payload);completed=self.clock();self.files[name]=len(payload);self.pending=[]
                self.history.extend(dict(topic=record['topic'],received_monotonic_ns=record['received_monotonic_ns'],file_complete_monotonic_ns=completed,file=name,sha256=sha(payload),record_index=index) for index,record in enumerate(records))
            counts={};kept=[]
            for row in reversed(self.history):
                counts[row['topic']]=counts.get(row['topic'],0)+1
                if counts[row['topic']]<=self.history_limit:kept.append(row)
            self.history=list(reversed(kept))
            while self.history and sum(self.files.get(name,0) for name in {row['file'] for row in self.history})>self.max_bytes:self.history.pop(0)
            active={row['file'] for row in self.history}
            document=dict(schema=INDEX_SCHEMA,writer_instance=self.instance,clock_domain=self.domain,boot_id=self.boot,
                records=self.history,seen=self.seen,throttled=self.throttled,errors=self.errors,history_per_topic=self.history_limit,
                max_bytes=self.max_bytes,published_monotonic_ns=self.clock(),clock_alignment_verified=False,physical_io=False)
            raw=encoded(document)
            if len(raw)>MAX_INDEX_BYTES:raise ValueError('Sensor index exceeds bound')
            atomic(self.root/'index.json',raw)
            for name in list(self.files):
                if name not in active:(self.root/name).unlink(missing_ok=True);del self.files[name]

    def status(self):
        with self.lock:return dict(schema=INDEX_SCHEMA,clock_domain=self.domain,boot_id=self.boot,recorded_history=len(self.history),pending=len(self.pending),seen=deepcopy(self.seen),throttled=deepcopy(self.throttled),errors=deepcopy(self.errors),physical_io=False)


def record(value,stamp,source_id,*,available=True,reason=None,mask=True):
    row=dict(present=True if available else None,available=available,value=deepcopy(value) if available else None,mask=deepcopy(mask) if available else False,stamp=deepcopy(stamp),source_id=source_id)
    if not available:row['unavailable_reason']=reason or 'NATIVE_SENSOR_NOT_RECORDED'
    return row


class NativeSensorCache:
    """Reads only OWN immutable cache batches; suitable observation_provider hook."""
    def __init__(self,root,*,clock_domain,boot_id,clock=time.monotonic_ns,max_age_ns=500_000_000,primary_scan='/scan0',camera_root=None):
        if not clock_domain or not boot_id or primary_scan not in ('/scan0','/scan1') or type(max_age_ns) is not int or max_age_ns<=0:raise ValueError('Explicit native boot/clock/sensor age required')
        self.root=Path(root);self.domain,self.boot,self.clock=clock_domain,boot_id,clock;self.max_age=max_age_ns;self.primary_scan=primary_scan;self.camera_root=Path(camera_root) if camera_root is not None else None
        self.lock=threading.RLock();self.index=None;self.index_raw=None;self.error=None

    def refresh(self):
        try:
            path=self.root/'index.json'
            if path.is_symlink() or path.stat().st_size>MAX_INDEX_BYTES:raise ValueError('Unsafe/oversized OWN sensor index')
            raw=path.read_bytes();index=json.loads(raw)
            if index.get('schema')!=INDEX_SCHEMA or index.get('clock_domain')!=self.domain or index.get('boot_id')!=self.boot:raise ValueError('Sensor index source boot/clock mismatch')
            if not isinstance(index.get('records'),list) or len(index['records'])>len(TOPICS)*256:raise ValueError('Sensor history bound exceeded')
            published=index.get('published_monotonic_ns')
            if type(published) is not int or not 0<=published<=self.clock():raise ValueError('Sensor index publication time is future/unknown')
            with self.lock:self.index,self.index_raw,self.error=index,raw,None
            return True
        except (OSError,ValueError,TypeError,KeyError) as error:
            with self.lock:self.index,self.index_raw,self.error=None,None,type(error).__name__+':'+str(error)
            return False

    def _read(self,row):
        name=row['file'];path=(self.root/name).resolve()
        if Path(name).name!=name or not path.is_relative_to(self.root.resolve()) or path.stat().st_size>MAX_BATCH_BYTES:raise ValueError('Unsafe sensor batch path/size')
        raw=path.read_bytes()
        if sha(raw)!=row['sha256']:raise ValueError('Original sensor batch SHA changed')
        batch=json.loads(raw)
        if batch.get('schema')!=SCHEMA or batch.get('clock_domain')!=self.domain or batch.get('boot_id')!=self.boot:raise ValueError('Original sensor batch boot/clock differs')
        index=row.get('record_index');records=batch.get('records')
        if type(index) is not int or not isinstance(records,list) or not 0<=index<len(records):raise ValueError('Explicit source record index required')
        source=records[index]
        if source.get('ros_type')!=TOPICS.get(row['topic']) or source.get('topic')!=row['topic'] or source.get('received_monotonic_ns')!=row['received_monotonic_ns'] or source.get('clock_domain')!=self.domain or source.get('boot_id')!=self.boot:raise ValueError('Sensor index/source identity differs')
        cdr=base64.b64decode(source['ros_cdr_base64'],validate=True)
        if not 0<len(cdr)<=MAX_CDR_BYTES or sha(cdr)!=source['ros_cdr_sha256']:raise ValueError('Original ROS CDR checksum differs')
        return source,raw

    def snapshot(self,cutoff_ns):
        if type(cutoff_ns) is not int or not 0<=cutoff_ns<=self.clock():raise ValueError('Actual past native same-kernel monotonic cutoff required')
        self.refresh()
        with self.lock:index=deepcopy(self.index);raw_index=self.index_raw;error=self.error
        result=dict(schema='explorer_native_sensor_snapshot_v1',cutoff_monotonic_ns=cutoff_ns,clock_domain=self.domain,boot_id=self.boot,
            sources={},source_payloads={},missing={},index_error=error,source_clock_alignment_verified=False,physical_io=False)
        if index is None:
            result['missing']={topic:'OWN_SENSOR_CACHE_UNAVAILABLE' for topic in TOPICS};return result
        for topic in TOPICS:
            eligible=[row for row in index['records'] if row.get('topic')==topic and type(row.get('received_monotonic_ns')) is int and type(row.get('file_complete_monotonic_ns')) is int and
                0<=row['received_monotonic_ns']<=row['file_complete_monotonic_ns']<=cutoff_ns and cutoff_ns-row['received_monotonic_ns']<=self.max_age]
            if not eligible:result['missing'][topic]='NO_FRESH_COMPLETED_SOURCE_BEFORE_NATIVE_CALL';
            else:
                selected=max(eligible,key=lambda row:row['received_monotonic_ns'])
                try:
                    source,raw=self._read(selected);name='sources/sensor-cache/'+selected['file'];result['source_payloads'][name]=raw
                    result['sources'][topic]=dict(record=source,source_file=name,source_sha256=sha(raw),json_pointer='/records/'+str(selected['record_index']),file_complete_monotonic_ns=selected['file_complete_monotonic_ns'])
                except (OSError,ValueError,TypeError,KeyError,IndexError) as failure:result['missing'][topic]='SOURCE_INTEGRITY:'+type(failure).__name__
        result['index_sha256']=sha(raw_index)
        name='sources/sensor-cache/index-'+sha(raw_index)+'.json.gz';result['source_payloads'][name]=gzip.compress(raw_index,mtime=0)
        result['original_index_asset']=dict(source_file=name,encoding='gzip',uncompressed_sha256=sha(raw_index))
        return result

    def _project(self,snapshot,topic,value,*,available=True,reason=None,mask=True):
        source=snapshot['sources'].get(topic)
        stamp=dict(clock_domain=self.domain,boot_id=self.boot,monotonic_ns=source['record']['received_monotonic_ns'] if source else snapshot['cutoff_monotonic_ns'])
        output=record(value,stamp,topic,available=available,reason=reason,mask=mask)
        if source:output['source_record_origin']={key:source[key] for key in ('source_file','source_sha256','json_pointer','file_complete_monotonic_ns')};output['source_ros_header']=deepcopy(source['record']['ros_message'].get('header'));output['timestamp_basis']='host_callback_receive_estimate_not_sensor_exposure'
        return output

    def observation_provider(self,native_receipt,frame=None):
        stamp=native_receipt.get('stamp') or {};cutoff=native_receipt.get('call_started_monotonic_ns')
        if stamp.get('clock_domain')!=self.domain or stamp.get('boot_id')!=self.boot:raise ValueError('Native receipt source boot/clock mismatch')
        return self.observation_at(cutoff,frame)

    def observation_at(self,cutoff,frame=None):
        """Read cached sensors at an actual local observation cutoff; no command required."""
        snapshot=self.snapshot(cutoff);channels={};diagnostics=dict(missing=snapshot['missing'],index_error=snapshot['index_error'],source_clock_alignment_verified=False,
            localization_verified=False,imu_absolute_heading_verified=False,lidar_extrinsics_verified=False,full_scans={},physical_io=False)
        odom=(snapshot['sources'].get('/odom_raw') or {}).get('record',{}).get('ros_message',{})
        pose=odom.get('pose',{}).get('pose',{});twist=odom.get('twist',{}).get('twist',{});position=xyz(pose.get('position'));orientation=quat(pose.get('orientation'))
        pose_ok=vector(position,3) and vector(orientation,4) and abs(sum(x*x for x in orientation)-1)<.01 and bool(odom.get('header',{}).get('frame_id'))
        channels['odometry_pose']=self._project(snapshot,'/odom_raw',dict(position=position,orientation_xyzw=orientation,units='m',frame_id=odom.get('header',{}).get('frame_id'),child_frame_id=odom.get('child_frame_id'),state_source='measured',basis='source_reported_wheel_odometry_not_global_localization'),available=pose_ok,reason=snapshot['missing'].get('/odom_raw','ODOMETRY_FIELDS_INVALID'))
        velocity=xyz(twist.get('linear'));omega=xyz(twist.get('angular'));velocity3=velocity[:2]+omega[2:] if len(velocity)==3 and len(omega)==3 else []
        channels['base_velocity']=self._project(snapshot,'/odom_raw',dict(values=velocity3,units=['m/s','m/s','rad/s'],frame_id=odom.get('child_frame_id'),state_source='measured',basis='original_odometry_twist_not_command_velocity'),available=vector(velocity3,3) and isinstance(odom.get('child_frame_id'),str) and bool(odom['child_frame_id']),reason=snapshot['missing'].get('/odom_raw','ODOMETRY_TWIST_OR_CHILD_FRAME_INVALID'))
        imu=(snapshot['sources'].get('/imu/data_raw') or {}).get('record',{}).get('ros_message',{});acceleration=xyz(imu.get('linear_acceleration'));angular=xyz(imu.get('angular_velocity'));orientation=quat(imu.get('orientation'))
        orientation_ok=vector(orientation,4) and abs(sum(x*x for x in orientation)-1)<.01 and (imu.get('orientation_covariance') or [-1])[0]!=-1
        imu_ok=vector(acceleration,3) and vector(angular,3) and (imu.get('linear_acceleration_covariance') or [-1])[0]!=-1 and (imu.get('angular_velocity_covariance') or [-1])[0]!=-1 and bool(imu.get('header',{}).get('frame_id'))
        channels['imu']=self._project(snapshot,'/imu/data_raw',dict(acceleration=acceleration,angular_velocity=angular,units=['m/s^2','rad/s'],frame_id=imu.get('header',{}).get('frame_id'),state_source='measured',orientation_xyzw=orientation if orientation_ok else None,orientation_available=orientation_ok,absolute_heading_verified=False,covariance_source='retained_original_ROS_fields_zero_covariance_means_unknown'),available=imu_ok,reason=snapshot['missing'].get('/imu/data_raw','IMU_ACCELERATION_OR_ANGULAR_FIELDS_UNAVAILABLE'))
        for topic in ('/scan0','/scan1'):
            source=snapshot['sources'].get(topic)
            if source:diagnostics['full_scans'][topic]=scan_projection(source['record']['ros_message'])
        scan=diagnostics['full_scans'].get(self.primary_scan)
        okay=bool(scan and scan['shape_valid'] and any(scan['range_valid_mask']))
        # Invalid beams remain in place as null with their original value/tag in
        # raw_ranges and CDR. They must never become free space or zero padding.
        channels['lidar']=self._project(snapshot,self.primary_scan,scan,available=okay,reason=snapshot['missing'].get(self.primary_scan,'FULL_SCAN_UNAVAILABLE_OR_INVALID'),mask=True if okay and all(scan['range_valid_mask']) else {'ranges':False,'raw_ranges':okay,'range_valid_mask':okay,'frame_id':okay})
        battery=(snapshot['sources'].get('/battery') or {}).get('record',{}).get('ros_message',{}).get('data')
        channels['power_context']=self._project(snapshot,'/battery',dict(voltage=battery,units='V',current=None,current_available=False,charging=None,permission_to_move=False),available=finite(battery) and battery>0,reason=snapshot['missing'].get('/battery','VOLTAGE_NOT_RECORDED'))
        camera=self._camera_snapshot(cutoff,frame,snapshot)
        channels.update(camera['channels']);snapshot['source_payloads'].update(camera['source_payloads']);diagnostics['camera']=camera['diagnostics']
        return dict(channels=channels,source_payloads=snapshot['source_payloads'],diagnostics=diagnostics)

    def _camera_publication(self,filename,metadata_raw,payload_sha,cutoff,output):
        path=self.camera_root/filename
        if path.stat().st_size>65536:raise ValueError('Cache publication receipt exceeds bound')
        raw=path.read_bytes();receipt=json.loads(raw);completed=receipt.get('metadata_complete_monotonic_ns')
        if receipt.get('schema')!='explorer_CPU_cache_publication_v1' or receipt.get('native_boot_id')!=self.boot or receipt.get('native_clock_domain')!=self.domain or receipt.get('metadata_sha256')!=sha(metadata_raw) or receipt.get('payload_sha256')!=payload_sha or type(completed) is not int or not 0<=cutoff-completed<=self.max_age:raise ValueError('Exact cache metadata not completed before native call')
        name='sources/sensor-cache/publication-'+sha(raw)+'.json';output['source_payloads'][name]=raw
        return completed

    def _camera_snapshot(self,cutoff,frame,snapshot):
        stamp=dict(clock_domain=self.domain,boot_id=self.boot,monotonic_ns=cutoff)
        output=dict(channels={name:record(None,stamp,'existing_CPU_ROS_camera_cache',available=False,reason='SOURCE_BOUND_CAMERA_METADATA_NOT_AVAILABLE') for name in ('camera_depth','camera_calibration')},source_payloads={},diagnostics=dict(exposure_alignment_verified=False,camera_to_base_extrinsics_verified=False))
        if self.camera_root is None or frame is None:return output
        try:
            path=self.camera_root/'camera-frame.json'
            raw=getattr(frame,'native_metadata_bytes',None)
            if raw is None:
                if path.stat().st_size>256*1024:raise ValueError('Camera metadata exceeds bound')
                raw=path.read_bytes()
            if len(raw)>256*1024:raise ValueError('Camera metadata exceeds bound')
            metadata=json.loads(raw);jpeg_sha=sha(frame.jpeg)
            received=metadata.get('received_monotonic_ns')
            if metadata.get('jpeg_sha256')!=jpeg_sha or metadata.get('native_boot_id')!=self.boot or metadata.get('native_clock_domain')!=self.domain or type(received) is not int or not 0<=cutoff-received<=self.max_age:raise ValueError('Exact JPEG/native boot/causal callback metadata missing')
            # A root-pinned Frame records when these exact metadata bytes were
            # read completely. Raw-file fallback needs its own after-write receipt.
            pinned=getattr(frame,'native_metadata_bytes',None) is not None and getattr(frame,'timestamp_basis',None)=='node_cache_read_completed_not_exposure'
            if pinned:
                completed=getattr(frame,'monotonic_ns',None)
                if type(completed) is not int or not received<=completed<=cutoff:raise ValueError('Pinned metadata read completion is future/unknown')
            else:completed=self._camera_publication('camera-cache-publication.json',raw,jpeg_sha,cutoff,output)
            stamp=dict(clock_domain=self.domain,boot_id=self.boot,monotonic_ns=max(received,completed))
            name='sources/sensor-cache/camera-'+sha(raw)+'.json';output['source_payloads'][name]=raw
            K,D=metadata.get('intrinsic_k'),metadata.get('distortion_d');valid=vector(K,9) and K[0]>0 and K[4]>0 and isinstance(D,list) and all(finite(value) for value in D) and bool(metadata.get('camera_info_version'))
            info_received,info_complete=metadata.get('camera_info_received_monotonic_ns'),metadata.get('camera_info_completed_monotonic_ns')
            info_timing=type(info_received) is int and type(info_complete) is int and 0<=info_received<=info_complete<=received
            if not info_timing:output['diagnostics']['camera_info_timing_reason']='SOURCE_CAMERA_INFO_RECEIPT_COMPLETION_NOT_BOUND_BEFORE_RGB_CALLBACK'
            if valid and info_timing:
                depth_info=(snapshot['sources'].get('/camera/depth/camera_info') or {}).get('record',{}).get('ros_message',{})
                depth_K,depth_D=depth_info.get('k'),depth_info.get('d')
                depth_known=vector(depth_K,9) and isinstance(depth_D,list) and all(finite(item) for item in depth_D) and depth_info.get('width')==frame.width and depth_info.get('height')==frame.height
                value=dict(calibration_id=metadata['camera_info_version'],camera_id=getattr(frame,'camera_id','nano-rgbd'),intrinsics=K,distortion=D,
                    frame_id=metadata.get('frame'),extrinsics=None,uncertainty=None,depth_camera_id='onboard_rgbd_depth' if depth_known else None,depth_intrinsics=depth_K if depth_known else None,depth_distortion=depth_D if depth_known else None,depth_to_rgb_extrinsics=None,
                    units='m',valid_from_ns=received,valid_until_ns=received+self.max_age,validity_basis='cache_freshness_not_extrinsic_or_physical_calibration',
                    original_native_frame_id=metadata.get('frame_id'),selected_wrapper_frame_id=frame.frame_id,selected_JPEG_sha256=jpeg_sha,camera_info_received_monotonic_ns=info_received,camera_info_completed_monotonic_ns=info_complete,metadata_available_monotonic_ns=completed)
                output['channels']['camera_calibration']=record(value,stamp,'existing_CPU_ROS_camera_info',mask={key:value[key] is not None for key in ('intrinsics','distortion','extrinsics','uncertainty','depth_intrinsics','depth_to_rgb_extrinsics')})
                output['channels']['camera_calibration']['source_record_origin']=dict(source_file=name,sha256=sha(raw),scope='original_camera_info_fields_only_no_extrinsics')
            pairpath=self.camera_root/'rgbd-cache-metadata.json'
            if pairpath.stat().st_size>256*1024:raise ValueError('RGBD metadata exceeds bound')
            pairraw=pairpath.read_bytes();pair=json.loads(pairraw);ready=pair.get('file_complete_monotonic_ns')
            ticks=[pair.get('color_received_monotonic_ns'),pair.get('depth_received_monotonic_ns'),ready]
            if pair.get('native_boot_id')!=self.boot or pair.get('native_clock_domain')!=self.domain or pair.get('color_jpeg_sha256')!=jpeg_sha or any(type(tick) is not int or not 0<=cutoff-tick<=self.max_age for tick in ticks):raise ValueError('RGBD pair not bound to exact selected JPEG before native call')
            blob=self.camera_root/'rgbd-snapshot.npz'
            if blob.stat().st_size>8*1024**2:raise ValueError('RGBD payload exceeds bound')
            pair_completed=self._camera_publication('rgbd-cache-publication.json',pairraw,pair.get('snapshot_sha256'),cutoff,output)
            ticks.append(pair_completed)
            payload=blob.read_bytes()
            if sha(payload)!=pair.get('snapshot_sha256') or pairpath.read_bytes()!=pairraw:raise ValueError('RGBD pair changed while reading')
            import numpy as np
            check_rgbd_container(payload,frame.width,frame.height)
            with np.load(io.BytesIO(payload),allow_pickle=False) as arrays:
                depth=arrays['depth'];color=arrays['rgb']
                if depth.shape!=(frame.height,frame.width) or color.shape!=(frame.height,frame.width,3) or depth.dtype.kind not in 'fiu' or color.dtype!=np.uint8:raise ValueError('Original RGBD array shape/type differs from selected RGB')
                if float(arrays['stamp'])!=pair['image_stamp'] or float(arrays['depth_stamp'])!=pair['depth_stamp']:raise ValueError('RGBD source stamps differ from original metadata')
                if str(arrays['frame'])!=pair.get('color_ros_header',{}).get('frame_id'):raise ValueError('RGBD optical source frame differs')
                valid_pixels=int((np.isfinite(depth)&(depth>0)).sum());pixel_count=int(depth.size)
            pairname='sources/sensor-cache/rgbd-'+sha(pairraw)+'.json';depthname='sensors/depth-'+sha(payload)+'.npz'
            output['source_payloads'].update({pairname:pairraw,depthname:payload})
            value=dict(source_file=depthname,sha256=sha(payload),camera_id='onboard_rgbd_depth',frame_id=pair.get('depth_ros_header',{}).get('frame_id'),encoding='numpy_npz_depth_m',units='m',
                width=frame.width,height=frame.height,aligned_to_rgb_frame_id=frame.frame_id,selected_JPEG_sha256=jpeg_sha,original_native_color_frame_id=pair.get('color_frame_id'),
                registration_basis='existing_same_optical_frame_shape_skew_helper_not_camera_to_base_extrinsics',exposure_alignment_verified=False,pixel_validity=dict(valid_count=valid_pixels,invalid_count=pixel_count-valid_pixels,basis='isfinite_and_positive_raw_depth_preserved'))
            output['channels']['camera_depth']=record(value,dict(stamp,monotonic_ns=max(ticks)), 'existing_CPU_registered_RGBD_cache')
            output['channels']['camera_depth']['source_record_origin']=dict(source_file=pairname,sha256=sha(pairraw))
        except (OSError,ValueError,KeyError,TypeError,zipfile.BadZipFile,EOFError) as error:output['diagnostics']['missing_reason']=str(error)
        return output

    def status(self):
        self.refresh()
        with self.lock:return dict(schema='explorer_native_sensor_cache_status_v1',available=self.index is not None,clock_domain=self.domain,boot_id=self.boot,records=len(self.index['records']) if self.index else 0,error=self.error,read_only=True,physical_io=False)


def scan_projection(message):
    ranges=message.get('ranges');minimum,maximum=message.get('range_min'),message.get('range_max')
    shape=isinstance(ranges,list) and 0<len(ranges)<=4096 and finite(minimum) and finite(maximum) and 0<=minimum<maximum and finite(message.get('angle_min')) and finite(message.get('angle_increment')) and message['angle_increment']!=0 and bool(message.get('header',{}).get('frame_id'))
    if not shape:return dict(shape_valid=False,raw_ranges=deepcopy(ranges),range_valid_mask=[],ranges=[],frame_id=message.get('header',{}).get('frame_id'))
    mask=[finite(value) and minimum<=value<=maximum for value in ranges]
    reasons=['valid' if valid else 'nonfinite_ROS_range' if not finite(value) else 'below_range_min' if value<minimum else 'above_range_max' for value,valid in zip(ranges,mask)]
    return dict(shape_valid=True,ranges=[value if valid else None for value,valid in zip(ranges,mask)],raw_ranges=deepcopy(ranges),range_valid_mask=mask,range_invalid_reasons=reasons,
        angle_min_rad=message['angle_min'],angle_increment_rad=message['angle_increment'],angle_max_rad=message.get('angle_max'),range_min_m=minimum,range_max_m=maximum,
        time_increment_s=message.get('time_increment'),scan_time_s=message.get('scan_time'),intensities=deepcopy(message.get('intensities')),frame_id=message['header']['frame_id'],units='m',state_source='measured',
        full_original_array=True,ray_order_preserved=True,invalid_rays_are_not_observed_free_space=True,extrinsics=None,extrinsics_verified=False)


def check_rgbd_container(payload,width,height):
    """Bound NPZ members/NPY shapes before NumPy allocates decoded arrays."""
    import numpy as np
    expected={'rgb':(height,width,3),'depth':(height,width),'stamp':(),'depth_stamp':(),'frame':()}
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        members=archive.infolist();names=[item.filename for item in members]
        if len(names)!=len(set(names)) or sum(item.file_size for item in members)>8*1024**2 or len(names)>16:raise ValueError('Original RGBD NPZ member/size bound exceeded')
        for name,shape in expected.items():
            with archive.open(name+'.npy') as stream:
                version=np.lib.format.read_magic(stream)
                if version==(1,0):actual,order,dtype=np.lib.format.read_array_header_1_0(stream,max_header_size=2048)
                elif version==(2,0):actual,order,dtype=np.lib.format.read_array_header_2_0(stream,max_header_size=2048)
                else:raise ValueError('Unsupported bounded RGBD NPY header')
                if actual!=shape or dtype.hasobject or dtype.itemsize>1024 or math.prod(actual)*dtype.itemsize>8*1024**2 or name!='frame' and dtype.kind not in 'fiu' or name=='frame' and dtype.kind not in 'US':raise ValueError('Original RGBD NPY declared shape/type differs')
