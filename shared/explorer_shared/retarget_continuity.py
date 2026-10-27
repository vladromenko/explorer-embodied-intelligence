"""ENGINEERING_GLUE: bounded offline trace qualification, memory-only sink.

No HTTP/client/controller/robot construction. Source clock intervals and NX
request RTTs are separate; replay time is not measured hardware latency.
"""
from copy import deepcopy
import hashlib
import json
import math
from pathlib import PurePosixPath
from types import SimpleNamespace

from .pi_native_retarget import PiNativeRetargetService


def sha(raw):return hashlib.sha256(raw).hexdigest()


def _json(raw):
    return json.loads(raw,parse_constant=lambda value:(_ for _ in ()).throw(ValueError('Nonfinite JSON forbidden')))


def _vector(values):
    if not isinstance(values,list) or len(values)!=6 or any(type(v) not in (int,float) or not math.isfinite(v) for v in values):
        raise ValueError('Six finite genuine source values required')
    return values


def inspect_recorded_trace(raw,source_payloads):
    if not isinstance(raw,bytes) or len(raw)>4*1024*1024:raise ValueError('Bounded original trace bytes required')
    value=_json(raw);samples=value.get('samples')
    if value.get('schema')!='explorer_leader_measured_cache_trace_v1' or not isinstance(samples,list) or not 2<=len(samples)<=10000:
        raise ValueError('Original measured-cache trace schema/bound required')
    if not isinstance(source_payloads,dict) or sum(len(v) for v in source_payloads.values())>32*1024*1024:
        raise ValueError('Bounded original source payload map required')
    originals=[];identities=set();timestamps=[];rtts=[];positions=[];bindings={}
    for index,row in enumerate(samples):
        name=row['raw_source_file'];path=PurePosixPath(name)
        if path.is_absolute() or '..' in path.parts or str(path)!=name or type(row.get('index')) is not int or row.get('index')!=index:
            raise ValueError('Exact relative original source name/index required')
        payload=source_payloads[name]
        if not isinstance(payload,bytes) or len(payload)>256*1024 or sha(payload)!=row['raw_source_sha256']:
            raise ValueError('Original source bytes changed')
        original=_json(payload);measured=original.get('measurements') or {};role=(original.get('roles') or {}).get('leader') or {}
        expected={'source_monotonic_ns':measured.get('monotonic_ns'),'source_clock_domain':measured.get('clock_domain'),
            'leader':measured.get('leader'),'source_calibration_sha256':role.get('calibration_sha256'),
            'station_control_epoch':original.get('control_epoch'),'available':original.get('available'),'station_mode':original.get('mode')}
        if type(row.get('available')) is not bool or type(original.get('available')) is not bool:
            raise ValueError('Actual boolean source availability required')
        if any(row.get(key)!=item for key,item in expected.items()):raise ValueError('Trace projection differs from original source')
        stamp=row['source_monotonic_ns'];started=row.get('request_started_homebrain_ns');received=row.get('response_received_homebrain_ns')
        if (type(stamp) is not int or stamp<0 or type(started) is not int or type(received) is not int or received<started or
                not row['source_clock_domain'] or not row['source_calibration_sha256'] or not row['station_control_epoch']):
            raise ValueError('Original independent source/NX clock identities required')
        positions.append(_vector(row['leader']));timestamps.append(stamp);rtts.append(received-started)
        identities.add((row['source_clock_domain'],row['source_calibration_sha256'],row['station_control_epoch']))
        originals.append(original);bindings[name]=sha(payload)
    ranges=[max(row[i] for row in positions)-min(row[i] for row in positions) for i in range(6)]
    return {'schema':'explorer_retarget_recorded_trace_inspection_v1','trace_sha256':sha(raw),
        'source_files_sha256':bindings,'source_samples':len(samples),'source_identity_count':len(identities),
        'source_position_ranges_normalized':ranges,'changing_input_observed':any(v!=0 for v in ranges),
        'source_timestamp_metrics':sequence_metrics(timestamps,positions),
        'homebrain_GET_RTT_ns':_distribution(rtts),'cross_device_clock_subtraction_used':False,
        'source_declared_scope':value.get('scope'),'originals':originals,'physical_commands_sent_by_qualifier':0}


