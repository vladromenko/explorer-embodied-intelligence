"""Real filesystem/cache timing, software JPEGs; no ROS/device/control requests."""
import hashlib
import io
import json
import time

from PIL import Image

from explorer_shared.node_agent import LegacyFileCamera
from explorer_shared.native_sensor_cache import NativeSensorCache
from test_native_capture import setup,publication,sealed,outcomes


def test_rgb_cutoff_is_original_call_start_not_later_publication_completion(tmp_path):
    writer,tap,camera,clock,ident,_=setup(tmp_path)
    try:
        packet,prior=publication(tap,camera,clock,stamp=2250)
        # Different JPEG arrived AFTER native call started2200, before return2250.
        future=b'\xff\xd8future-after-command\xff\xd9'
        camera.publish(future,4,3,monotonic_ns=2220)
        assert writer.submit(ident,packet)
        result=sealed(writer,ident,outcomes(ident))
        rgb=result['embodied_episode']['samples'][0]['channels']['camera_rgb']
        assert rgb['value']['sha256']==hashlib.sha256(prior).hexdigest()
        assert rgb['stamp']['monotonic_ns']==2100
    finally:writer.close()


def test_cached_original_metadata_survives_new_file_and_read_completion_guards_causality(tmp_path):
    image=io.BytesIO();Image.new('RGB',(8,6),'red').save(image,'JPEG');jpeg=image.getvalue()
    path=tmp_path/'frame-raw.jpg';path.write_bytes(jpeg)
    start=time.monotonic_ns();received=start-50_000_000
    original=dict(frame_id='actual-original-frame',native_boot_id='boot',native_clock_domain='nano:boot',
        width=8,height=6,jpeg_sha256=hashlib.sha256(jpeg).hexdigest(),received_monotonic_ns=received,
        camera_info_received_monotonic_ns=received-10_000_000,camera_info_completed_monotonic_ns=received-5_000_000,
        camera_info_version='original-camera-info',intrinsic_k=[10,0,4,0,10,3,0,0,1],distortion_d=[0,0,0,0,0],frame='original_optical_frame')
    raw=json.dumps(original).encode();metadata=tmp_path/'camera-frame.json';metadata.write_bytes(raw)
    camera=LegacyFileCamera(path,tmp_path/'status.json',clock_domain='nano:boot',boot_id='boot');camera.start()
    try:
        deadline=time.monotonic()+1
        while camera.latest() is None and time.monotonic()<deadline:time.sleep(.005)
        frame=camera.latest();assert frame is not None and frame.native_metadata_bytes==raw
        assert frame.monotonic_ns>=start and frame.timestamp_basis=='node_cache_read_completed_not_exposure'
        assert camera.before(start-1) is None
        assert camera.before(time.monotonic_ns()) is frame
        assert frame.metadata()['original_source_metadata']['frame_id']=='actual-original-frame'
        assert frame.frame_id!='actual-original-frame' and not frame.metadata()['exposure_timestamp_available']
        # Metadata for a later different source must never replace this JPEG's original.
        metadata.write_text(json.dumps(dict(original,frame_id='new-frame',jpeg_sha256='b'*64)))
        cache=NativeSensorCache(tmp_path/'sensor-history',clock_domain='nano:boot',boot_id='boot',camera_root=tmp_path)
        packet=cache.observation_provider(dict(call_started_monotonic_ns=time.monotonic_ns(),stamp={'clock_domain':'nano:boot','boot_id':'boot'}),frame)
        calibration=packet['channels']['camera_calibration']
        assert calibration['available'] and calibration['value']['original_native_frame_id']=='actual-original-frame'
        assert calibration['value']['extrinsics'] is None
        assert raw in packet['source_payloads'].values()
        assert not camera.metadata()['camera_to_base_extrinsics_verified']
    finally:camera.close()


def test_unbound_or_future_original_camera_metadata_never_becomes_verified(tmp_path):
    jpeg=b'\xff\xd8software-fixture\xff\xd9';path=tmp_path/'frame-raw.jpg'
    source=LegacyFileCamera(path,tmp_path/'status.json',clock_domain='nano:boot',boot_id='boot')
    body=dict(frame_id='id',width=8,height=6,native_boot_id='other-boot',native_clock_domain='nano:boot',jpeg_sha256=hashlib.sha256(jpeg).hexdigest(),received_monotonic_ns=time.monotonic_ns())
    metadata=tmp_path/'camera-frame.json';metadata.write_text(json.dumps(body))
    assert source._native_metadata(jpeg,8,6) is None
    body.update(native_boot_id='boot',received_monotonic_ns=time.monotonic_ns()+10_000_000_000)
    metadata.write_text(json.dumps(body));assert source._native_metadata(jpeg,8,6) is None
