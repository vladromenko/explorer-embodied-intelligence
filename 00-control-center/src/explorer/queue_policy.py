"""ENGINEERING_GLUE: pure shared queue budgets, dependencies and fair admission.

Reservations are conservative scheduling budgets, not measured peaks or OS
limits. Host memory includes Jetson UMA; no controller/power setting is touched.
"""
import math
import time

GIB=1024**3
HEAVY_JOBS=('sim_pilot','train_act','rosmaster_skill_train','so101_skill_train','world_model','dagger','skill_cycle',
    'manipulation','manipulation_student','manipulation_benchmark','manipulation_curriculum','mobile_manipulation',
    'manipulation_selective_repair','policy_transfer','fault_repair')
PHYSICAL_JOBS=('station_rollout','handover_physical','so101_skill_eval')
INFERENCE_JOBS=('station_shadow','station_rollout','so101_skill_shadow','rosmaster_skill_shadow','cooperative_policy_warm','so101_skill_eval')
DATA_JOBS=('station_import','mobile_import','retarget_import','camera_recording','program_trace','research_export','study_export')
TERMINAL=('completed','failed','cancelled')


def default_metadata(job):
    config=job.get('config') or {};heavy=job['kind'] in HEAVY_JOBS;inference=job['kind'] in INFERENCE_JOBS
    source_shadow=job['kind'] in ('so101_skill_shadow','rosmaster_skill_shadow');source_eval=job['kind']=='so101_skill_eval'
    warm=job['kind']=='cooperative_policy_warm';study=job['kind']=='study_export';export=job['kind'] in ('research_export','study_export')
    gpu=False if source_shadow or source_eval or warm or export else config.get('device')=='cuda' or inference and config.get('device')!='cpu'
    size,batch=config.get('image_size',64),config.get('batch_size',2)
    measured_compact=job['kind']=='so101_skill_train' and type(size) is int and 32<=size<=64 and type(batch) is int and 1<=batch<=2
    memory=(4 if warm else 2 if study else .5 if export else 2 if source_shadow or source_eval else 4 if gpu or heavy and not measured_compact else 2 if heavy else .75 if inference else .25)*GIB
    payload=config.get('estimated_payload_bytes');estimated_export=export and type(payload) is int and payload>=0
    disk=int((2 if heavy else .25)*GIB)+(3*payload if estimated_export else 0)
    budget=dict(cpu_cores=4 if warm else 2 if heavy or source_shadow or source_eval else 1,memory_bytes=int(memory),disk_bytes=disk,gpu_slots=int(gpu))
    return dict(schema='explorer_job_scheduling_v1',job_id=job['id'],experiment_slug=job.get('experiment_slug','unresolved:'+job['project_id']),
        project_id=job['project_id'],experiment_numbering_version=2,skill_id=config.get('skill_id'),skill_version=config.get('skill_version'),
        backend=config.get('backend','act' if job['kind'] in ('train_act','rosmaster_skill_train','so101_skill_train','manipulation_student','so101_skill_shadow','rosmaster_skill_shadow','cooperative_policy_warm','so101_skill_eval') else None),
        dataset_sha256=config.get('aggregate_manifest_sha256',config.get('dataset_manifest_sha256')),
        priority=0,preemptible=job['kind'] in HEAVY_JOBS,dependencies=[],not_before=None,resources=budget,
        resource_budget_basis='conservative_reservation_not_measured_peak',physical_execution_authorized=False)


def validate_options(job,previous,options):
    if not isinstance(options,dict) or set(options)-{'priority','preemptible','dependencies','not_before','resources'}:raise ValueError('Only scheduling priority/preemption/dependencies/time/budgets may be edited')
    metadata=dict(previous);metadata['resources']=dict(previous['resources'])
    if 'priority' in options:
        value=options['priority']
        if type(value) is not int or not -10<=value<=10:raise ValueError('Priority must be an integer from -10 to10')
        metadata['priority']=value
    if 'preemptible' in options:
        value=options['preemptible']
        if type(value) is not bool or job['kind'] in PHYSICAL_JOBS+INFERENCE_JOBS+('research_export','study_export') and value:raise ValueError('Native inference, export and physical jobs cannot become checkpoint-preemptible training')
        metadata['preemptible']=value
    if 'dependencies' in options:
        values=options['dependencies']
        if not isinstance(values,list) or len(values)>32 or any(not isinstance(value,str) or not value or len(value)>160 for value in values) or len(values)!=len(set(values)) or job['id'] in values:raise ValueError('Bounded unique existing job dependencies required; no self-dependency')
        metadata['dependencies']=list(values)
    if 'not_before' in options:
        value=options['not_before']
        if value is not None and (type(value) not in (int,float) or not math.isfinite(value) or value<0):raise ValueError('not_before must be finite Unix time or null')
        metadata['not_before']=value
    if 'resources' in options:
        values=options['resources'];minimum=default_metadata(job)['resources']
        if not isinstance(values,dict) or set(values)-{'cpu_cores','memory_bytes','disk_bytes'}:raise ValueError('GPU slot and source identity are server-derived')
        for key,value in values.items():
            upper={'cpu_cores':64,'memory_bytes':128*GIB,'disk_bytes':1024*GIB}[key]
            if type(value) is not int or not minimum[key]<=value<=upper:raise ValueError('Budget below server minimum or outside bound: '+key)
            metadata['resources'][key]=value
    return metadata


