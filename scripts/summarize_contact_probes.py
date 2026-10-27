#!/usr/bin/env python3
"""ENGINEERING_GLUE: independent byte/clock/force checks of frozen contact probes.

No model, NumPy, simulator, policy, restore or dynamics execution. JSON contact
records are checked against the actual returned frames/wrenches and registered
initial native bytes, retaining all16 outcomes and numerical ambiguities.
"""
from __future__ import annotations
import argparse
import ast
import hashlib
import json
import math
import os
from pathlib import Path
import stat
import struct

from explorer_shared.contact_diagnosis import canonical, sha, signed_world_force, _relative, geometry_from_xml


class ProbeReportRejected(ValueError):pass


def checked_file(path,expected=None,maximum=256*1024**2):
    path=Path(path);before=path.stat(follow_symlinks=False)
    if path.is_symlink() or path.resolve()!=path.absolute() or not stat.S_ISREG(before.st_mode) or before.st_size>maximum:
        raise ProbeReportRejected('Bounded nonaliased regular probe evidence required')
    with os.fdopen(os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK),'rb') as stream:
        opened=os.fstat(stream.fileno());checksum=hashlib.file_digest(stream,'sha256').hexdigest();after=os.fstat(stream.fileno())
    current=path.stat(follow_symlinks=False)
    def identity(value):return value.st_dev,value.st_ino,value.st_size,value.st_mtime_ns,value.st_ctime_ns
    if any(identity(value)!=identity(before) for value in (opened,after,current)) or expected is not None and checksum!=expected:
        raise ProbeReportRejected('Probe evidence SHA/identity changed')
    return dict(sha256=checksum,bytes=before.st_size)


def read_json(path,expected=None):
    checked_file(path,expected,16*1024**2);return json.loads(Path(path).read_bytes())


def native_layout(path):
    with Path(path).open('rb') as stream:
        if stream.read(6)!=b'\x93NUMPY':raise ProbeReportRejected('Native NPY magic differs')
        version=stream.read(2)
        if version==b'\x01\x00':length=struct.unpack('<H',stream.read(2))[0]
        elif version==b'\x02\x00':length=struct.unpack('<I',stream.read(4))[0]
        else:raise ProbeReportRejected('Native NPY version unsupported')
        if not 0<length<10000:raise ProbeReportRejected('Bounded native header required')
        descriptor=ast.literal_eval(stream.read(length).decode('ascii'))
        shape=descriptor.get('shape');dtype=descriptor.get('descr')
        if (dtype not in ('<f8','>f8') or descriptor.get('fortran_order') is not False or not isinstance(shape,tuple) or
                len(shape) not in (1,2) or any(type(v) is not int or not 0<v<=10000 for v in shape)):
            raise ProbeReportRejected('Exact bounded float64 native ABI required')
        offset=stream.tell()
    if offset+math.prod(shape)*8!=Path(path).stat().st_size:raise ProbeReportRejected('Native array byte count differs')
    return dict(shape=shape,dtype=dtype,offset=offset)


def native_check(path,initial,steps,dt):
    layout=native_layout(path);first=native_layout(initial)
    if layout['shape']!=(steps+1,first['shape'][0]) or first['dtype']!=layout['dtype']:
        raise ProbeReportRejected('Native trajectory/initial ABI differs')
    stride=first['shape'][0]*8
    with Path(path).open('rb') as stream,Path(initial).open('rb') as source:
        stream.seek(layout['offset']);source.seek(first['offset'])
        if stream.read(stride)!=source.read(stride):raise ProbeReportRejected('Native first row differs from registered initial snapshot')
        for index in range(steps+1):
            stream.seek(layout['offset']+index*stride);tick=struct.unpack(layout['dtype'][0]+'d',stream.read(8))[0]
            if not math.isfinite(tick) or abs(tick-index*dt)>1e-9:raise ProbeReportRejected('Native trajectory time differs')
    return dict(initial_native_bytes_equal=True,rows=steps+1,state_size=first['shape'][0],clock_verified=True)


def verified_snapshot(record,folder):
    manifest=folder/'initial-state/manifest.json'
    if record.get('status')!='RECORDED' or Path(record['manifest_path']).absolute()!=manifest:
        raise ProbeReportRejected('Actual same-attempt registered initial snapshot required')
    document=read_json(manifest,record['manifest_sha256']);payloads=document['payloads']
    if payloads!=record['payloads'] or document.get('physical_io') is not False or document.get('retroactive_capture') is not False:
        raise ProbeReportRejected('Initial snapshot declared byte inventory/scope differs')
    total=0
    for name,entry in payloads.items():
        descriptor=checked_file(manifest.parent/_relative(name),entry['sha256'])
        if descriptor['bytes']!=entry['bytes']:raise ProbeReportRejected('Initial snapshot size differs')
        total+=descriptor['bytes']
    if total!=record['total_payload_bytes'] or total>256*1024**2:raise ProbeReportRejected('Initial total bound differs')
    return dict(manifest_sha256=record['manifest_sha256'],payloads_verified=len(payloads),payload_bytes_verified=total,
        native_initial_file=manifest.parent/'native-integration.npy')


