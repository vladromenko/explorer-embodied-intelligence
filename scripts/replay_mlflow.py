from pathlib import Path
from so101_lab.telemetry import DurableMLflow
root=Path(__file__).resolve().parents[1]
for p in (root/'runtime/mlflow-spool').glob('*'):
    if p.is_dir() and list(p.glob('*.json')):
        obj=DurableMLflow.recover(p)
        try:obj.flush();print(p.name,'delivered')
        except Exception as e:print(p.name,str(e))
