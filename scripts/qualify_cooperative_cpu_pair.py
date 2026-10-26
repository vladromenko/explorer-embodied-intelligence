#!/usr/bin/env python3
"""Finite actual Torch CPU106→9 + ACT6 SIM pair qualification, never hardware.

Read original fixtures; copy SQLite pages before read-only admission queries.
No production allow_simulation switch, network, actuator or model-training path.
"""
import argparse
from copy import deepcopy
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import resource
import signal
import sqlite3
import sys
import threading
import time

ROOT=Path(__file__).resolve().parents[1]
for path in (ROOT/'shared',ROOT/'00-control-center/src',ROOT/'01-autonomous-learning/src'):
    sys.path.insert(0,str(path))

from explorer_shared.contracts import canonical,digest
from explorer_shared.learning.checkpoint_control import atomic_json


def sha(path):
    with Path(path).open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()


def original_sql_view(root,destination):
    """Copy stable original SQLite/WAL bytes without opening the original DB."""
    from explorer.store import Store
    from explorer.experiment_projection import ExperimentProjection
    root=Path(root).resolve();destination=Path(destination);destination.mkdir(mode=0o700)
    files=[root/name for name in ('research.sqlite3','research.sqlite3-wal') if (root/name).exists()]
    for path in files:
        if path.is_symlink() or not path.is_file() or path.stat().st_size>64*1024**2:raise ValueError('Bounded qualification fixture registry required')
    before={path.name:sha(path) for path in files}
    for path in files:
        (destination/path.name).write_bytes(path.read_bytes())
    if before!={path.name:sha(path) for path in files}:raise ValueError('Original fixture registry changed during snapshot')
    class ReadOnlyOriginal(Store):
        def __init__(self):
            self.root=root;self.path=destination/'research.sqlite3';self.experiment_projection=ExperimentProjection(root)
        def connect(self):
            db=sqlite3.connect(self.path.as_uri()+'?mode=ro',uri=True);db.row_factory=sqlite3.Row
            db.execute('PRAGMA query_only=ON');return db
    return ReadOnlyOriginal(),before


