"""MLflow network/imports in a separate low-priority process, never the servo process."""
import os,sys,time,json
from pathlib import Path
from .telemetry import DurableMLflow

def main():
    path=Path(sys.argv[1]).resolve();root=Path(__file__).resolve().parents[2]
    if not path.is_relative_to(root):raise ValueError('Recording spool outside laboratory')
    os.nice(10)
    telemetry=DurableMLflow.recover(path)
    failures=0
    while True:
        owner_file=path/'recording.owner'
        if owner_file.exists() and not (path/'recording.finished').exists():
            owner=json.loads(owner_file.read_text());proc=Path('/proc')/str(owner['pid'])
            try:alive=(proc/'stat').read_text().split()[21]==owner['start_ticks']
            except OSError:alive=False
            if not alive:
                has_status=any(json.loads(p.read_text()).get('kind')=='status' for pattern in ('*.json','*.sent') for p in path.glob(pattern))
                if not has_status:telemetry.close('FAILED',flush=False)
                (path/'recording.finished').touch()
        try:telemetry.flush();failures=0
        except Exception as exc:failures+=1;(path/'last_error.txt').write_text(str(exc))
        if (path/'recording.finished').exists() and not list(path.glob('*.json')):break
        if (path/'recording.finished').exists() and failures>=3:break  # Durable timer replays later; don't retain one process per failed session.
        time.sleep(1)
if __name__=='__main__':main()
