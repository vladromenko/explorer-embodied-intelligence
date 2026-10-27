#!/usr/bin/env python3
"""ENGINEERING_GLUE: source-locked V10 contact development plots, no physics.

Four matched, previously inspected source cases per condition. Raw file bytes
are rehashed; independently recomputed statistics are displayed without native
state loading, model execution, inferential tests or generalization claims.
"""
from __future__ import annotations
import argparse
import csv
import hashlib
import importlib.metadata
import json
import math
import os
from pathlib import Path
import sys
import time

RECEIPT_SHA='2552d94ddaffca33d9669f3e4c0eceefd4416542a756641d4a20a58d51af85d5'
CONDITIONS=('captured-physics','quarter-timestep','thick-bottom-same-top','low-drop-rest')
RAW_BASIS='first_independent_registration_after_unit_exit_no_prior_producer_raw_hash_claim'


class PlotRejected(ValueError):pass


def canonical(value):return json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()
def sha(path):
    with Path(path).open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()
def read(path):
    path=Path(path)
    if path.is_symlink() or not path.is_file() or path.stat().st_size>2*1024**2:raise PlotRejected('Bounded regular input JSON required')
    return json.loads(path.read_bytes())
def write(path,value):
    with Path(path).open('xb') as stream:stream.write(canonical(value))


def finite(value):
    if type(value) not in (int,float) or not math.isfinite(value):raise PlotRejected('Finite measured number required')
    return float(value)


def flatten(report,cases):
    if (report.get('source_domain')!='SIMULATION' or report.get('unique_solver_cause')!='AMBIGUOUS' or
            any(report.get(name) is not False for name in ('H4_test','full_task_success_claimed','learned_policy_executed','optimizer_admission','physics_accuracy_validated','physical_io'))):
        raise PlotRejected('Development-only source interpretation changed')
    if len(cases)!=4 or len(set(cases))!=4:raise PlotRejected('Four matched inspected source cases required')
    rows={};expected={(case,condition) for case in cases for condition in CONDITIONS}
    for attempt in report['attempts']:
        pair=attempt['original_case'],attempt['condition']
        if pair not in expected or pair in rows:raise PlotRejected('Unexpected/duplicate matched case-condition pair')
        analysis=attempt['analysis'];predicates=analysis['terminal_predicates'];recovered=analysis['recomputed_contact_recovered']
        if set(predicates)!={'inside','supported','settled','other_contact_free'} or any(type(v) is not bool for v in predicates.values()) or type(recovered) is not bool or recovered!=all(predicates.values()):
            raise PlotRejected('Contact-only outcome/predicate mismatch')
        if attempt['raw_SHA_binding_basis']!=RAW_BASIS:raise PlotRejected('Raw producer/post-exit hash distinction changed')
        minimum=finite(analysis['minimum_pair_signed_distance_m']);bottom=finite(analysis['terminal_mean_bottom_world_Z_N'])
        rows[pair]=dict(original_case=pair[0],condition=pair[1],attempt_id=attempt['attempt_id'],
            original_V9_full_task_success=attempt['original_case_success'],contact_recovered=recovered,
            **{'terminal_'+key:value for key,value in predicates.items()},minimum_contact_signed_distance_m=minimum,
            maximum_recorded_contact_penetration_mm=1000*max(0.,-minimum),terminal_mean_bottom_world_Z_N=bottom,
            terminal_mean_floor_world_Z_N=finite(analysis['terminal_mean_floor_world_Z_N']),
            terminal_net_contact_world_Z_N=finite(analysis['terminal_net_world_Z_contact_N']),object_weight_N=finite(analysis['weight_N']),
            verified_substeps=analysis['verified_substeps'],native_rows=attempt['native']['rows'],native_state_size=attempt['native']['state_size'],
            result_sha256=attempt['result_sha256'],initial_manifest_sha256=attempt['initial_snapshot']['manifest_sha256'],
            substeps_sha256=attempt['raw_artifacts']['substeps.jsonl']['sha256'],native_sha256=attempt['raw_artifacts']['native-states.npy']['sha256'],
            raw_SHA_binding_basis=attempt['raw_SHA_binding_basis'])
    if set(rows)!=expected:raise PlotRejected('Incomplete matched development matrix')
    ordered=[rows[(case,condition)] for case in cases for condition in CONDITIONS];summary=[]
    for condition in CONDITIONS:
        counts=dict(both_recovered=0,gained_contact_recovery=0,lost_contact_recovery=0,both_not_recovered=0)
        for case in cases:
            baseline=rows[(case,CONDITIONS[0])]['contact_recovered'];current=rows[(case,condition)]['contact_recovered']
            key='both_recovered' if baseline and current else ('gained_contact_recovery' if current else ('lost_contact_recovery' if baseline else 'both_not_recovered'))
            counts[key]+=1
        recovered=sum(rows[(case,condition)]['contact_recovered'] for case in cases)
        if report['by_condition'][condition]!=dict(attempts=4,recovered=recovered):raise PlotRejected('Independent condition denominator/count differs')
        summary.append(dict(condition=condition,matched_source_cases=4,contact_recovered=recovered,contact_not_recovered=4-recovered,**counts))
    return ordered,summary


