#!/usr/bin/env python3
"""ENGINEERING_GLUE: full16 wrapper bound to the successful portable ONE proof.

The already qualified portable verifier, frozen V10 loop/helper/classes and old
data are unchanged. This wrapper freezes the ONE→batch lineage before copying
or starting any new model and retains every exact replay result.
"""
from __future__ import annotations
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess

from explorer_shared.contact_diagnosis import canonical,sha

REPO=Path(__file__).resolve().parents[1]
QUALIFIER_SHA='013587bcf9b05a36da65bb98a50d45a71b8b86d8885a5eecae6ba45a8ac43f3c'


class BatchRejected(ValueError):pass


def file_sha(path):
    path=Path(path)
    if path.is_symlink() or path.resolve()!=path.absolute() or not path.is_file():raise BatchRejected('Nonaliased regular batch evidence required')
    with path.open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()


def read(path,expected):
    if Path(path).stat().st_size>16*1024**2 or file_sha(path)!=expected:raise BatchRejected('Bounded exact batch evidence SHA required')
    return json.loads(Path(path).read_bytes())


def write(path,value):
    with Path(path).open('xb') as stream:stream.write(canonical(value));stream.flush();os.fsync(stream.fileno())


def one_proof(root,expected):
    root=Path(root).absolute();result=read(root/'qualification.json',expected)
    if not result.get('all_declared_cases_PASS') or len(result.get('children',[]))!=1:
        raise BatchRejected('Actual one-case numeric proof must pass before full16')
    child=result['children'][0];proof=child.get('qualification',{})
    if (child.get('exit_code')!=0 or child.get('reaped') is not True or proof.get('status')!='PASS' or
            proof.get('native',{}).get('bitwise_equal') is not True or proof.get('native',{}).get('different_float64_words')!=0 or
            proof.get('contacts_solver_JSONL_bitwise_equal') is not True or proof.get('contact_score_bitwise_equal') is not True or
            proof.get('Python_open_positive_control_blocked') is not True or proof.get('old_producer_Python_open_attempts')):
        raise BatchRejected('Exact native/contact/score and real old-Python-open ban ONE proof required')
    plan=read(root/'portable-plan.json',result['plan_sha256'])
    if plan.get('qualifier_sha256')!=QUALIFIER_SHA or len(plan.get('cases',[]))!=1:
        raise BatchRejected('Exact immutable original portable qualifier/one case required')
    return dict(root=str(root),qualification_sha256=expected,plan_sha256=result['plan_sha256'],
        child_qualification_sha256=child['qualification_sha256'],qualifier_sha256=QUALIFIER_SHA,
        trusted_current_sources=plan['trusted_current_sources'],producer_report_sha256=plan['producer_report_sha256'],
        C_open_scope=result['C_open_scope'],original_OS_closure='PARTIAL')


