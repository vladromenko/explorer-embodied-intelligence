"""Pure history fixtures; no ROS nodes, devices, publishers or physical IO."""
from copy import deepcopy
import gzip
import hashlib
import io
import json
from pathlib import Path
from types import SimpleNamespace as NS

import numpy as np
import pytest

from explorer_shared.native_sensor_cache import (NativeSensorCache,SensorCacheSink,TOPICS,encoded,ros_value,scan_projection,sha)


class Clock:
    value=1_000_000_000
    def __call__(self):return self.value


def header(frame='source-frame'):return dict(stamp=dict(sec=50_000,nanosec=0),frame_id=frame)
def v(x=0.,y=0.,z=0.):return dict(x=x,y=y,z=z)
def q():return dict(x=0.,y=0.,z=0.,w=1.)
def odom():return dict(header=header('odom_raw'),child_frame_id='base_footprint',pose=dict(pose=dict(position=v(),orientation=q()),covariance=[0.]*36),twist=dict(twist=dict(linear=v(),angular=v()),covariance=[0.]*36))
def imu():return dict(header=header('imu_frame'),linear_acceleration=v(z=9.8),angular_velocity=v(x=.1),orientation=q(),orientation_covariance=[0.]*9,linear_acceleration_covariance=[0.]*9,angular_velocity_covariance=[0.]*9)
def scan():return dict(header=header('laser0_frame'),ranges=[.2,0.,float('inf'),float('nan'),13.,.3],intensities=[1.,2.,3.,4.,5.,6.],angle_min=0.,angle_max=.5,angle_increment=.1,range_min=.05,range_max=12.,scan_time=.08,time_increment=.01)


def rig(tmp_path,**options):
    clock=Clock();sink=SensorCacheSink(tmp_path/'cache',clock_domain='nano:boot',boot_id='boot',clock=clock,**options)
    reader=NativeSensorCache(sink.root,clock_domain='nano:boot',boot_id='boot',clock=clock)
    return sink,reader,clock


def publish(sink,clock,topic,message):
    # CDR is explicit software fixture bytes here; actual ROS CDR qualified in
    # the separate passive read-only Nano evidence, not generated from zeros.
    return sink.ingest(topic,message,b'FIXTURE-CDR-'+topic.encode(),received_monotonic_ns=clock.value)


def receipt(clock):return dict(stamp=dict(clock_domain='nano:boot',boot_id='boot',monotonic_ns=clock.value+10),call_started_monotonic_ns=clock.value)


def test_actual_neutral_odometry_is_measured_and_not_masked_with_own_clock_causal_selection(tmp_path):
    sink,reader,clock=rig(tmp_path);publish(sink,clock,'/odom_raw',odom());publish(sink,clock,'/imu/data_raw',imu());sink.flush();clock.value+=10
    output=reader.observation_provider(receipt(clock));channels=output['channels']
    assert channels['base_velocity']['value']['values']==[0.,0.,0.] and channels['base_velocity']['mask'] is True
    assert channels['base_velocity']['value']['basis']=='original_odometry_twist_not_command_velocity'
    assert channels['odometry_pose']['value']['frame_id']=='odom_raw'
    assert channels['imu']['value']['orientation_available'] is True
    assert channels['imu']['value']['absolute_heading_verified'] is False
    assert channels['imu']['stamp']['monotonic_ns']<clock.value
    assert channels['imu']['source_ros_header']['stamp']['sec']==50_000  # Unaligned/future wall header preserved, never subtracted.
    assert channels['lidar']['available'] is False and channels['lidar']['value'] is None
    assert channels['camera_depth']['available'] is False
    sources=output['source_payloads'];assert all(name.startswith('sources/sensor-cache/') for name in sources)
    packed=next(raw for name,raw in sources.items() if name.endswith('.json.gz'))
    assert json.loads(gzip.decompress(packed))['records']


def test_full_scan_invalid_beams_never_become_fake_free_space_or_reindexed(tmp_path):
    sink,reader,clock=rig(tmp_path);publish(sink,clock,'/scan0',scan());second=scan();second['header']=header('laser1_frame');publish(sink,clock,'/scan1',second);sink.flush();clock.value+=1
    output=reader.observation_provider(receipt(clock));value=output['channels']['lidar']['value']
    assert value['range_valid_mask']==[True,False,False,False,False,True]
    assert value['ranges']==[.2,None,None,None,None,.3] and value['raw_ranges'][1]==0.
    assert value['raw_ranges'][2]=={'nonfinite':'+inf'} and value['raw_ranges'][3]=={'nonfinite':'nan'}
    assert value['intensities']==[1.,2.,3.,4.,5.,6.] and len(value['ranges'])==len(value['raw_ranges'])
    assert value['range_invalid_reasons'][1]=='below_range_min'
    assert output['channels']['lidar']['mask']['ranges'] is False  # Current canonical full-vector gate remains honest.
    assert output['diagnostics']['full_scans']['/scan1']['frame_id']=='laser1_frame'
    assert value['frame_id']=='laser0_frame' and value['extrinsics'] is None


