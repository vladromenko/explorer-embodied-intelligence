import time,json
from pathlib import Path
import httpx
from so101_lab.telemetry import DurableMLflow
root=Path(__file__).resolve().parents[1];c=httpx.Client(base_url='http://127.0.0.1:8020',timeout=10)
s=c.get('/api/status').json(); assert s['resources']['compute_conflicts'], 'Run only while compute conflict exists'
did=next(d['dataset_id'] for d in s['datasets'] if d['source']=='synthetic_test')
j=c.post('/api/jobs',json={'kind':'annotate','dataset_id':did,'options':{'device':'cuda'}}).json();time.sleep(1)
j=next(x for x in c.get('/api/status').json()['jobs'] if x['id']==j['id']);assert j['state']=='waiting_resources',j
c.post('/api/jobs/'+j['id']+'/stop',json={}).raise_for_status();time.sleep(5)
j=next(x for x in c.get('/api/status').json()['jobs'] if x['id']==j['id']);assert j['state']=='stopped',j
# The hardware preflight is tested through real API, busy guarantees no opening.
h=c.post('/api/datasets',json={'name':'Аппаратный preflight — не запись','task':'busy refusal no motion','source':'hardware'});h.raise_for_status()
r=c.post('/api/record/start',json={'dataset_id':h.json()['dataset_id']});assert r.status_code==409,r.text
assert 'занят' in r.text
# Deliver durable event after initially unreachable tracking server.
t=DurableMLflow(root,'outage-recovery-technical',{'check':'delivery_retry'},tracking_uri='http://127.0.0.1:59999');t.log_metrics({'queue_wait_observed':1},0);t.close();assert list(t.path.glob('*.json'));t.uri='http://127.0.0.1:5020';t.flush();assert not list(t.path.glob('*.json'))
report={'queue_wait_stop':'passed','hardware_busy_api_refusal_before_open':'passed','mlflow_outage_replay':'passed','run_id':t.run_id,'physical_actions':False}
(root/'validation/queue-smoke.json').write_text(json.dumps(report,indent=2));print(report)