def _distribution(values):
    if not values:return None
    ordered=sorted(values)
    return {'count':len(values),'min':min(values),'max':max(values),'mean':sum(values)/len(values),
        'p95':ordered[min(len(ordered)-1,math.ceil(.95*len(ordered))-1)]}


def sequence_metrics(stamps,vectors):
    if len(stamps)!=len(vectors):raise ValueError('Correlated timestamp/vector sequence required')
    dts=[b-a for a,b in zip(stamps,stamps[1:])];steps=[];velocities=[];accels=[];jerks=[]
    for index,dt in enumerate(dts):
        delta=[vectors[index+1][axis]-vectors[index][axis] for axis in range(6)];steps.append(max(abs(v) for v in delta))
        if dt>0:velocities.append([v/(dt/1e9) for v in delta])
    # Derivatives are only defined on a strictly increasing SAME source clock.
    if dts and all(dt>0 for dt in dts):
        for index in range(len(velocities)-1):
            dt=(dts[index]+dts[index+1])/2e9
            accels.append([(b-a)/dt for a,b in zip(velocities[index],velocities[index+1])])
        for index in range(len(accels)-1):
            dt=(dts[index]+2*dts[index+1]+dts[index+2])/4e9
            jerks.append([(b-a)/dt for a,b in zip(accels[index],accels[index+1])])
    maximum=lambda values:max((abs(v) for row in values for v in row),default=None)
    return {'sample_count':len(stamps),'dt_ns':_distribution(dts),'out_of_order_or_duplicate':sum(dt<=0 for dt in dts),
        'mean_frequency_hz':((len(stamps)-1)*1e9/(stamps[-1]-stamps[0]) if len(stamps)>1 and stamps[-1]>stamps[0] else None),
        'dt_jitter_span_ns':max(dts)-min(dts) if dts else None,'max_step_delta':max(steps,default=None),
        'max_velocity':maximum(velocities),'max_acceleration':maximum(accels),'max_jerk':maximum(jerks),
        'derivative_basis':'finite_differences_of_supplied_vector_in_its_original_clock_not_attained_motion'}


class _ReplayStation:
    camera=SimpleNamespace(stale_s=2.)
    def __init__(self,original):self.current=original;self.boot=original['measurements']['clock_domain'].rsplit(':',1)[-1]
    def status(self):return deepcopy(self.current)


class _MemorySink:
    """Only memory method calls: neither HTTP nor NativeStreamClient is used."""
    def __init__(self,reference,period):
        self.reference_value=reference;self.period=period;self.active=False;self.state='STOPPED';self.epoch='memory-native-epoch';self.authorization_id='memory-only';self.inputs=[];self.calls=[]
    def status(self):
        return {'execution_permission':True,'active':self.active,'stop_latched':not self.active,'state':self.state,
            'control_epoch':self.epoch,'boot_id':self.reference_value['boot_id'],'activation':{'authorization_id':self.authorization_id},
            'arm_backend':'existing_factory_ruckig_velocity','original_stream_config':{'publish_period_s':self.period}}
    def reference(self):return deepcopy(self.reference_value)
    def select_keyboard(self,**kwargs):self.calls.append('select')
    def begin(self,**kwargs):self.active=True;self.state='CLUTCHED';self.calls.append('begin');return {'control_epoch':self.epoch}
    def resume(self,epoch,**kwargs):self.state='ACTIVE';self.calls.append('resume')
    def keyboard_base(self,epoch,actions,**kwargs):self.calls.append('explicit_neutral_lease');return {'accepted':True,'native_dispatched':False}
    def input(self,epoch,sequence,velocity,evidence,**kwargs):
        self.inputs.append({'source_monotonic_ns':evidence['monotonic_ns'],'velocity':deepcopy(velocity),'proposal':deepcopy(evidence['geometric_proposal'])})
        return {'accepted':True,'native_dispatched':False,'command_estimate':self.reference()}