def test_future_completed_batch_and_new_callback_cannot_leak_into_prior_native_action(tmp_path):
    sink,reader,clock=rig(tmp_path);publish(sink,clock,'/odom_raw',odom());first=clock.value;clock.value+=100;sink.flush()
    assert '/odom_raw' not in reader.snapshot(first+1)['sources']  # Record not yet persisted.
    clock.value+=100_000_000;later=odom();later['twist']['twist']['linear']['x']=.2;publish(sink,clock,'/odom_raw',later);sink.flush()
    before=reader.snapshot(first+101);assert before['sources']['/odom_raw']['record']['ros_message']['twist']['twist']['linear']['x']==0.
    current=reader.snapshot(clock.value);assert current['sources']['/odom_raw']['record']['ros_message']['twist']['twist']['linear']['x']==.2
    with pytest.raises(ValueError,match='past'):reader.snapshot(clock.value+1)
    bad=receipt(clock);bad['stamp']['boot_id']='foreign'
    with pytest.raises(ValueError,match='boot/clock'):reader.observation_provider(bad)


def test_stale_wrong_boot_and_batch_tamper_fail_closed(tmp_path):
    sink,reader,clock=rig(tmp_path);publish(sink,clock,'/imu/data_raw',imu());sink.flush();clock.value+=600_000_000
    assert '/imu/data_raw' not in reader.snapshot(clock.value)['sources']
    wrong=NativeSensorCache(sink.root,clock_domain='nano:other',boot_id='other',clock=clock)
    assert wrong.snapshot(clock.value)['sources']=={}
    clock.value-=600_000_000
    batch=next(sink.root.glob('batch-*.json'));batch.write_bytes(b'changed')
    result=reader.snapshot(clock.value);assert '/imu/data_raw' not in result['sources'] and result['missing']['/imu/data_raw'].startswith('SOURCE_INTEGRITY')


def test_IMU_missing_orientation_does_not_discard_real_acceleration_or_invent_heading(tmp_path):
    sink,reader,clock=rig(tmp_path);message=imu();message['orientation_covariance'][0]=-1.;publish(sink,clock,'/imu/data_raw',message);sink.flush()
    channel=reader.observation_provider(receipt(clock))['channels']['imu'];assert channel['available'] and channel['mask'] is True
    assert channel['value']['orientation_xyzw'] is None and channel['value']['orientation_available'] is False
    assert channel['value']['acceleration']==[0.,0.,9.8]


def test_bounded_history_prunes_only_own_batches_and_restart_does_not_keep_old_sources(tmp_path):
    sink,reader,clock=rig(tmp_path,history_per_topic=2);(sink.root/'operator-notes.txt').write_bytes(b'untouched')
    for index in range(8):
        clock.value+=100_000_000;message=odom();message['twist']['twist']['linear']['x']=float(index);publish(sink,clock,'/odom_raw',message);sink.flush()
    assert len(sink.history)==2 and len(list(sink.root.glob('batch-*.json')))==2
    assert reader.snapshot(clock.value)['sources']['/odom_raw']['record']['ros_message']['twist']['twist']['linear']['x']==7.
    new=SensorCacheSink(sink.root,clock_domain='nano:boot',boot_id='boot',clock=clock)
    assert reader.snapshot(clock.value)['sources']=={} and not list(sink.root.glob('batch-*.json'))
    assert (sink.root/'operator-notes.txt').read_bytes()==b'untouched'


def test_throttling_and_nonfinite_raw_fields_are_explicit_not_fabricated(tmp_path):
    sink,reader,clock=rig(tmp_path);assert publish(sink,clock,'/scan0',scan());clock.value+=1
    assert not publish(sink,clock,'/scan0',scan());sink.flush()
    assert sink.status()['seen']['/scan0']==2 and sink.status()['throttled']['/scan0']==1
    source=reader.snapshot(clock.value)['sources']['/scan0']['record']
    assert source['ros_message']['ranges'][2]=={'nonfinite':'+inf'} and source['ros_cdr_sha256']==sha(b'FIXTURE-CDR-/scan0')


