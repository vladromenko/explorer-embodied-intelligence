import json,sys,time,traceback
from pathlib import Path
from .jobs import ROOT,DIR,atomic
from .resources import ResourceBusy,ResourceLease,assert_compute_available

def main():
    jid=sys.argv[1];d=DIR/jid;p=d/'job.json';obj=json.loads(p.read_text());options=obj['options'];stopped=lambda:(d/'stop').exists()
    def update(data=None,**kw):
        obj.update(kw)
        if data is not None:obj['progress']=data
        atomic(p,obj)
    lease=None;data_lease=None
    try:
        dataset=Path(obj['dataset'])
        if obj['kind'] in ('export','validate','finalize'):
            while not stopped():
                try:assert_compute_available('cpu',minimum_available_gb=1.5);break
                except ResourceBusy as e:update(state='waiting_resources',message=str(e));time.sleep(2)
            if stopped():update(state='stopped');return
            update(state='running',message='Проверка raw и подготовка LeRobot Dataset (CPU)')
            from .lerobot_export import export_dataset,validate_raw_dataset
            if obj['kind']=='finalize':
                from .recording import finalize_dataset_path
                result=finalize_dataset_path(dataset,allow_test_data=bool(options.get('allow_test_data')),progress=update,stop=stopped)
                if not result['finalized']:raise ValueError('Датасет не прошёл проверку: '+ '; '.join(result['errors'][:5]))
                if not stopped():result['lerobot_export']=export_dataset(dataset,options=options,progress=update,stop=stopped)
            elif obj['kind']=='export':result=export_dataset(dataset,options=options,progress=update,stop=stopped)
            else:result=validate_raw_dataset(dataset,allow_test_data=bool(options.get('allow_test_data')))
            update(state='stopped' if stopped() else 'completed',result=result);return
        if obj['kind']=='train':
            update(state='running',message='Проверка raw, аннотаций и выборок (CPU)')
            from .training_options import preview
            check=preview(dataset,options,public=False)
            if options.get('expected_split_sha256') and check['split']['sha256']!=options['expected_split_sha256']:raise ValueError('Выборки изменились после отправки, повторите проверку')
        while not stopped():
            try:
                assert_compute_available(options.get('device','cuda'),minimum_available_gb=options.get('minimum_available_gb',7))
                if obj['kind']=='annotate':
                    from .resources import dataset_lease
                    candidate=dataset_lease(dataset);candidate.__enter__();data_lease=candidate
                lease=ResourceLease('gpu-heavy');lease.__enter__();break
            except ResourceBusy as e:
                if data_lease:
                    data_lease.__exit__(None,None,None);data_lease=None
                if obj['kind'] in ('policy','policy_load'):raise
                update(state='waiting_resources',message=str(e));time.sleep(2)
        if stopped():update(state='stopped');return
        try:
            update(state='running',message='')
            if obj['kind']=='annotate':
                from .annotations import annotate_dataset
                result=annotate_dataset(dataset,options,progress=update,stop=stopped)
            elif obj['kind']=='train':
                from .training import train
                output=Path(options['output_dir'])/jid if options.get('output_dir') else ROOT/'models'/jid
                result=train(dataset,output,options,progress=update,stop=stopped)
            elif obj['kind']=='policy':
                from .inference import run_policy
                result=run_policy(dataset,options,progress=update,stop=stopped)
            elif obj['kind']=='policy_load':
                from .inference import check_checkpoint
                result=check_checkpoint(dataset,options,progress=update,stop=stopped)
            else:raise ValueError('Unsupported job kind')
            update(state='stopped' if stopped() else 'completed',result=result)
        finally:
            if lease:lease.__exit__(None,None,None)
            if data_lease:data_lease.__exit__(None,None,None)
    except Exception as e:update(state='failed',error=str(e),traceback=traceback.format_exc());traceback.print_exc()
if __name__=='__main__':main()