def qualify_recorded_trace(raw,source_payloads,*,state_dir,geometry_factory=None,target_reference=None,original_native_period_s=None):
    inspected=inspect_recorded_trace(raw,source_payloads);originals=inspected.pop('originals')
    result={**inspected,'schema':'explorer_retarget_continuity_qualification_v1',
        'full_continuity_qualification':'OPEN','task_completed':False,'physical_commands':0,'native_publication_observed':False,
        'native_Ruckig_executed':False,'native_TTL_simulated':False,'model_or_controller_settings_changed':False,
        'open_facts':['CHANGING_REAL_LEADER_TRACE_REQUIRED' if not inspected['changing_input_observed'] else 'REAL_TRACE_ORIGIN_AND_CONTEXT_REVIEW_REQUIRED',
            'ACTUAL_CURRENT_REGISTERED_GEOMETRY_AND_NATIVE_REFERENCE_REQUIRED','NATIVE_OUTPUT_CONTINUITY_NOT_MEASURED'],
        'software_gateway_comparison':{'state':'BLOCKED','reason':'REGISTERED_GEOMETRY_AND_VALID_NATIVE_REFERENCE_NOT_SUPPLIED'}}
    if geometry_factory is not None and target_reference is not None:
        if (inspected['source_identity_count']!=1 or type(original_native_period_s) not in (int,float) or
                not math.isfinite(original_native_period_s) or original_native_period_s<=0 or target_reference.get('measured') is not False):
            raise ValueError('One original source identity, explicit native period and command-only reference required')
        station=_ReplayStation(originals[0]);now=[originals[0]['measurements']['monotonic_ns']]
        peer=_MemorySink(target_reference,original_native_period_s);geometry=geometry_factory();baseline=geometry_factory()
        source=PiNativeRetargetService(geometry,station,state_dir,peer,clock=lambda:now[0],allow_synthetic=True)
        states=[];expected=[];accepted_stamps=[];target=target_reference.get('q_estimated_deg',target_reference.get('servo_deg'))
        try:
            source.start({'control_epoch':source.epoch,'observing':True})
            source.lease({'control_epoch':source.epoch,'sequence':0,'held':True,'observing':True,'base_actions':{}})
            baseline.recenter(originals[0]['measurements']['leader'],target);states.append(source.mode)
            for original in originals[1:]:
                station.current=original;now[0]=original['measurements']['monotonic_ns']
                before=len(peer.inputs);source._step_once();states.append(source.mode)
                if len(peer.inputs)>before:
                    proposal=baseline.propose(original['measurements']['leader'],target,original_native_period_s=original_native_period_s)
                    expected.append(proposal['joint_velocity_deg_s']);accepted_stamps.append(now[0])
            actual=[row['velocity'] for row in peer.inputs]
            exact=json.dumps(actual,allow_nan=False,separators=(',',':'))==json.dumps(expected,allow_nan=False,separators=(',',':'))
            goals=[row['proposal'].get('desired_servo_deg') for row in peer.inputs]
            result['software_gateway_comparison']={'state':'PASS' if exact and all(v=='ACTIVE' for v in states) else 'FAIL',
                'scope':'OFFLINE_MEMORY_SINK_ONLY_NOT_NATIVE_PUBLICATION','exact_unchanged_geometry_velocity_values':exact,
                'accepted_inputs':len(actual),'packet_drops':dict(source.packet_drops),'state_transitions':states,'last_error':source.error,
                'injected_zero_or_substituted_vector_count':sum(a!=b for a,b in zip(actual,expected)),
                'naturally_zero_baseline_vectors':sum(not any(v) for v in expected),
                'input_velocity_metrics':sequence_metrics(accepted_stamps,actual),
                'desired_command_metrics':sequence_metrics(accepted_stamps,goals) if all(isinstance(v,list) and len(v)==6 for v in goals) else None,
                'resume_calls':peer.calls.count('resume'),'explicit_neutral_lease_calls':peer.calls.count('explicit_neutral_lease'),
                'replay_wall_frequency_is_hardware_frequency':False,
                'registration_provenance':deepcopy(getattr(geometry,'registration',None))}
        finally:source.close()
    return result