def recompute_contact_rows(rows,dt,top,weight):
    """Independent terminal score and signed normals, without driver imports."""
    count=0;window=[];first_down=None;opposing=None;max_other=0;max_force_error=0.;max_solver_iterations=0;min_distance=None
    for row in rows:
        if row['index']!=count or abs(row['pre_step_time_s']-count*dt)>1e-9 or abs(row['post_step_time_s']-(count+1)*dt)>1e-9:
            raise ProbeReportRejected('Substep row index/native clock differs')
        bottom=[0.,0.,0.];floor=[0.,0.,0.];normal=0.;selected=[]
        for contact in row['contacts']:
            force=signed_world_force(contact['contact_frame_rows'],contact['local_wrench'],object_side=contact['object_side'])
            error=max(abs(a-b) for a,b in zip(force,contact['world_force_on_object_N']));max_force_error=max(max_force_error,error)
            if error>1e-10:raise ProbeReportRejected('Returned local wrench/frame and saved world force disagree')
            other=contact['geom_names'][1-contact['object_side']]
            if other=='bin_bottom':
                bottom=[a+b for a,b in zip(bottom,force)];normal+=max(0.,contact['local_wrench'][0]);selected.append(contact)
            if other=='floor':floor=[a+b for a,b in zip(floor,force)]
            min_distance=contact['distance_m'] if min_distance is None else min(min_distance,contact['distance_m'])
        for saved,computed in ((row['bottom_signed_world_force_N'],bottom),(row['floor_signed_world_force_N'],floor)):
            if max(abs(a-b) for a,b in zip(saved,computed))>1e-10:raise ProbeReportRejected('Saved per-pair force sum disagrees')
        if abs(normal-row['bottom_normal_magnitude_N'])>1e-10:raise ProbeReportRejected('Normal-magnitude scalar disagrees')
        event=dict(index=count,time_s=row['post_step_time_s'],object=row['object'],bottom_force_N=bottom,floor_force_N=floor,
            bottom_contacts=[dict(geom_names=c['geom_names'],object_side=c['object_side'],distance_m=c['distance_m'],
                object_normal_world=[(1 if c['object_side']==1 else -1)*v for v in c['contact_frame_rows'][:3]],
                world_force_on_object_N=c['world_force_on_object_N']) for c in selected])
        if bottom[2]<-1e-4 and first_down is None:first_down=event
        if bottom[2]<-weight and floor[2]>weight and opposing is None:opposing=event
        max_other=max(max_other,len(row['other_object_contacts']));max_solver_iterations=max(max_solver_iterations,max(row['solver']['niter']))
        if any(warning['number'] for warning in row['solver']['warnings']):raise ProbeReportRejected('Completed outcome contains native warning')
        if row['post_step_time_s']>=.8-1e-9:window.append((row,bottom,floor))
        count+=1
    if count!=round(1./dt) or len(window)!=round(.2/dt)+1:raise ProbeReportRejected('All declared native/terminal samples required')
    supported=all(row['object']['minimum_point_z_m']>=top-.001 for row,_,_ in window)
    settled=all(row['maximum_object_point_speed_m_s']<.05 for row,_,_ in window)
    free=all(not row['other_object_contacts'] for row,_,_ in window);inside=all(row['inside_bottom_xy'] for row,_,_ in window)
    bottom=sum(force[2] for _,force,_ in window)/len(window);floor=sum(force[2] for _,_,force in window)/len(window)
    return dict(recomputed_contact_recovered=bool(supported and settled and free and inside and bottom>1e-4),
        terminal_predicates=dict(supported=supported,settled=settled,other_contact_free=free,inside=inside),
        terminal_mean_bottom_world_Z_N=bottom,terminal_mean_floor_world_Z_N=floor,
        terminal_net_world_Z_contact_N=bottom+floor,weight_N=weight,
        first_downward_bottom_force=first_down,first_large_opposing_floor_bottom=opposing,
        maximum_other_object_contact_count=max_other,maximum_force_conversion_error=max_force_error,
        maximum_solver_iterations=max_solver_iterations,minimum_pair_signed_distance_m=min_distance,verified_substeps=count)


