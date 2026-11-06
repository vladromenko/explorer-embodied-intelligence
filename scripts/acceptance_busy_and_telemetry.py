"""Integration failures with own inert lease/test source only, no GPU model/robot."""
import json,subprocess,sys,time
from pathlib import Path
import httpx
from so101_lab.resources import ROOT
from so101_lab.telemetry import DurableMLflow

def wait_for(fn,timeout=15):
    end=time.monotonic()+timeout
    while time.monotonic()<end:
        result=fn()
        if result:return result
        time.sleep(.2)
    raise RuntimeError('Integration state timeout')

def main():
    c=httpx.Client(base_url='http://127.0.0.1:8020',timeout=10)
    child=subprocess.Popen([sys.executable,'-c',"from so101_lab.resources import ResourceLease; import time; lease=ResourceLease('gpu_compute');lease.__enter__();time.sleep(45)"])
    try:
        wait_for(lambda:c.get('/api/status').json()['resources']['compute_conflicts'])
        d=c.post('/api/datasets',json={'name':'Acceptance busy technical source','task':'software fault test only','source':'synthetic_test'}).json()
        r=c.post('/api/record/start',json={'dataset_id':d['dataset_id'],'fps':5,'episode_seconds':30,'episode_count':1});r.raise_for_status()
        wait_for(lambda:c.get('/api/status').json()['recording'].get('frames_written',0)>=2)
        before=c.get('/api/status').json()['recording']['frames_written']
        j=c.post('/api/jobs',json={'kind':'annotate','dataset_id':d['dataset_id'],'options':{'device':'cuda'}});j.raise_for_status();jid=j.json()['id']
        def waiting():
            job=next(x for x in c.get('/api/status').json()['jobs'] if x['id']==jid)
            return job if job['state']=='waiting_resources' else None
        job=wait_for(waiting);time.sleep(.5);after=c.get('/api/status').json()['recording']['frames_written'];assert after>before
        c.post('/api/jobs/'+jid+'/stop',json={}).raise_for_status()
        wait_for(lambda:next(x for x in c.get('/api/status').json()['jobs'] if x['id']==jid)['state']=='stopped')
        c.post('/api/record/stop',json={}).raise_for_status();wait_for(lambda:not c.get('/api/status').json()['recording']['active'])
    finally:
        child.terminate();child.wait(timeout=5)
    t=DurableMLflow(ROOT,'acceptance-MLflow-retry-20261106',{'technical_test':True,'fault':'unreachable_server'},tracking_uri='http://127.0.0.1:59999');t.log_metrics({'recorded_frames_during_compute_busy':after-before},step=1);t.close();assert list(t.path.glob('*.json'))
    recovered=DurableMLflow.recover(t.path);recovered.uri='http://127.0.0.1:5020';recovered.flush();assert not list(t.path.glob('*.json'))
    result={'gpu_busy_job':'PASS','raw_recording_during_gpu_busy':'PASS','frame_count_before':before,'frame_count_after':after,'job_id':jid,'mlflow_durable_retry':'PASS','mlflow_run_id':recovered.run_id,'physical_hardware':False,'GPU_model_loaded':False}
    (ROOT/'validation/acceptance-20261106/busy-and-telemetry.json').write_text(json.dumps(result,indent=2));print(result)
if __name__=='__main__':main()
