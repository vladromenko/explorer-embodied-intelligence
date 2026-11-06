"""Real MLflow unreachable→same-URI proxy restore; raw test recording remains intact."""
import json,time,threading,urllib.request,urllib.error,socket
from http.server import ThreadingHTTPServer,BaseHTTPRequestHandler
from pathlib import Path
from so101_lab import recording,resources
from so101_lab.recording_telemetry import RecordingTelemetry
ROOT=Path(__file__).resolve().parents[1]
def main():
 root=ROOT/'runtime'/('recording-outage-'+str(time.time_ns()));root.mkdir();resources.ROOT=root
 with socket.socket() as probe:probe.bind(('127.0.0.1',0));port=probe.getsockname()[1]
 trackers=[]
 def factory(*args):
  tracker=RecordingTelemetry(root,*args,tracking_uri=f'http://127.0.0.1:{port}');trackers.append(tracker);return tracker
 manager=recording.RecordingManager(root,telemetry_factory=factory);d=manager.create_dataset('TECHNICAL MLflow outage','No hardware, network fault test')
 manager.start(d['dataset_id'],fps=20,episode_seconds=2,width=160,height=120)
 manager.wait(15);assert manager.status()['state']=='stopped'
 trackers[0].thread.join(15)
 meta_file=next((Path(d['path'])/'sessions').glob('*/telemetry.json'));meta=json.loads(meta_file.read_text());spool=Path(meta['spool'])
 pending=len(list(spool.glob('*.json')));assert pending>0
 class Proxy(BaseHTTPRequestHandler):
  def log_message(self,*a):pass
  def do_GET(self):self.forward()
  def do_POST(self):self.forward()
  def do_PUT(self):self.forward()
  def forward(self):
   size=int(self.headers.get('Content-Length','0'));body=self.rfile.read(size) if size else None
   request=urllib.request.Request('http://127.0.0.1:5020'+self.path,data=body,method=self.command,headers={'Content-Type':self.headers.get('Content-Type','application/json')})
   try:
    with urllib.request.urlopen(request,timeout=10) as response:payload=response.read();status=response.status;content=response.headers.get('Content-Type','application/json')
   except urllib.error.HTTPError as response:payload=response.read();status=response.code;content='application/json'
   self.send_response(status);self.send_header('Content-Type',content);self.send_header('Content-Length',str(len(payload)));self.end_headers();self.wfile.write(payload)
 server=ThreadingHTTPServer(('127.0.0.1',port),Proxy);threading.Thread(target=server.serve_forever,daemon=True).start()
 from so101_lab.telemetry import DurableMLflow
 recovered=DurableMLflow.recover(spool)
 deadline=time.monotonic()+45
 while list(spool.glob('*.json')) and time.monotonic()<deadline:
  recovered.flush();time.sleep(.1)
 assert not list(spool.glob('*.json'))
 recovered=DurableMLflow.recover(spool)
 from mlflow.tracking import MlflowClient
 client=MlflowClient('http://127.0.0.1:5020');run=client.get_run(recovered.run_id);assert run.info.status=='FINISHED';assert run.data.params['purpose']=='recording'
 summary=next((Path(d['path'])/'sessions').glob('*/summary.json'));expected=json.loads(summary.read_text())
 downloaded=client.download_artifacts(recovered.run_id,'summary.json',str(root));assert json.loads(Path(downloaded).read_text())['session_id']==expected['session_id']
 metrics=client.get_metric_history(recovered.run_id,'recording/fps_observed');assert len(metrics)>=2
 report={'status':'PASS','hardware_opened':False,'raw_recording_completed_during_outage':True,'pending_before_restore':pending,'pending_after_restore':0,'same_tracking_URI_restored_by_local_proxy':True,'real_MLflow_run_id':recovered.run_id,'run_status':run.info.status,'metric_points':len(metrics),'artifact_readback_matches':True,'dataset':d['path']}
 (ROOT/'validation/recording-fix-20261107/mlflow-outage.json').write_text(json.dumps(report,indent=2));print(json.dumps(report,indent=2));server.shutdown()
if __name__=='__main__':main()