def json_rows(path):
    with Path(path).open('rb') as stream:
        for line in stream:
            if len(line)>1024**2:raise ProbeReportRejected('Bounded raw substep record required')
            yield json.loads(line)


def summarize(folder,expected_protocol_sha,expected_results_sha):
    folder=Path(folder).absolute();protocol=read_json(folder/'frozen-protocol.json',expected_protocol_sha)
    campaign=read_json(folder/'campaign-results.json',expected_results_sha)
    if campaign['protocol_sha256']!=expected_protocol_sha or not campaign['complete_16_attempt_receipts'] or len(campaign['attempts'])!=16:
        raise ProbeReportRejected('All16 frozen outcome receipts required')
    outputs=[];seen=set()
    for item in campaign['attempts']:
        path=Path(item['path']);checked_file(path,item['sha256']);result=read_json(path,item['sha256'])
        if result!=item['result'] or path.parent.parent!=folder/'attempts' or result['status']!='COMPLETED':
            raise ProbeReportRejected('Current finite completed receipt/root differs')
        identity=(result['case']['id'],result['condition'])
        if identity in seen:raise ProbeReportRejected('Duplicate original case/condition')
        seen.add(identity);condition=next(c for c in protocol['conditions'] if c['id']==result['condition'])
        snapshot=verified_snapshot(result['initial_state'],path.parent)
        raw_files={name:checked_file(path.parent/name,maximum=64*1024**2) for name in ('substeps.jsonl','native-states.npy','compiled-input-model.xml')}
        geometry=geometry_from_xml((path.parent/'compiled-input-model.xml').read_bytes())
        native=native_check(path.parent/'native-states.npy',snapshot.pop('native_initial_file'),round(1./condition['timestep_s']),condition['timestep_s'])
        analysis=recompute_contact_rows(json_rows(path.parent/'substeps.jsonl'),condition['timestep_s'],geometry['bin_bottom_world_top_z_m'],geometry['weight_N'])
        if analysis['recomputed_contact_recovered']!=result['outcome']['contact_recovered']:
            raise ProbeReportRejected('Independent raw contact score disagrees with original new attempt')
        for name,entry in raw_files.items():checked_file(path.parent/name,entry['sha256'],64*1024**2)
        outputs.append(dict(attempt_id=result['attempt_id'],original_case=identity[0],condition=identity[1],
            original_case_success=result['original_success'],result_sha256=item['sha256'],initial_snapshot=snapshot,native=native,
            raw_artifacts=raw_files,raw_SHA_binding_basis='first_independent_registration_after_unit_exit_no_prior_producer_raw_hash_claim',analysis=analysis))
    expected={(case['original_scenario']['id'],condition['id']) for case in protocol['cases'] for condition in protocol['conditions']}
    if seen!=expected:raise ProbeReportRejected('Full matched four-by-four design required')
    by_condition={c['id']:dict(attempts=4,recovered=sum(entry['analysis']['recomputed_contact_recovered'] for entry in outputs if entry['condition']==c['id'])) for c in protocol['conditions']}
    return dict(schema='explorer_controlled_contact_independent_report_v1',protocol_sha256=expected_protocol_sha,
        producer_campaign_results_sha256=expected_results_sha,report_driver_sha256=sha(Path(__file__).read_bytes()),
        by_condition=by_condition,attempts=outputs,resources=campaign['resources'],elapsed_s=campaign['elapsed_s'],
        original_V9_outcomes_unchanged=True,source_domain='SIMULATION',new_initial_conditions_not_original_reexecution=True,
        signed_opposing_contact_mechanism='OBSERVED_IN_NEW_BASELINE_RAW_CONTACTS_not_retroactively_measured_V9',
        unique_solver_cause='AMBIGUOUS',physics_accuracy_validated=False,initial_restore_reexecution='UNQUALIFIED',
        learned_policy_executed=False,physical_io=False,full_task_success_claimed=False,H4_test=False,optimizer_admission=False)


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--folder',type=Path,required=True);parser.add_argument('--protocol-sha256',required=True)
    parser.add_argument('--campaign-results-sha256',required=True);parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
    result=summarize(args.folder,args.protocol_sha256,args.campaign_results_sha256)
    with args.output.open('xb') as stream:stream.write(canonical(result));stream.flush();os.fsync(stream.fileno())
    print(canonical(dict(by_condition=result['by_condition'],report_sha256=sha(canonical(result)),physical_io=False)).decode())


if __name__=='__main__':main()
