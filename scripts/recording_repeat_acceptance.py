"""Repeated API workflow and Start/STOP races on own synthetic-only child backend."""
import concurrent.futures,json,socket,subprocess,time,urllib.request,urllib.error
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
def main():
 root=ROOT/'runtime'/('recording-repeat-'+str(time.time_ns()));root.mkdir()
 with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
 base=f'http://127.0.0.1:{port}'
 def request(path,data=None):
  body=json.dumps(data).encode() if data is not None else None
  with urllib.request.urlopen(urllib.request.Request(base+path,data=body,headers={'Content-Type':'application/json'}),timeout=15) as r:return json.load(r)
 def wait(predicate,seconds=30):
  deadline=time.monotonic()+seconds
  while time.monotonic()<deadline:
   try:
    result=predicate()
    if result:return result
   except (OSError,ValueError):pass
   time.sleep(.05)
  raise AssertionError('QA wait deadline')
 log=(root/'server.log').open('a');proc=subprocess.Popen([str(ROOT/'.venv/bin/python'),str(ROOT/'scripts/recording_qa_server.py'),'--root',str(root),'--port',str(port)],cwd=ROOT,stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT)
 try:
  wait(lambda:request('/api/status'));dataset=request('/api/datasets',{'name':'TECHNICAL repeated sessions','task':'No hardware, fault regression','source':'synthetic_test'});did=dataset['dataset_id']
  for cycle in range(6):
   start={'dataset_id':did,'episode_seconds':30,'preparation_seconds':15,'auto_advance':False,'keep_open':True,'fps':30}
   request('/api/record/start',start);wait(lambda:request('/api/status')['recording']['state']=='preparing')
   request('/api/record/begin',{});wait(lambda:request('/api/status')['recording'].get('frames_written',0)>=3)
   request('/api/record/end',{});wait(lambda:request('/api/status')['recording']['state']=='ready')
   if cycle%2==0:
    request('/api/record/rerecord',{});wait(lambda:request('/api/status')['recording']['state']=='preparing')
    request('/api/record/begin',{});wait(lambda:request('/api/status')['recording'].get('frames_written',0)>=3)
    request('/api/record/end',{});wait(lambda:request('/api/status')['recording']['state']=='ready')
   request('/api/record/stop',{});wait(lambda:not request('/api/status')['recording']['active'])
  episodes=request('/api/episodes/'+did);assert sum(e['storage']=='episodes' for e in episodes)==6;assert sum(e['storage']=='quarantine' for e in episodes)==3
  race_results=[]
  for index in range(8):
   generation=request('/api/status')['hardware_stop_generation']
   def race_start():
    try:return request('/api/record/start',{'dataset_id':did,'preparation_seconds':120,'expected_stop_generation':generation})['state']
    except urllib.error.HTTPError as e:assert e.code==409;return 'cancelled_by_STOP'
   with concurrent.futures.ThreadPoolExecutor(2) as pool:
    future=pool.submit(race_start);request('/api/record/stop',{});race_results.append(future.result())
   wait(lambda:not request('/api/status')['recording']['active'])
   assert not list((Path(dataset['path'])/'.partial').glob('*.partial'))
  request('/api/record/start',{'dataset_id':did,'episode_seconds':30,'fps':30,'keep_open':True})
  active=wait(lambda:(s if (s:=request('/api/status')['recording']).get('mlflow',{}).get('run_id') and s.get('frames_written',0)>30 else None),45)
  from mlflow.tracking import MlflowClient
  client=MlflowClient('http://127.0.0.1:5020');run_id=active['mlflow']['run_id']
  live=wait(lambda:(r if (r:=client.get_run(run_id)).data.metrics.get('recording/frames_written',0)>0 else None),30)
  assert live.info.status=='RUNNING';assert request('/api/status')['recording']['active']
  request('/api/record/stop',{});wait(lambda:not request('/api/status')['recording']['active'])
  wait(lambda:client.get_run(run_id).info.status=='FINISHED',45)
  report={'status':'PASS','hardware_opened':False,'repeated_sessions':6,'saved_episodes_before_final_session':6,'rerecord_archives':3,'concurrent_Start_STOP_trials':8,'race_results':race_results,'MLflow_metric_read_while_recording_active':True,'MLflow_status_during_recording':live.info.status,'MLflow_run_id':run_id,'MLflow_final_status':'FINISHED','dataset':dataset['path']}
  (ROOT/'validation/recording-fix-20261107/repeat-and-live-mlflow.json').write_text(json.dumps(report,indent=2));print(json.dumps(report,indent=2))
 finally:
  if proc.poll() is None:proc.terminate();proc.wait(20)
  log.close()
if __name__=='__main__':main()
