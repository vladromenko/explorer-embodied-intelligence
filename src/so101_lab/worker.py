import json,sys,time,traceback
from pathlib import Path
from .jobs import ROOT,DIR,atomic
from .resources import ResourceBusy,ResourceLease,assert_compute_available

def main():
    jid=sys.argv[1]; d=DIR/jid;p=d/'job.json';obj=json.loads(p.read_text());options=obj['options']; stopped=lambda:(d/'stop').exists()
    def update(data=None,**kw):
        obj.update(kw)
        if data is not None: obj['progress']=data
        atomic(p,obj)
    lease=None
    try:
        while not stopped():
            try:
                assert_compute_available(options.get('device','cuda'),minimum_available_gb=options.get('minimum_available_gb',7))
                lease=ResourceLease('gpu-heavy');lease.__enter__();break
            except ResourceBusy as e:
                if obj['kind'] in ('policy','policy_load'): raise
                update(state='waiting_resources',message=str(e));time.sleep(5)
        if stopped(): update(state='stopped');return
        try:
            update(state='running',message='')
            dataset=Path(obj['dataset'])
            if obj['kind']=='annotate':
                from .annotations import annotate_dataset
                result=annotate_dataset(dataset,options,progress=update,stop=stopped)
            elif obj['kind']=='train':
                from .training import train
                result=train(dataset,ROOT/'models'/jid,options,progress=update,stop=stopped)
            elif obj['kind']=='policy':
                from .inference import run_policy
                result=run_policy(dataset,options,progress=update,stop=stopped)
            elif obj['kind']=='policy_load':
                from .inference import load_checkpoint
                policy=load_checkpoint(dataset,device=options.get('device','cpu'))
                result={'checkpoint':str(dataset),'contract':policy.contract.to_dict(),'loaded':True,'physical_actions':False}
            else: raise ValueError('Unsupported job kind')
            update(state='stopped' if stopped() else 'completed',result=result)
        finally:
            if lease:lease.__exit__(None,None,None)
    except Exception as e: update(state='failed',error=str(e),traceback=traceback.format_exc());traceback.print_exc()
if __name__=='__main__': main()
