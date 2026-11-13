import json,os,time
from pathlib import Path
import numpy as np
from PIL import Image
import pytest
from so101_lab.recording import RecordingManager,EpisodeWriter,SyntheticSource
from so101_lab.hardware import HardwareSource,hardware_settings,JOINTS
from test_recording import wait_for


def test_scene_preparation_begin_and_auto_next_do_not_save_preparation(tmp_path):
 m=RecordingManager(tmp_path);d=m.create_dataset('workflow','technical test')
 m.start(d['dataset_id'],fps=20,episode_count=2,episode_seconds=10,preparation_seconds=10,keep_open=True,auto_advance=True,width=160,height=120)
 wait_for(m,lambda s:s['state']=='preparing');assert not list((Path(d['path'])/'.partial').glob('*'))
 m.command('begin');wait_for(m,lambda s:s.get('frames_written',0)>=2)
 m.command('end');s=wait_for(m,lambda s:s['state']=='preparing' and s['completed_in_session']==1)
 assert m.dataset_info(d['dataset_id'])['episodes']==1
 m.command('rerecord');wait_for(m,lambda s:s.get('completed_in_session')==0)
 assert m.dataset_info(d['dataset_id'])['quarantine_count']==1
 m.command('stop');assert m.wait()['state']=='stopped'
 assert m.dataset_info(d['dataset_id'])['episodes']==0


def test_fast_png_parallel_writer_preserves_exact_rgb(tmp_path):
 source=SyntheticSource({'width':640,'height':480});source.start();sample=source.sample(1)
 rgb=np.random.default_rng(42).integers(0,256,(480,640,3),dtype=np.uint8)
 for frame in sample['images'].values():frame['image']=rgb
 writer=EpisodeWriter(tmp_path,{'episode_id':'exact_rgb'});writer.put(sample);result=writer.finish()
 assert result['status']=='complete'
 row=json.loads((Path(result['path'])/'frames.jsonl').read_text())
 for raw in row['cameras'].values():assert np.array_equal(np.asarray(Image.open(Path(result['path'])/raw['path'])),rgb)
 assert result['writer_performance']['encoder']=='opencv_png1_lossless'


def test_image_write_does_not_fsync_each_frame(tmp_path,monkeypatch):
 source=SyntheticSource({'width':160,'height':120});source.start();sample=source.sample(1)
 writer=EpisodeWriter(tmp_path,{'episode_id':'no_per_frame_fsync'})
 calls=[]
 monkeypatch.setattr('so101_lab.recording.os.fsync',lambda fd:calls.append(fd))
 writer.put(sample);result=writer.finish()
 assert result['status']=='complete'
 # Metadata/manifest/directory durability remains, but neither camera image
 # invokes an additional synchronous flush in the high-rate writer path.
 assert len(calls)<8


def test_active_recording_never_fsyncs_manifest(tmp_path,monkeypatch):
 source=SyntheticSource({'width':160,'height':120});source.start()
 calls=[]
 real_fsync=os.fsync
 monkeypatch.setattr('so101_lab.recording.os.fsync',lambda fd:calls.append(fd))
 writer=EpisodeWriter(tmp_path,{'episode_id':'no_live_manifest_fsync'})
 calls.clear()  # Initial durable episode metadata is written before capture starts.
 for frame_id in range(1,31):writer.put(source.sample(frame_id))
 deadline=time.monotonic()+3
 while writer.frames<30 and time.monotonic()<deadline:time.sleep(.01)
 assert writer.frames==30 and calls==[]
 monkeypatch.setattr('so101_lab.recording.os.fsync',real_fsync)
 assert writer.finish()['status']=='complete'


def test_borrowed_camera_readers_survive_source_cleanup():
 released=[]
 class Provider:
  def release_recording(self,token):released.append(token)
 class Camera:
  def close(self):pytest.fail('Recorder must never close a borrowed preview reader')
 source=HardwareSource(camera_provider=Provider());source.camera_borrow_token='token';source.cameras={'top':Camera(),'wrist':Camera()}
 assert source.close()==[]
 assert released==['token'] and source.cameras=={}


