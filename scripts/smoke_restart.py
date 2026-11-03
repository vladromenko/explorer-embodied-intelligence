"""Abrupt restart of own UI with synthetic recording only. Never touches foreign processes."""
import time,json,subprocess,httpx
from pathlib import Path
c=httpx.Client(base_url='http://127.0.0.1:8020',timeout=5)
d=c.post('/api/datasets',json={'name':'Тест аварии собственного UI','task':'synthetic restart only','source':'synthetic_test'}).json();r=c.post('/api/record/start',json={'dataset_id':d['dataset_id'],'episode_count':1,'episode_seconds':30,'fps':5});r.raise_for_status();time.sleep(1)
s=c.get('/api/status').json();assert s['recording']['source']=='synthetic_test' and not s['recording']['hardware_open']
subprocess.run(['systemctl','--user','kill','--kill-whom=main','--signal=KILL','so101-ai-learning-ui'],check=True)
for i in range(20):
 time.sleep(1)
 try:
  r=c.get('/api/status')
  if r.status_code==200:break
 except httpx.HTTPError:pass
else:raise RuntimeError('UI did not restart')
s=r.json();d2=next(x for x in s['datasets'] if x['dataset_id']==d['dataset_id']);assert d2['episodes']==0 and d2['quarantine_count']>=1;assert not s['recording']['active'];assert not s['recording']['hardware_open']
report={'abrupt_own_ui_restart':'passed','dataset':d['dataset_id'],'incomplete_quarantined':True,'motion_auto_resume':False,'source':'synthetic_test'}
Path('validation/restart-smoke.json').write_text(json.dumps(report,indent=2));print(report)
