from pathlib import Path
import json,threading
from so101_lab.telemetry import DurableMLflow
root=Path(__file__).resolve().parents[1]
for p in (root/'runtime/mlflow-spool').glob('*'):
    if not list(p.glob('*.json')): continue
    obj=DurableMLflow.__new__(DurableMLflow);obj.path=p;obj.uri='http://127.0.0.1:5020';obj.name='recovered-'+p.name;obj.experiment='SO101 independent lab';obj.lock=threading.Lock();obj.run_id=(p/'run_id').read_text() if (p/'run_id').exists() else None
    try: obj.flush(); print(p.name,'delivered')
    except Exception as e: print(p.name,str(e))