def qualify(native9_result,source6_root,source6_job,output):
    output=Path(output).resolve()
    if output.exists() or not output.parent.is_dir():raise ValueError('New private qualification directory with existing parent required')
    output.mkdir(mode=0o700)
    os.environ['HF_HUB_OFFLINE']='1';os.environ['TRANSFORMERS_OFFLINE']='1'
    blocked=[]
    def offline(event,args):
        if event in ('socket.connect','socket.bind','subprocess.Popen','os.system'):
            blocked.append(event);raise RuntimeError('Finite offline qualifier forbids network/process IO')
    sys.addaudithook(offline)
    def timed_out(signum,frame):raise TimeoutError('Finite dual CPU qualification exceeded120s')
    previous=signal.signal(signal.SIGALRM,timed_out);signal.alarm(120)
    import psutil
    import numpy as np
    from PIL import Image
    from explorer_shared.learning.rosmaster_skill_pipeline import RosmasterSkillPolicy,CanonicalCorpus
    from explorer.so101_shadow import load_shadow_policy,infer_shadow
    from explorer.so101_dataset_admission import verify_so101_aggregate,member
    import torch
    torch.set_num_threads(2)
    process=psutil.Process();measurements=[];done=threading.Event();started=time.monotonic()
    def sample():
        while not done.wait(.01):measurements.append(process.memory_info().rss)
    sampler=threading.Thread(target=sample,daemon=True);sampler.start();results=[]
    try:
        path=Path(native9_result).resolve();raw_result_sha=sha(path);result9=json.loads(path.read_bytes())
        manifest=Path(result9['model_manifest']);saved=json.loads(manifest.read_bytes())
        if sha(manifest)!=result9['model_manifest_sha256'] or saved.get('source_kind')!=['simulation']:
            raise ValueError('Exact archived SIMULATION106D model required for finite software qualification')
        if (saved.get('ACT9') or {}).get('state_dimension')!=106:raise ValueError('State9 and106 are not interchangeable')
        if json.loads((manifest.parent/'student_act/config.json').read_bytes()).get('device')!='cpu':raise ValueError('Archived CPU model config required')
        corpus=CanonicalCorpus(result9['source_manifest_path'])
        before_load=process.memory_info().rss;load_started=time.monotonic();policy9=RosmasterSkillPolicy(manifest,candidate='ACT9')
        loaded9=time.monotonic();rss9=process.memory_info().rss
        store,registry_sha=original_sql_view(source6_root,output/'registry-readonly-copy')
        training=store.job(source6_job)
        if not training or training['kind']!='so101_skill_train' or training['result'].get('source_kind')!='SIMULATION':
            raise ValueError('Actual original SIMULATION source6 training job required')
        outer_path,outer,view_path,view=verify_so101_aggregate(store,training['config'])
        episode=next(row for row in view['source_records'] if row['split']=='development')
        source=Path(episode['source_evidence']['source_manifest_path']);original=json.loads(source.read_bytes())
        if sha(source)!=episode['source_manifest_sha256']:raise ValueError('Original held-out SO101 manifest SHA differs')
        calibration=outer['source_calibration_metadata']['calibration_metadata']['follower']['calibration_sha256']
        camera=original['samples'][0]['frame']['camera_id']
        policy6=load_shadow_policy(store,source6_job,expected_camera_id=camera,expected_calibration_sha256=calibration,allow_simulation=True)
        loaded_both=time.monotonic();rss_both=process.memory_info().rss
        for policy in (policy9,policy6):
            if any(parameter.device.type!='cpu' for parameter in policy.policy.parameters()):raise ValueError('No GPU tensors permitted')
        development=corpus.development[0]
        for index in range(2):
            frame=development['frames'][index];sample9=deepcopy(frame['sample'])
            for field in ('action','target_action9','phase_label'):sample9.pop(field,None)
            admitted=development['report']['channels'];begin=time.monotonic()
            prediction9=policy9.predict(sample9,lambda name:corpus.read(development,name),
                verify_observation=lambda observation,model:deepcopy(admitted))
            elapsed9=time.monotonic()-begin
            sample6=original['samples'][index];frame6=sample6['frame'];jpeg=member(source.parent,frame6['file'])
            if sha(jpeg)!=frame6['sha256']:raise ValueError('Original held-out SO101 RGB changed')
            with Image.open(jpeg) as image:rgb=np.asarray(image.convert('RGB'))
            clock_domain=original['clock_domain'];boot=original['boot_id'];cutoff=sample6['dispatch_receipt']['monotonic_ns']
            observation={'camera_id':camera,'follower_calibration_sha256':calibration,'state_basis':original['state_basis'],
                'frame_id':frame6['frame_id'],'frame_sha256':frame6['sha256'],'decoded_rgb_sha256':hashlib.sha256(rgb.tobytes()).hexdigest(),
                'frame_clock_domain':frame6['clock_domain'],'state_clock_domain':sample6['clock_domain'],'source_cutoff_clock_domain':sample6['dispatch_receipt']['clock_domain'],
                'frame_boot_id':boot,'state_boot_id':boot,'source_cutoff_boot_id':boot,'frame_monotonic_ns':frame6['monotonic_ns'],
                'state_monotonic_ns':sample6['state_monotonic_ns'],'source_cutoff_monotonic_ns':cutoff,
                'source_cutoff_basis':'original_native_dispatch_time_for_offline_evaluation'}
            begin=time.monotonic();prediction6=infer_shadow(policy6,rgb,sample6['state'],valid_mask=sample6['source_valid_masks']['follower_measured'],observation=observation)
            elapsed6=time.monotonic()-begin
            actions={'giver':prediction9['action9'],'receiver':prediction6['predicted_native6']}
            if any(len(actions[role])!=dimension or not np.isfinite(actions[role]).all() for role,dimension in (('giver',9),('receiver',6))):
                raise ValueError('Actual separate finite9D/6D native predictions required')
            results.append({'index':index,'actions':actions,'elapsed_ms':{'giver':elapsed9*1000,'receiver':elapsed6*1000},
                'source_episode_ids':{'giver':development['descriptor']['source_episode_id'],'receiver':original['episode_id']},
                'source_frame_ids':{'giver':frame['rgb_record']['frame_id'],'receiver':frame6['frame_id']},
                'native9_prediction':prediction9,'native6_prediction':prediction6,'rss_bytes':process.memory_info().rss})
        corpus.recheck()
        for name,expected in training['result']['checkpoint_sha256'].items():
            if sha(Path(training['result']['checkpoint_dir'])/name)!=expected:raise ValueError('Original source6 checkpoint changed during qualification')
        if sha(path)!=raw_result_sha or registry_sha!={name:sha(Path(source6_root)/name) for name in registry_sha}:
            raise ValueError('Original archived fixture bytes changed during qualification')
        peak=max(measurements+[rss_both,process.memory_info().rss,int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)*1024])
        implementations={str(path.relative_to(ROOT)):_sha for path in [
            ROOT/'shared/explorer_shared/cooperative_policy_actor.py',ROOT/'00-control-center/src/explorer/cooperative_policy_job.py',
            ROOT/'00-control-center/src/explorer/cooperative_warm_service.py',ROOT/'shared/explorer_shared/learning/rosmaster_skill_pipeline.py',
            ROOT/'shared/explorer_shared/learning/rosmaster_skill_features.py',ROOT/'shared/explorer_shared/learning/act_policy.py',
            ROOT/'shared/explorer_shared/learning/act_backend.py',Path(__file__)] for _sha in [sha(path)]}
        receipt={'schema':'explorer_cooperative_cpu_pair_finite_qualification_v1','source_kind':'SIMULATION',
            'qualification_scope':'actual_dual_Torch_CPU_load_and_original_offline_inference_not_live_queue_or_handover',
            'native_profiles':{'giver':{'state_dimension':106,'action_dimension':9},'receiver':{'state_dimension':6,'action_dimension':6}},
            'models':{'giver_manifest_sha256':sha(manifest),'giver_checkpoint_sha256':saved['weights_sha256'],
                'receiver_checkpoint_sha256':training['result']['checkpoint_sha256'],'receiver_job_id':source6_job},
            'framework_versions':{name:importlib.metadata.version(name) for name in ('torch','torchvision','lerobot')},
            'implementation_sha256':implementations,'threads':torch.get_num_threads(),'load_ms':{'giver':(loaded9-load_started)*1000,
                'receiver_with_source_admission':(loaded_both-loaded9)*1000},'rss_bytes':{'before_load':before_load,'giver_loaded':rss9,
                'both_loaded':rss_both,'peak':peak},'within4GiB':peak<=4*1024**3,'predictions':results,'network_or_process_attempts':blocked,
            'original_fixture_registry_sha256':registry_sha,'originals_modified':False,'hardware_commands_dispatched':False,
            'physical_execution_authorized':False,'independent_full_task_success':False,'production_simulation_admission_enabled':False,
            'elapsed_s':time.monotonic()-started}
        atomic_json(output/'qualification.json',receipt)
        if peak>4*1024**3:raise ValueError('Actual dual loaded peak exceeds conservative4GiB compute budget; no qualification')
        return receipt
    finally:
        done.set();sampler.join(1);signal.alarm(0);signal.signal(signal.SIGALRM,previous)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--native9-result',required=True);parser.add_argument('--source6-state',required=True)
    parser.add_argument('--source6-job',required=True);parser.add_argument('--output-dir',required=True);args=parser.parse_args()
    result=qualify(args.native9_result,args.source6_state,args.source6_job,args.output_dir)
    print(json.dumps({key:result[key] for key in ('source_kind','within4GiB','rss_bytes','elapsed_s')}))
