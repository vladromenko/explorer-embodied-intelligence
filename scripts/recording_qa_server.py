"""Private loopback-only browser acceptance backend. Hardware factory forbidden."""
import argparse,contextlib,os
from pathlib import Path
import uvicorn
from so101_lab import app as web,recording,resources,jobs
from so101_lab.recording_telemetry import RecordingTelemetry,recover_recording_telemetry
PROJECT=Path(__file__).resolve().parents[1]
def main():
 p=argparse.ArgumentParser();p.add_argument('--root',required=True);p.add_argument('--port',type=int,default=8021);args=p.parse_args()
 root=Path(args.root).resolve();assert root.is_relative_to(PROJECT/'runtime')
 root.mkdir(parents=True,exist_ok=True)
 for name in ('runtime/jobs','logs','models'): (root/name).mkdir(parents=True,exist_ok=True)
 if not (root/'static').exists():(root/'static').symlink_to(PROJECT/'static',target_is_directory=True)
 web.ROOT=root;resources.ROOT=root;jobs.ROOT=root;jobs.DIR=root/'runtime/jobs'
 def forbidden(*a,**kw):raise RuntimeError('PHYSICAL HARDWARE FORBIDDEN in QA server')
 recording.HardwareSource=forbidden
 class Preview:
  def status(self):return {'active':False,'cameras':{}}
  def latest_frame(self,*a):return None
  def start(self,**kwargs):raise ValueError('QA: no physical cameras')
  def stop(self):return {'active':False}
 @contextlib.asynccontextmanager
 async def lifecycle(app):
  web.camera_preview=Preview();web.teleoperation=None
  web.manager=recording.RecordingManager(root,telemetry_factory=lambda *a:RecordingTelemetry(root,*a))
  recover_recording_telemetry(root)
  yield
  web.manager.command('stop');web.manager.wait(10)
  if getattr(web.manager,'telemetry',None):web.manager.telemetry.thread.join(5)
 web.app.router.lifespan_context=lifecycle
 uvicorn.run(web.app,host='127.0.0.1',port=args.port,log_level='warning')
if __name__=='__main__':main()
