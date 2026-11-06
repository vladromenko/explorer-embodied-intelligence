"""Kill/restart only a child synthetic QA recorder. No hardware constructors allowed."""
import json,os,signal,socket,subprocess,time,urllib.request
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
def main():
 root=ROOT/'runtime'/('recording-crash-'+str(time.time_ns()));root.mkdir()
 with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
 base=f'http://127.0.0.1:{port}'
 def request(path,data=None):
  body=json.dumps(data).encode() if data is not None else None
  with urllib.request.urlopen(urllib.request.Request(base+path,data=body,headers={'Content-Type':'application/json'}),timeout=10) as response:return json.load(response)
 def wait(predicate,seconds=30):
  deadline=time.monotonic()+seconds
  while time.monotonic()<deadline:
   try:
    value=predicate()
    if value:return value
   except (OSError,ValueError):pass
   time.sleep(.1)
  raise AssertionError('QA deadline')
 log=(root/'server.log').open('a');child=None
 def launch():
  proc=subprocess.Popen([str(ROOT/'.venv/bin/python'),str(ROOT/'scripts/recording_qa_server.py'),'--root',str(root),'--port',str(port)],cwd=ROOT,stdout=log,stderr=subprocess.STDOUT)
  wait(lambda:request('/api/status'))
  return proc
 try:
  child=launch()
  dataset=request('/api/datasets',{'name':'TECHNICAL crash recovery','task':'No hardware, synthetic only','source':'synthetic_test'})
  request('/api/record/start',{'dataset_id':dataset['dataset_id'],'episode_seconds':60,'fps':30,'keep_open':True})
  wait(lambda:request('/api/status')['recording'].get('frames_written',0)>30)
  meta_file=wait(lambda:next((Path(dataset['path'])/'sessions').glob('*/telemetry.json'),None))
  meta=json.loads(meta_file.read_text());spool=Path(meta['spool'])
  child.kill();child.wait(10);child=launch()
  state=request('/api/status')['recording'];assert not state['active'];assert state['state']=='idle';assert state['recovered_episodes']
  assert not list((Path(dataset['path'])/'.partial').glob('*.partial'))
  episodes=request('/api/episodes/'+dataset['dataset_id']);assert len(episodes)==1
  episode=episodes[0];assert episode['storage']=='quarantine';assert episode['metadata']['status']=='incomplete';assert len(episode['frames'])>=15
  wait(lambda:json.loads(meta_file.read_text())['status']=='FAILED')
  wait(lambda:(spool/'run_id').exists(),45)
  from mlflow.tracking import MlflowClient
  client=MlflowClient('http://127.0.0.1:5020');run_id=(spool/'run_id').read_text()
  wait(lambda:client.get_run(run_id).info.status=='FAILED',45)
  run=client.get_run(run_id);assert run.data.params['source']=='synthetic_test'
  report={'status':'PASS','hardware_opened':False,'killed_only_child_QA_server':True,'raw_frames_recovered':len(episode['frames']),'archive_status':episode['metadata']['status'],'recording_after_restart':state['state'],'auto_motion_resume':False,'real_MLflow_run_id':run_id,'MLflow_status':run.info.status,'dataset':dataset['path']}
  (ROOT/'validation/recording-fix-20261107/crash-recovery.json').write_text(json.dumps(report,indent=2));print(json.dumps(report,indent=2))
 finally:
  if child and child.poll() is None:child.terminate();child.wait(15)
  log.close()
if __name__=='__main__':main()
