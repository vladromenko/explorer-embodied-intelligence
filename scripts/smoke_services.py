"""Real HTTP/WebSocket/MLflow technical checks; no hardware and no torque."""
import json,time,asyncio
from pathlib import Path
import httpx,websockets
from so101_lab.telemetry import DurableMLflow
ROOT=Path(__file__).resolve().parents[1];c=httpx.Client(base_url='http://127.0.0.1:8020',timeout=10)
# Startup readiness retries are read-only; never retry record/motion commands.
for _ in range(30):
    try:
        if c.get('/api/status').status_code==200:break
    except httpx.HTTPError:pass
    time.sleep(.5)
else:raise RuntimeError('UI startup readiness timeout')
def post(p,d):
    r=c.post(p,json=d);r.raise_for_status();return r.json()
r=post('/api/datasets',{'name':'Техническая проверка 51 — не демонстрации','task':'service smoke no hardware','source':'synthetic_test'});did=r['dataset_id']
r=post('/api/record/start',{'dataset_id':did,'episode_count':51,'episode_seconds':10,'fps':5});time.sleep(1)
async def ws():
    async with websockets.connect('ws://127.0.0.1:8020/ws') as w:
        s=json.loads(await w.recv());assert s['recording']['active'];return True
assert asyncio.run(ws())
time.sleep(.5);post('/api/record/end',{});time.sleep(.5)
post('/api/record/rerecord',{});time.sleep(.5);post('/api/record/end',{});time.sleep(.5)
post('/api/record/next',{});time.sleep(.5);post('/api/record/stop',{});time.sleep(1)
eps=c.get('/api/episodes/'+did).json();assert len(eps)>=2,eps
f=eps[0]['frames'][0];image=c.get('/api/files/'+did+'/episodes/'+eps[0]['id']+'/'+f['cameras']['top']['path']);assert image.status_code==200
s=c.get('/api/status').json();assert not s['recording']['active'];assert 'navigator.mediaDevices' not in (ROOT/'static/app.js').read_text()
telemetry=DurableMLflow(ROOT,'real-service-smoke',{'source':'synthetic_test','hardware':False});telemetry.log_metrics({'technical_frames':sum(len(x['frames']) for x in eps)},step=1)
artifact=ROOT/'validation/service-smoke.json';artifact.write_text(json.dumps({'dataset':did,'episodes':len(eps),'websocket_disconnect_recording_survived':True,'saved_image_http':image.status_code,'physical_test':False},indent=2));telemetry.log_artifact(artifact);telemetry.close();assert telemetry.run_id
import mlflow
mlflow.set_tracking_uri('http://127.0.0.1:5020')
from mlflow.tracking import MlflowClient
mc=MlflowClient(tracking_uri='http://127.0.0.1:5020');run=mc.get_run(telemetry.run_id);assert run.info.status=='FINISHED';assert run.data.metrics['technical_frames']>0
art=mc.download_artifacts(telemetry.run_id,'service-smoke.json',str(ROOT/'validation'));assert json.loads(Path(art).read_text())['dataset']==did
print(json.dumps({'dataset':did,'episodes':len(eps),'mlflow_run':telemetry.run_id,'artifact_reload':'passed'},indent=2))