def original_qualifier():
    path=REPO/'scripts/qualify_contact_portable.py'
    if file_sha(path)!=QUALIFIER_SHA:raise BatchRejected('Previously qualified portable verifier changed')
    spec=importlib.util.spec_from_file_location('trusted_unchanged_portable_contact',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module


def wrapper_trust(commit):
    if subprocess.check_output(['git','-C',str(REPO),'rev-parse','HEAD'],text=True).strip()!=commit:
        raise BatchRejected('Batch current trusted Git commit differs')
    if subprocess.check_output(['git','-C',str(REPO),'status','--porcelain'],text=True).strip():raise BatchRejected('Clean trusted batch tree required')
    relative=Path(__file__).resolve().relative_to(REPO).as_posix()
    if sha(subprocess.check_output(['git','-C',str(REPO),'show',commit+':'+relative]))!=file_sha(__file__):
        raise BatchRejected('Batch wrapper must be exact committed current code')


def prepare(one,one_sha,producer,output,commit):
    evidence=one_proof(one,one_sha);wrapper_trust(commit);legacy=original_qualifier()
    staged=legacy.prepare(Path(producer),evidence['producer_report_sha256'],Path(output),commit,'',all_cases=True)
    plan=legacy.load_plan(output,staged['plan_sha256'])
    if plan['trusted_current_sources']!=evidence['trusted_current_sources'] or len(plan['cases'])!=16:
        raise BatchRejected('All16 must use unchanged source14 and the same producer cohort as ONE')
    batch=dict(schema='explorer_portable_contact_full16_frozen_v1',ONE=evidence,portable_plan_sha256=staged['plan_sha256'],
        qualifier_sha256=QUALIFIER_SHA,wrapper_sha256=file_sha(__file__),current_trusted_commit=commit,
        output_root=str(Path(output).absolute()),declared_cases=16,all_source14_unchanged=True,
        physical_io=False,learned_policy=False,full_task_success_claimed=False,
        numerical_comparison='all_native_float64_and_raw_contact_solver_and_score_exact_no_tolerance',
        original_OS_closure='PARTIAL',C_open_scope='strace_if_available_else_UNMEASURED',
        native_nodes=False,original_V9_reexecution=False)
    write(Path(output)/'portable-batch-plan.json',batch)
    return dict(plan_path=str(Path(output)/'portable-batch-plan.json'),plan_sha256=sha(canonical(batch)),
        portable_plan_sha256=staged['plan_sha256'],CAD_loaded=False,dynamics_steps=0,cases=16)


def coordinate(output,checksum):
    output=Path(output).absolute();batch=read(output/'portable-batch-plan.json',checksum)
    if batch['schema']!='explorer_portable_contact_full16_frozen_v1' or batch['output_root']!=str(output) or batch['wrapper_sha256']!=file_sha(__file__):
        raise BatchRejected('Exact frozen batch wrapper/root required')
    wrapper_trust(batch['current_trusted_commit']);legacy=original_qualifier()
    fresh_one=one_proof(batch['ONE']['root'],batch['ONE']['qualification_sha256'])
    if fresh_one!=batch['ONE']:raise BatchRejected('ONE proof changed before batch start')
    plan=legacy.load_plan(output,batch['portable_plan_sha256'])
    if len(plan['cases'])!=16 or plan['trusted_current_sources']!=batch['ONE']['trusted_current_sources']:
        raise BatchRejected('Frozen full16 source14 changed')
    legacy.coordinate(plan,batch['portable_plan_sha256'])
    raw=output/'qualification.json';result=legacy.read_json(raw);children=result['children']
    clocks=[child.get('qualification',{}).get('actual_caller_replay_clock') for child in children]
    unique=len(clocks)==16 and None not in clocks and len(set(clocks))==16
    receipt=dict(schema='explorer_portable_contact_full16_qualification_v1',frozen_batch_plan_sha256=checksum,
        source_ONE_qualification_sha256=batch['ONE']['qualification_sha256'],portable_qualification_sha256=file_sha(raw),
        all16_exact_reproduced=result['all_declared_cases_PASS'] and unique,all16_fresh_clock_UUIDs=unique,
        child_count=len(children),all_children_exit_zero_and_reaped=all(c['exit_code']==0 and c['reaped'] for c in children),
        results=result,qualifier_sha256=QUALIFIER_SHA,wrapper_sha256=file_sha(__file__),
        physical_io=False,learned_policy_executed=False,full_task_success_claimed=False,
        original_V9_outcomes_unchanged=True,original_OS_closure='PARTIAL',
        raw_measurement_hash_timing='V10_independent_first_registration_after_original_unit_exit_preserved',
        C_open_scope=result['C_open_scope'])
    write(output/'batch-qualification.json',receipt)
    print(canonical(dict(all16_exact_reproduced=receipt['all16_exact_reproduced'],child_count=len(children),
        receipt_sha256=sha(canonical(receipt)),physical_io=False,C_open_scope=receipt['C_open_scope'])).decode())


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--mode',choices=('prepare','coordinate'),required=True)
    parser.add_argument('--one-root',type=Path);parser.add_argument('--one-qualification-sha256');parser.add_argument('--producer',type=Path)
    parser.add_argument('--output',type=Path,required=True);parser.add_argument('--trusted-commit');parser.add_argument('--plan-sha256');args=parser.parse_args()
    if args.mode=='prepare':print(canonical(prepare(args.one_root,args.one_qualification_sha256,args.producer,args.output,args.trusted_commit)).decode())
    else:coordinate(args.output,args.plan_sha256)


if __name__=='__main__':main()
