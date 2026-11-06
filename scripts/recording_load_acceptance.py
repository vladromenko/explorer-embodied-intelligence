"""Sustained 2-camera 30FPS file replay + recording MLflow, no hardware access."""
import argparse,json,time,threading
from pathlib import Path
import numpy as np
from PIL import Image
from so101_lab import recording,resources
from so101_lab.recording_telemetry import RecordingTelemetry
ROOT=Path(__file__).resolve().parents[1]
def main():
 p=argparse.ArgumentParser();p.add_argument('--seconds',type=float,default=120);p.add_argument('--execute',action='store_true');p.add_argument('--sam-preview',action='store_true');args=p.parse_args()
 if not args.execute:raise SystemExit('Explicit --execute required')
 target=ROOT/'runtime'/('recording-load-'+str(time.time_ns()));target.mkdir();resources.ROOT=target
 source=ROOT/'data/ds_3e9fb723b2f1475d/quarantine/ep_20261107T014016_576641ba3d/images'
 images={c:np.array(Image.open(next((source/c).glob('*.png'))).convert('RGB')) for c in ('top','wrist')}
 class Replay(recording.SyntheticSource):
  def sample(self,fid):
   sample=super().sample(fid)
   for name,frame in sample['images'].items():frame['image']=images[name].copy();frame['historical_file_replay']=True
   return sample
 recording.SyntheticSource=Replay
 def forbidden(*a,**kw):raise RuntimeError('Hardware forbidden in load test')
 recording.HardwareSource=forbidden
 telemetry=[]
 def factory(*a):
  result=RecordingTelemetry(target,*a);telemetry.append(result);return result
 manager=recording.RecordingManager(target,telemetry_factory=factory)
 dataset=manager.create_dataset('TECHNICAL real RGB replay — no robot','Technical throughput acceptance',source='synthetic_test')
 manager.start(dataset['dataset_id'],fps=30,episode_count=2,episode_seconds=args.seconds/2,preparation_seconds=0)
 job=None
 if args.sam_preview:
  from so101_lab import jobs
  job=jobs.submit('sam_watch',Path(dataset['path']),{'device':'cuda','session_id':manager.status()['session_id'],'recording_status_path':str(target/'runtime/recording.json'),'technical_test':True,'job_name':'technical concurrent RAW SAM'})
  manager._update(sam_observer=job)
 start=time.monotonic();peak=0;last_notice=0
 while time.monotonic()-start<args.seconds+90:
  s=manager.status();peak=max(peak,s.get('writer_queue',0))
  if s['state']=='failed':raise RuntimeError(s.get('error'))
  if s['state']=='ready':manager.command('next')
  if not s['active']:break
  if time.monotonic()-last_notice>10:
   print(json.dumps({'elapsed':round(time.monotonic()-start,1),'state':s['state'],'frames':s.get('frames_written'),'queue':s.get('writer_queue'),'peak':peak}),flush=True);last_notice=time.monotonic()
  time.sleep(.1)
 else:manager.command('stop');raise RuntimeError('Load test deadline')
 manager.wait()
 for t in telemetry:t.thread.join(25)
 sam_result=None
 if job:
  deadline=time.monotonic()+180
  while time.monotonic()<deadline:
   sam_result=next(j for j in jobs.list_jobs() if j['id']==job['id'])
   if sam_result['state'] not in jobs.ACTIVE:break
   time.sleep(.25)
  assert sam_result['state']=='completed',sam_result
  assert sam_result.get('result',{}).get('sample_count',0)>0,sam_result
 info=manager.dataset_info(dataset['dataset_id']);assert info['episodes']==2
 final=recording.finalize_dataset_path(Path(dataset['path']),allow_test_data=True);assert final['finalized'],final['errors']
 assert not final['training_eligible']
 report={'status':'PASS','technical_file_replay':True,'physical_test':False,'hardware_opened':False,'elapsed_seconds':time.monotonic()-start,'peak_writer_queue':peak,'dataset':dataset['path'],'frames':final['frames'],'episodes':2,'sam_preview_job':sam_result,'raw_validation':final,'mlflow':json.loads((Path(dataset['path'])/'sessions'/manager.status()['session_id']/'telemetry.json').read_text())}
 from so101_lab.lerobot_export import export_dataset
 report['official_export']=export_dataset(Path(dataset['path']),options={'allow_test_data':True})
 (ROOT/('validation/recording-fix-20261107/concurrent-sam-load-test.json' if args.sam_preview else 'validation/recording-fix-20261107/load-test.json')).write_text(json.dumps(report,ensure_ascii=False,indent=2));print(json.dumps({'status':report['status'],'frames':report['frames'],'peak_queue':peak,'dataset':dataset['path']},ensure_ascii=False),flush=True)
if __name__=='__main__':main()