def camera_files(tmp_path,clock):
    camera=tmp_path/'camera';camera.mkdir();frame=NS(frame_id='own-wrapper-frame',width=3,height=2,jpeg=b'EXACT-SOFTWARE-JPEG')
    meta=dict(native_boot_id='boot',native_clock_domain='nano:boot',received_monotonic_ns=clock.value,jpeg_sha256=sha(frame.jpeg),frame_id='native-original-frame',frame='camera_color_optical_frame',intrinsic_k=[500.,0,1.,0,500.,1.,0,0,1.],distortion_d=[0.]*5,camera_info_version='c'*64)
    (camera/'camera-frame.json').write_bytes(encoded(meta))
    buffer=io.BytesIO();np.savez(buffer,depth=np.asarray([[1.,0.,1.],[1.,1.,1.]],np.float32),rgb=np.zeros((2,3,3),np.uint8),stamp=100.,depth_stamp=100.01,frame='camera_color_optical_frame')
    payload=buffer.getvalue();(camera/'rgbd-snapshot.npz').write_bytes(payload)
    pair=dict(native_boot_id='boot',native_clock_domain='nano:boot',color_received_monotonic_ns=clock.value,depth_received_monotonic_ns=clock.value,file_complete_monotonic_ns=clock.value,
        color_jpeg_sha256=sha(frame.jpeg),color_frame_id='native-original-frame',snapshot_sha256=sha(payload),image_stamp=100.,depth_stamp=100.01,color_ros_header=header('camera_color_optical_frame'),depth_ros_header=header('camera_color_optical_frame'))
    (camera/'rgbd-cache-metadata.json').write_bytes(encoded(pair));return camera,frame,meta,pair


def test_depth_and_partial_calibration_require_exact_selected_JPEG_and_no_fake_extrinsics(tmp_path):
    sink,reader,clock=rig(tmp_path);camera,frame,meta,pair=camera_files(tmp_path,clock);reader.camera_root=camera
    output=reader.observation_provider(receipt(clock),frame);depth=output['channels']['camera_depth'];calib=output['channels']['camera_calibration']
    assert depth['available'] and depth['value']['aligned_to_rgb_frame_id']==frame.frame_id
    assert depth['value']['pixel_validity']['invalid_count']==1
    assert depth['value']['selected_JPEG_sha256']==sha(frame.jpeg)
    assert calib['available'] and calib['value']['extrinsics'] is None and calib['mask']['extrinsics'] is False
    assert calib['value']['original_native_frame_id']=='native-original-frame' and calib['value']['selected_wrapper_frame_id']==frame.frame_id
    assert depth['value']['source_file'] in output['source_payloads']


@pytest.mark.parametrize('fault',['JPEG','boot','future_depth','wrong_pair','payload'])
def test_unbound_or_future_camera_data_remains_unavailable(tmp_path,fault):
    sink,reader,clock=rig(tmp_path);camera,frame,meta,pair=camera_files(tmp_path,clock);reader.camera_root=camera
    if fault=='JPEG':meta['jpeg_sha256']='z'*64
    elif fault=='boot':meta['native_boot_id']='other'
    elif fault=='future_depth':pair['depth_received_monotonic_ns']=clock.value+1
    elif fault=='wrong_pair':pair['color_jpeg_sha256']='z'*64
    else:(camera/'rgbd-snapshot.npz').write_bytes(b'tamper')
    (camera/'camera-frame.json').write_bytes(encoded(meta));(camera/'rgbd-cache-metadata.json').write_bytes(encoded(pair))
    output=reader.observation_provider(receipt(clock),frame)
    assert output['channels']['camera_depth']['available'] is False
    if fault in ('JPEG','boot'):assert output['channels']['camera_calibration']['available'] is False
    else:assert output['channels']['camera_calibration']['available'] is True


def test_RGBD_huge_declared_shape_is_rejected_before_any_array_allocation(tmp_path):
    import zipfile
    from explorer_shared.native_sensor_cache import check_rgbd_container
    stream=io.BytesIO()
    with zipfile.ZipFile(stream,'w',compression=zipfile.ZIP_DEFLATED) as archive:
        for name,shape in [('rgb',(2,3,3)),('depth',(2**35,2**35)),('stamp',()),('depth_stamp',()),('frame',())]:
            member=io.BytesIO();np.lib.format.write_array_header_1_0(member,{'descr':'<f4' if name!='frame' else '<U20','fortran_order':False,'shape':shape});archive.writestr(name+'.npy',member.getvalue())
    with pytest.raises(ValueError,match='declared shape'):check_rgbd_container(stream.getvalue(),3,2)


def test_bad_RGBD_container_cannot_discard_other_valid_sensor_channels(tmp_path):
    sink,reader,clock=rig(tmp_path);publish(sink,clock,'/odom_raw',odom());sink.flush();camera,frame,meta,pair=camera_files(tmp_path,clock);reader.camera_root=camera
    payload=b'not-an-NPZ-despite-matching-metadata-SHA';(camera/'rgbd-snapshot.npz').write_bytes(payload);pair['snapshot_sha256']=sha(payload);(camera/'rgbd-cache-metadata.json').write_bytes(encoded(pair))
    output=reader.observation_provider(receipt(clock),frame)
    assert output['channels']['base_velocity']['available'] and output['channels']['camera_calibration']['available']
    assert output['channels']['camera_depth']['available'] is False and output['diagnostics']['camera']['missing_reason']