def verify_inputs(receipt_path,raw_root):
    receipt_path=Path(receipt_path).resolve();raw_root=Path(raw_root).resolve()
    if sha(receipt_path)!=RECEIPT_SHA:raise PlotRejected('Frozen public V10 receipt SHA differs')
    public=read(receipt_path);report_path=raw_root/'independent-report.json';protocol_path=raw_root/'frozen-protocol.json';campaign_path=raw_root/'campaign-results.json'
    expected={report_path:public['independent_report_sha256'],protocol_path:public['independent_report']['protocol_sha256'],campaign_path:public['independent_report']['producer_campaign_results_sha256']}
    locked={str(receipt_path):dict(sha256=RECEIPT_SHA,size_bytes=receipt_path.stat().st_size,role='public_receipt')}
    for path,value in expected.items():
        if path.is_symlink() or sha(path)!=value:raise PlotRejected('Frozen independent/protocol/campaign input changed')
        locked[str(path)]=dict(sha256=value,size_bytes=path.stat().st_size,role='registered_input')
    report=read(report_path);protocol=read(protocol_path)
    if report!=public['independent_report'] or [v['id'] for v in protocol['conditions']]!=list(CONDITIONS):raise PlotRejected('Independent receipt/protocol mismatch')
    cases=[value['original_scenario']['id'] for value in protocol['cases']];rows,summary=flatten(report,cases)
    expected_attempts={row['attempt_id'] for row in rows}
    if {p.parent.name for p in (raw_root/'attempts').glob('*/result.json')}!=expected_attempts:raise PlotRejected('Unreported/excluded raw attempt directory')
    total_raw_bytes=0
    for attempt in report['attempts']:
        identity=attempt['attempt_id']
        if Path(identity).name!=identity:raise PlotRejected('Attempt ID escaped its raw directory')
        folder=raw_root/'attempts'/identity;result_path=folder/'result.json'
        if sha(result_path)!=attempt['result_sha256']:raise PlotRejected('Raw producer result SHA changed')
        result=read(result_path);analysis=attempt['analysis'];outcome=result['outcome']
        if (result['attempt_id']!=identity or result['case']['id']!=attempt['original_case'] or result['condition']!=attempt['condition'] or
                result['physical_io'] is not False or result['full_task_success_claimed'] is not False or result['renderer_created'] is not False or
                result['native_steps']!=analysis['verified_substeps'] or outcome['contact_recovered']!=analysis['recomputed_contact_recovered'] or
                outcome['minimum_saved_contact_distance_m']!=analysis['minimum_pair_signed_distance_m'] or outcome['terminal_mean_bottom_signed_Z_N']!=analysis['terminal_mean_bottom_world_Z_N']):
            raise PlotRejected('Raw result differs from independent plotted statistics')
        locked[str(result_path)]=dict(sha256=attempt['result_sha256'],size_bytes=result_path.stat().st_size,role='raw_result')
        initial_path=folder/'initial-state/manifest.json';initial_sha=attempt['initial_snapshot']['manifest_sha256']
        if sha(initial_path)!=initial_sha or result['initial_state_manifest_sha256']!=initial_sha:raise PlotRejected('Producer initial manifest binding changed')
        locked[str(initial_path)]=dict(sha256=initial_sha,size_bytes=initial_path.stat().st_size,role='producer_bound_initial_manifest_only_not_payload_reexecution')
        for name,descriptor in attempt['raw_artifacts'].items():
            if Path(name).name!=name:raise PlotRejected('Raw source filename escaped attempt')
            path=folder/name;size=path.stat().st_size;total_raw_bytes+=size
            if path.is_symlink() or size!=descriptor['bytes'] or size>64*1024**2 or total_raw_bytes>512*1024**2 or sha(path)!=descriptor['sha256']:
                raise PlotRejected('Post-exit registered raw bytes changed or exceeded bound')
            locked[str(path)]=dict(sha256=descriptor['sha256'],size_bytes=size,role=RAW_BASIS)
    return rows,summary,cases,locked,protocol