def test_camera_provider_prevents_preview_stop_until_recorder_releases(monkeypatch,tmp_path):
 from so101_lab import preview
 from test_preview import FakeCamera
 monkeypatch.setattr(preview,'CameraCapture',FakeCamera);monkeypatch.setattr(preview,'assert_hardware_available',lambda paths:None)
 actual=preview.ResourceLease;monkeypatch.setattr(preview,'ResourceLease',lambda name:actual('test-provider-'+tmp_path.name))
 pv=preview.CameraPreview();pv.start(explicit_user_start=True)
 token,cameras=pv.acquire_for_recording(hardware_settings())
 with pytest.raises(RuntimeError,match='используются'):pv.stop()
 assert pv.status()['active']
 pv.release_recording(token);pv.stop();assert not pv.status()['active']


def test_sparse_preview_reads_only_last_complete_json_line(tmp_path):
 from so101_lab.scene import _last_committed_frame
 p=tmp_path/'frames.jsonl';p.write_bytes(b'{"frame_id":1}\n{"frame_id":2}\n{"frame_')
 assert _last_committed_frame(p)['frame_id']==2


def test_recovery_counts_only_complete_raw_rows_without_making_episode_trainable(tmp_path):
 m=RecordingManager(tmp_path);d=m.create_dataset('recovery','technical only')
 episode=Path(d['path'])/'.partial'/'crash.partial';episode.mkdir(parents=True)
 (episode/'episode.json').write_text(json.dumps({'episode_id':'crash','status':'recording','frames':0,'writer_pid':99999999,'writer_process_start_ticks':'0'}))
 (episode/'frames.jsonl').write_text('{"frame_id":1,"timestamp_ns":100}\n{"frame_id":2,"timestamp_ns":200}\n{"incomplete"')
 recovered=m.recover_incomplete();assert len(recovered)==1
 info=json.loads((Path(recovered[0])/'episode.json').read_text())
 assert info['status']=='incomplete' and info['frames']==2 and info['invalid_json_lines']==1
 assert info['first_timestamp_ns']==100 and info['last_timestamp_ns']==200
 assert m.dataset_info(d['dataset_id'])['episodes']==0


def test_mlflow_status_exposes_buffering_after_a_run_was_already_created(tmp_path):
 m=RecordingManager(tmp_path);spool=tmp_path/'spool';spool.mkdir()
 (spool/'run_info').write_text(json.dumps({'run_id':'real-run','experiment_id':'1'}))
 (spool/'one.json').write_text('{}');(spool/'last_error.txt').write_text('Connection refused')
 m._update(mlflow={'spool':str(spool),'state':'connected'})
 state=m.status()['mlflow'];assert state['run_id']=='real-run'
 assert state['state']=='buffering' and state['pending_events']==1


def test_resource_inventory_tracks_stable_camera_paths_instead_of_video_numbers(monkeypatch,tmp_path):
 from so101_lab import hardware,resources
 top=tmp_path/'video42';wrist=tmp_path/'video91';top.touch();wrist.touch()
 monkeypatch.setattr(hardware,'DEFAULT_CAMERAS',{'top':str(top),'wrist':str(wrist)})
 seen=[]
 def owners(paths):
  seen.extend(paths);return [{'pid':12345,'name':'test_owner','devices':[str(top)]}]
 monkeypatch.setattr(resources,'owners',owners)
 state=resources.snapshot()
 assert str(top) in seen and str(wrist) in seen
 assert state['device_inventory']['top']['state']=='BUSY'
 assert state['device_inventory']['wrist']['state']=='AVAILABLE'
 assert state['device_inventory']['top']['resolved_path']==str(top)


@pytest.mark.parametrize('camera_fault',['stale','missing','read_error'])
def test_camera_failure_refuses_the_next_leader_read_and_motor_command(camera_fault):
 calls=[]
 class Bus:
  def sync_read(self,*args,**kwargs):return {n:0. for n in JOINTS}
  def sync_write(self,*args,**kwargs):calls.append('motor_command')
 class Leader(Bus):
  def sync_read(self,*args,**kwargs):calls.append('leader_read');return super().sync_read(*args,**kwargs)
 class Camera:
  def latest_before(self,cutoff):
   if camera_fault=='read_error':raise RuntimeError('camera reader failed')
   if camera_fault=='missing':return None
   return {'timestamp_ns':cutoff-1_000_000_000,'sequence':1}
 source=HardwareSource();source.started=True;source.buses={'leader':Leader(),'follower':Bus()};source.cameras={'top':Camera()}
 with pytest.raises(RuntimeError):source.sample(1)
 assert calls==[]
