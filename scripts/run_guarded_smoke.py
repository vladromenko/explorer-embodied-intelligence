"""Run own GPU-only acceptance child with a host RAM reserve. No hardware access."""
import argparse,json,subprocess,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
def available():return int(next(x.split()[1] for x in Path('/proc/meminfo').read_text().splitlines() if x.startswith('MemAvailable:')))*1024
def main():
    parser=argparse.ArgumentParser();parser.add_argument('kind',choices=['sam3','pretrained','pretrained_reload']);args=parser.parse_args()
    script=ROOT/'scripts'/('sam3_gpu_acceptance.py' if args.kind=='sam3' else 'pretrained_gpu_acceptance.py')
    out=ROOT/'validation/acceptance-20261106';report={'kind':args.kind,'status':'running','baseline_available_bytes':available(),'hardware_opened':False};minimum=available();peak_rss=0;began=time.monotonic()
    with (ROOT/'logs'/('acceptance-'+args.kind+'-gpu.log')).open('w') as log:
        command=[sys.executable,str(script),'--execute']+(['--reload'] if args.kind=='pretrained_reload' else [])
        p=subprocess.Popen(command,cwd=ROOT,stdout=log,stderr=subprocess.STDOUT)
        while p.poll() is None:
            minimum=min(minimum,available())
            try:peak_rss=max(peak_rss,int(Path(f'/proc/{p.pid}/statm').read_text().split()[1])*4096)
            except (OSError,IndexError):pass
            if minimum<1.3*1024**3 or time.monotonic()-began>900:
                report['status']='ABORTED_TO_PRESERVE_RAM';p.terminate()
                try:p.wait(timeout=10)
                except subprocess.TimeoutExpired:p.kill();p.wait()
            time.sleep(.1)
    report.update(exit_code=p.returncode,elapsed_seconds=time.monotonic()-began,minimum_available_bytes=minimum,peak_child_rss_bytes=peak_rss)
    if report['status']=='running':report['status']='PASS' if p.returncode==0 else 'FAIL'
    (out/(args.kind+'-guard.json')).write_text(json.dumps(report,indent=2));print(json.dumps(report,indent=2));return p.returncode or (0 if report['status']=='PASS' else 1)
if __name__=='__main__':raise SystemExit(main())