def save_csv(path,rows):
    with Path(path).open('x',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=list(rows[0]),lineterminator='\n');writer.writeheader()
        for row in rows:writer.writerow({key:(format(value,'.17g') if type(value) is float else value) for key,value in row.items()})


def render(path,rows,cases):
    import matplotlib
    matplotlib.use('Agg',force=True)
    import matplotlib.pyplot as plt
    from matplotlib.colors import ListedColormap
    import numpy as np
    plt.rcParams.update({'font.family':'DejaVu Sans','font.size':8.5,'pdf.fonttype':42,'ps.fonttype':42,'axes.unicode_minus':True})
    lookup={(r['original_case'],r['condition']):r for r in rows}
    matrices=[np.asarray([[float(lookup[(case,cond)][field]) for cond in CONDITIONS] for case in cases]) for field in ('contact_recovered','maximum_recorded_contact_penetration_mm','terminal_mean_bottom_world_Z_N')]
    figure,axes=plt.subplots(1,3,figsize=(8.2,4.2),gridspec_kw={'width_ratios':[1,1,1]})
    figure.subplots_adjust(left=.15,right=.985,bottom=.33,top=.79,wspace=.14)
    titles=['(a) Contact-only recovery','(b) Max contact penetration','(c) Mean bottom force, world Z']
    labels=['Captured','Quarter\ndt','Thicker\nbottom','Low\ndrop']
    for index,(axis,data) in enumerate(zip(axes,matrices)):
        image=axis.imshow(data,aspect='auto',interpolation='nearest',cmap=ListedColormap(['#efb6b6','#8cc4b5']) if index==0 else ('YlOrRd' if index==1 else 'RdBu_r'),vmin=0 if index<2 else -35,vmax=1 if index==0 else (18 if index==1 else 35))
        axis.set_title(titles[index],fontsize=9.1,pad=10);axis.set_xticks(range(4),labels,fontsize=8)
        axis.set_yticks(range(4));axis.tick_params(axis='both',length=0,pad=5)
        axis.set_yticklabels([case.removeprefix('v9-').rsplit('-',1)[0]+'\n'+case.rsplit('-',1)[1] for case in cases] if index==0 else [])
        axis.set_xticks(np.arange(-.5,4,1),minor=True);axis.set_yticks(np.arange(-.5,4,1),minor=True);axis.grid(which='minor',color='white',linewidth=1.6);axis.tick_params(which='minor',bottom=False,left=False)
        for i in range(4):
            for j in range(4):
                value=data[i,j];text=('yes' if value else 'no') if index==0 else (f'{value:.1f}' if index==1 else f'{value:+.2f}')
                axis.text(j,i,text,ha='center',va='center',fontsize=9,color='white' if (index==1 and value>10) or (index==2 and abs(value)>20) else '#151515')
        if index:
            position=axis.get_position()
            color_axis=figure.add_axes([position.x0,position.y0-.17,position.width,.025])
            bar=figure.colorbar(image,cax=color_axis,orientation='horizontal')
            bar.set_label('mm; max(0, -min signed distance)' if index==1 else 'N; negative = downward on object',fontsize=7.6,labelpad=3)
            bar.ax.tick_params(labelsize=7.3,length=2)
        else:
            axis.text(.5,-.31,'Recovered: 2/4, 3/4, 4/4, 4/4',transform=axis.transAxes,ha='center',va='top',fontsize=8)
    figure.suptitle('Four matched development cases per condition — contact-only diagnostics',fontsize=11,y=.96)
    figure.text(.5,.025,'16 new attempts; 14,000 substeps are not independent trials. Recovery does not validate physics or a full skill.',ha='center',fontsize=8.1)
    figure.savefig(path/'contact_development_v10.pdf',metadata={'Title':'V10 matched development contact diagnostics','Author':'','Creator':'Matplotlib Agg source-locked engineering plot','CreationDate':None,'ModDate':None})
    figure.savefig(path/'contact_development_v10.png',dpi=300,metadata={'Software':'Matplotlib Agg','Description':'Four matched inspected development cases per condition; no physics-fidelity or task-mastery claim'})
    plt.close(figure)
    from matplotlib.font_manager import findfont,FontProperties
    font=Path(findfont(FontProperties(family='DejaVu Sans')))
    return dict(matplotlib=matplotlib.__version__,numpy=np.__version__,backend=matplotlib.get_backend(),
        matplotlib_source_sha256=sha(matplotlib.__file__),numpy_source_sha256=sha(np.__file__),
        font_path=str(font),font_sha256=sha(font))