def dependency_reasons(metadata,jobs):
    reasons=[]
    for identity in metadata['dependencies']:
        source=jobs.get(identity)
        if source is None:reasons.append('dependency_missing:'+identity)
        elif source['status'] in ('failed','cancelled'):reasons.append('dependency_'+source['status']+':'+identity)
        elif source['status']!='completed':reasons.append('dependency_waiting:'+identity)
    return reasons


def queue_plan(jobs,schedules,resources,active=(),*,service_seconds=None,now=None):
    """One deterministic admission pass; no DB/process/device mutation.

    Aging is unbounded and fair-service charge is measured budget*time, not
    scientific optimizer compute. Paused SIGSTOP memory/GPU leases remain held.
    """
    now=time.time() if now is None else now;service_seconds=service_seconds or {};by_id={job['id']:job for job in jobs}
    reserve=2.5*GIB;cpu_capacity=max(1,resources['cpu_count']-min(2,max(0,resources['cpu_count']-1)))
    # Unsupported SIGSTOP pauses retain their resource lease, including the
    # CPU reservation needed to resume without silently exceeding concurrency.
    cpu_used=sum(item['resources']['cpu_cores'] for item in active)
    gpu_used=sum(item['resources']['gpu_slots'] for item in active)
    growth=sum(max(0,item['resources']['memory_bytes']-max(0,item.get('resident_memory_bytes',0))) for item in active)
    memory_left=resources['available_memory_bytes']-reserve-growth;disk_left=resources['free_disk_bytes']-GIB
    waiting={};ranked=[];running_ids={item['job_id'] for item in active}
    data_used=sum(by_id.get(item['job_id'],{}).get('kind') in DATA_JOBS for item in active)
    inference_used=sum(by_id.get(item['job_id'],{}).get('kind') in INFERENCE_JOBS for item in active)
    for job in jobs:
        if job['status'] in ('queued','resume_requested') and job['id'] not in running_ids:
            metadata=schedules[job['id']];age=metadata.get('_waiting_age_s',max(0,now-job['created']));role=metadata['experiment_slug']
            protected=30 if job['kind'] in PHYSICAL_JOBS else 20 if job['kind'] in INFERENCE_JOBS else 5 if job['kind'] in DATA_JOBS else 0
            score=protected+metadata['priority']+age/30-service_seconds.get(role,0)/30
            ranked.append((-score,job['created'],job['id'],job,metadata))
    starts=[];ranking=[]
    for negative,created,identity,job,metadata in sorted(ranked,key=lambda item:item[:3]):
        budget=metadata['resources'];reasons=dependency_reasons(metadata,by_id);ranking.append(identity)
        if job['kind'] in ('research_export','study_export'):
            estimate=(job.get('config') or {}).get('estimated_payload_bytes')
            if type(estimate) is not int or estimate<0:reasons.append(job['kind']+'_payload_estimate_missing_or_invalid')
        if metadata.get('not_before') is not None and now<metadata['not_before']:reasons.append('scheduled_time_not_reached')
        if budget['cpu_cores']>cpu_capacity-cpu_used:reasons.append('cpu_concurrency_budget_exhausted')
        if budget['cpu_cores']>cpu_capacity:reasons.append('job_CPU_budget_exceeds_host_worker_pool')
        if job['kind'] in DATA_JOBS and data_used>=1:reasons.append('single_ingest_disk_network_budget_occupied')
        if job['kind'] in INFERENCE_JOBS and inference_used>=1:reasons.append('single_interactive_inference_budget_occupied')
        if budget['gpu_slots']+gpu_used>1:reasons.append('single_heavy_GPU_or_inference_slot_occupied')
        if budget['memory_bytes']>memory_left:reasons.append('available_UMA_memory_below_reserve_plus_budgets')
        if budget['memory_bytes']+reserve>resources.get('total_memory_bytes',float('inf')):reasons.append('job_memory_budget_exceeds_host_UMA_capacity')
        if budget['disk_bytes']>disk_left:reasons.append('disk_reserve_plus_job_budget_unavailable')
        if budget['disk_bytes']+GIB>resources.get('total_disk_bytes',float('inf')):reasons.append('job_disk_budget_exceeds_host_storage_capacity')
        if job['kind'] in HEAVY_JOBS and resources.get('load_1m',0)>=max(1,resources['cpu_count']-1):reasons.append('host_cpu_reserve_busy')
        for zone in resources.get('thermal',[]):
            trip=zone.get('existing_hot_critical_trip_c')
            if trip is not None and zone['temperature_c']>=trip-5:reasons.append('near_existing_thermal_trip:'+zone['name'])
        if reasons:waiting[identity]=dict(reasons=reasons,effective_score=-negative,age_s=max(0,now-created))
        else:
            starts.append(identity);cpu_used+=budget['cpu_cores'];gpu_used+=budget['gpu_slots'];memory_left-=budget['memory_bytes'];disk_left-=budget['disk_bytes']
            data_used+=job['kind'] in DATA_JOBS;inference_used+=job['kind'] in INFERENCE_JOBS
    return dict(schema='explorer_shared_queue_plan_v1',start=starts,waiting=waiting,ranked=ranking,
        cpu_capacity=cpu_capacity,cpu_reserved_or_selected=cpu_used,gpu_reserved_or_selected=gpu_used,
        UMA_runtime_reserve_bytes=int(reserve),remaining_memory_budget_bytes=int(memory_left),resources_are_reservations_not_OS_limits=True,
        controller_settings_writes=0)