def resource_state(unit):
    group=Path('/sys/fs/cgroup')/next(line.split('::',1)[1].lstrip('/') for line in Path('/proc/self/cgroup').read_text().splitlines() if line.startswith('0::'))
    quota,period=(group/'cpu.max').read_text().split()
    if group.name!=unit+'.service' or (group/'memory.max').read_text().strip()!=str(512*1024**2) or (group/'memory.swap.max').read_text().strip()!='0' or quota=='max' or int(quota)!=int(period):raise PlotRejected('Own CPU1/512MiB/swap0 plotting unit required')
    return dict(cgroup=str(group),memory_max_bytes=512*1024**2,memory_peak_bytes=int((group/'memory.peak').read_text()),swap_peak_bytes=int((group/'memory.swap.peak').read_text()),memory_events=(group/'memory.events').read_text(),cpu_quota_cores=1)


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--receipt',required=True,type=Path);parser.add_argument('--raw-root',required=True,type=Path);parser.add_argument('--output',required=True,type=Path);parser.add_argument('--unit',required=True)
    args=parser.parse_args();start=time.monotonic();resource_state(args.unit);script_sha=sha(__file__)
    if args.output.exists() or args.output.resolve().is_relative_to(args.raw_root.resolve()):raise PlotRejected('New output outside preserved original raw root required')
    args.output.mkdir(mode=0o700,parents=True);failure=None
    try:
        rows,summary,cases,locked,protocol=verify_inputs(args.receipt,args.raw_root)
        save_csv(args.output/'contact_development_v10.csv',rows);save_csv(args.output/'contact_development_v10_paired_counts.csv',summary)
        first=args.output/'render1';second=args.output/'render2';first.mkdir();second.mkdir();runtime=render(first,rows,cases);render(second,rows,cases)
        identical={name:sha(first/name)==sha(second/name) for name in ('contact_development_v10.pdf','contact_development_v10.png')}
        if not all(identical.values()):raise PlotRejected('Repeated current-environment render bytes differ')
        assert not {'mujoco','torch','OpenGL'}&set(sys.modules)
        if script_sha!=sha(__file__) or any(sha(path)!=row['sha256'] for path,row in locked.items()):raise PlotRejected('Plot source/input bytes changed during rendering')
        artifacts=[args.output/name for name in ('contact_development_v10.csv','contact_development_v10_paired_counts.csv')] + [folder/name for folder in (first,second) for name in ('contact_development_v10.pdf','contact_development_v10.png')]
        proof=dict(schema='explorer_contact_development_plot_provenance_v1',plot_script_sha256=script_sha,input_files=locked,public_receipt_sha256=RECEIPT_SHA,
            case_order=cases,condition_order=list(CONDITIONS),matched_source_cases_per_condition=4,attempts=16,total_verified_contact_substeps=sum(r['verified_substeps'] for r in rows),
            discarded_constructor_reset_steps=protocol['discarded_constructor_and_reset_native_steps_per_attempt']*len(rows),
            paired_counts=summary,uncertainty_scope='descriptive paired counts only; no population confidence interval or significance test for inspected nonrandom cases',
            new_contact_initial_conditions_not_original_V9_reexecution=True,heldout_generalization_test=False,original_V9_outcomes_unchanged=True,
            raw_SHA_binding_basis=RAW_BASIS,raw_bytes_rehashed_by_plotter=True,producer_raw_hash_claim_added=False,
            signed_opposing_floor_bottom_mechanism='OBSERVED_IN_NEW_BASELINE_CONTACTS',unique_solver_cause='AMBIGUOUS',physics_accuracy_validated=False,
            full_task_success_claimed=False,learned_repair_tested=False,learned_policy_executed=False,initial_restore_reexecution='UNQUALIFIED',original_runtime_OS_closure='PARTIAL',
            system_python=sys.executable,runtime=runtime,repeated_render_same_process_and_environment_only=True,repeated_render_bytes_equal=identical,scientific_archived_code_or_native_state_executed=False,
            output_sha256={str(p.relative_to(args.output)):sha(p) for p in artifacts},elapsed_s=time.monotonic()-start,physical_io=False)
        write(args.output/'provenance.json',proof)
        print(json.dumps(dict(paired_counts=summary,repeated_render_bytes_equal=identical,elapsed_s=proof['elapsed_s'])))
    except BaseException as error:
        failure=dict(error_type=type(error).__name__,detail=str(error));write(args.output/'failure.json',failure);raise
    finally:write(args.output/'resources.json',dict(resource_state(args.unit),failure=failure))


if __name__=='__main__':main()
