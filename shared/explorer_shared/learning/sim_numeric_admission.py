"""ENGINEERING_GLUE: evidence-only admission of native float64 interpolation.

No executor, physics, weights or normalization formula is changed. Four rounded
operations r=fl(s+fl(fl(fl(e-s)*n)/n)) give
|r-e| <= gamma4*(|s|+|e-s|), gamma4=4u/(1-4u), u=2**-53,
when binary64 round-to-nearest has no overflow/underflow. Fraction comparisons
retain the exact bound. Admission also requires exact native reproduction and
bitwise identical raw/boundary-normalized float32 neural input.

Primary references: https://numpy.org/doc/stable/reference/generated/numpy.finfo.html
and Higham/Mary https://tmary.perso.lip6.fr/doc/surveyMixed.pdf (standard model).
"""
from copy import deepcopy
from fractions import Fraction
import math

import numpy as np

from explorer_shared.contracts import digest
from .sim_object_route import LOW,HIGH
from .sim_temporal_route import TemporalChunkDispatcher,decode_increment9

SCHEMA='explorer_native_interpolation_admission_v2'
FORMULA='original_float64_start_plus_endpoint_minus_start_times_n_div_n_v1'


class NumericAdmissionRejected(ValueError):pass


def _vector(value,count,name):
    try:array=np.array(value,dtype=np.float64,copy=True)
    except (TypeError,ValueError) as error:raise NumericAdmissionRejected('Invalid '+name) from error
    if array.shape!=(count,) or not np.isfinite(array).all():raise NumericAdmissionRejected('Finite literal '+name+' required')
    return array


def _same(a,b):return np.asarray(a,np.float64).tobytes()==np.asarray(b,np.float64).tobytes()


def normalized_state9(raw):
    prior=np.asarray(raw,np.float32)
    return ((prior-LOW)*2/(HIGH-LOW)-1).astype(np.float32)


def ieee_interpolation_bound(start,endpoint):
    """Exact rational bound; no fitted epsilon and no physical error budget."""
    limits=np.finfo(np.float64)
    if limits.bits!=64 or limits.nmant!=52 or limits.eps!=2.**-52 or limits.epsneg!=2.**-53:
        raise NumericAdmissionRejected('Qualified IEEE binary64 required')
    one=np.float64(1);half=np.float64(2.**-53)
    if np.add(one,half)!=one or np.add(one,3*half)!=np.float64(1+2.**-51) or np.subtract(-one,half)!=-one:
        raise NumericAdmissionRejected('IEEE round-to-nearest/ties-to-even required')
    unit=Fraction.from_float(float(limits.epsneg));gamma=4*unit/(1-4*unit)
    s,e=Fraction.from_float(float(start)),Fraction.from_float(float(endpoint))
    return gamma*(abs(s)+abs(e-s))


def admit_prior9(raw_prior9,stamp,context,expectation):
    """Only past actual command metadata can repair a strict precheck rejection.

    Inputs are snapshotted before validation. A normal input follows the original
    path exactly; unsupported/outbound contexts remain UNKNOWN at the caller.
    """
    raw=_vector(raw_prior9,9,'prior9');stamp=deepcopy(stamp);context=deepcopy(context);expected=deepcopy(expectation)
    # Preserve the frozen actor's actual FLOAT32 precheck, including its normal
    # boundary quantization. This is not a new float64-domain restriction.
    with np.errstate(over='ignore',invalid='ignore'):prior32=raw.astype(np.float32)
    if not np.isfinite(prior32).all():raise NumericAdmissionRejected('Finite float32 actor prior required')
    outside=np.flatnonzero((prior32<LOW)|(prior32>HIGH)).tolist()
    if not outside:
        return raw,dict(schema=SCHEMA,kind='NORMAL',raw_prior9=raw.tolist(),network_prior9=raw.tolist(),normalization_bytes_unchanged=True)
    if any(index>=6 for index in outside):raise NumericAdmissionRejected('Only native arm interpolation roundoff is supported; base9 remains literal')
    if not isinstance(context,dict) or context.get('schema')!=SCHEMA or context.get('formula')!=FORMULA:
        raise NumericAdmissionRejected('Past actual native interpolation evidence required')
    if context.get('physical_io') is not False or context.get('finite_dynamics_step_completed') is not True or not context.get('receipt_id'):
        raise NumericAdmissionRejected('Actual finite SIM receipt required')
    identity=expected.get('source_identity') if isinstance(expected,dict) else None
    required={'implementation_sha256','environment_implementation_sha256','target_model_sha256','engine_version'}
    if not isinstance(identity,dict) or set(identity)!=required or any(not isinstance(identity[k],str) or len(identity[k])!=64 or any(c not in '0123456789abcdef' for c in identity[k]) for k in required-{'engine_version'}) or not identity['engine_version'] or context.get('source_identity')!=identity or context.get('source_kind')!='simulation':
        raise NumericAdmissionRejected('Qualified original SIM source/model identity required')
    after=context.get('dispatch_stamp');before=context.get('pre_action_stamp')
    if after!=stamp or not isinstance(before,dict) or not stamp.get('boot_id') or stamp.get('clock_domain')!=stamp['boot_id'] or before.get('boot_id')!=stamp['boot_id'] or before.get('clock_domain')!=stamp['clock_domain'] or type(before.get('monotonic_ns')) is not int or type(stamp.get('monotonic_ns')) is not int or not 0<=before['monotonic_ns']<stamp['monotonic_ns']:
        raise NumericAdmissionRejected('Same-boot immediately preceding actual dispatch clock required')
    start9=_vector(context.get('actual_start9'),9,'actual start9');native_start=_vector(context.get('native_command_before6'),6,'native start6')
    request9=_vector(context.get('actual_requested9'),9,'actual requested9');native_request=_vector(context.get('native_requested6'),6,'native requested6')
    actual9=_vector(context.get('actual_dispatched9'),9,'actual dispatched9');reported_endpoint=_vector(context.get('intended_endpoint6'),6,'intended endpoint6')
    if not _same(start9[:6],native_start) or not _same(request9[:6],native_request) or not _same(actual9,raw):
        raise NumericAdmissionRejected('Literal native start/request/dispatch binding differs')
    if np.any(start9<LOW) or np.any(start9>HIGH) or np.any(request9<LOW) or np.any(request9>HIGH):
        raise NumericAdmissionRejected('Only an original in-range start and request are qualified')
    velocity=_vector(context.get('velocity_deg_s'),6,'native velocity6');period=context.get('period_s');dt=context.get('timestep_s');n=context.get('substeps')
    if velocity.tolist()!=expected.get('velocity_deg_s') or period!=expected.get('period_s') or dt!=expected.get('timestep_s') or n!=expected.get('substeps') or context.get('controller_settings_modified') is not False:
        raise NumericAdmissionRejected('Original native rate/timestep profile differs')
    if type(n) is not int or not 1<=n<2**53 or type(period) not in (float,int) or type(dt) not in (float,int) or not math.isfinite(period) or not math.isfinite(dt) or not 0<dt<=period or n!=round(period/dt) or np.any(velocity<=0):
        raise NumericAdmissionRejected('Documented original finite substep count required')
    if stamp['monotonic_ns']-before['monotonic_ns']!=round(n*dt*1e9):
        raise NumericAdmissionRejected('Actual preceding dispatch duration differs from declared native substeps')
    endpoint=native_start+np.clip(native_request-native_start,-velocity*period,velocity*period)
    if not _same(endpoint,reported_endpoint) or np.any(endpoint<LOW[:6]) or np.any(endpoint>HIGH[:6]):
        raise NumericAdmissionRejected('Original intended endpoint must be reproduced and in range')
    difference=endpoint-native_start;product=difference*n;quotient=product/n;reproduced=native_start+quotient
    tiny=np.finfo(np.float64).tiny
    for vector in (native_start,native_request,endpoint,difference,product,quotient,reproduced):
        if not np.isfinite(vector).all() or np.any((abs(vector)>0)&(abs(vector)<tiny)):
            raise NumericAdmissionRejected('Overflow/subnormal interpolation has no admitted error model')
    if not _same(reproduced,actual9[:6]):raise NumericAdmissionRejected('Native final interpolation does not reproduce bitwise')
    bounds=[]
    for index in outside:
        bound=ieee_interpolation_bound(native_start[index],endpoint[index]);error=abs(Fraction.from_float(float(actual9[index]))-Fraction.from_float(float(endpoint[index])))
        if error>bound:raise NumericAdmissionRejected('Native excursion exceeds derived IEEE operation bound')
        bounds.append(dict(index=index,error_exact=str(error),bound_exact=str(bound),bound_outward_float=math.nextafter(float(bound),math.inf)))
    boundary=np.clip(raw,LOW,HIGH);raw_tensor=normalized_state9(raw);boundary_tensor=normalized_state9(boundary)
    if raw_tensor.tobytes()!=boundary_tensor.tobytes():raise NumericAdmissionRejected('Raw/boundary float32 neural tensor differs')
    return boundary,dict(schema=SCHEMA,kind='PROVEN_NATIVE_INTERPOLATION_ROUNDOFF',raw_prior9=raw.tolist(),network_prior9=boundary.tolist(),
        outside_components=outside,formula=FORMULA,unit_roundoff='2**-53',bound_formula='gamma4*(abs(start)+abs(endpoint-start)); gamma4=4u/(1-4u)',
        exact_bounds=bounds,context_receipt_id=context['receipt_id'],context_sha256=digest(context),
        native_reproduction_bitwise=True,normalization_bytes_unchanged=True,normalized_float32_values=raw_tensor.tolist(),
        physical_limits_or_executor_changed=False,admission_context_is_neural_input=False)


def command_context(target,observation,requested9,dispatch,preparation):
    """Capture copies of already-completed native command facts, never truth."""
    env=target.env;start9=_vector(observation['preceding_command9'],9,'start9');request9=_vector(requested9,9,'requested9')
    start=_vector(env.command_before,6,'command_before6');native_request=_vector(env.requested,6,'requested6');velocity=_vector(env.velocity,6,'velocity6')
    period=float(env.period);dt=float(env.model.opt.timestep);n=round(period/dt);endpoint=start+np.clip(native_request-start,-velocity*period,velocity*period)
    if not _same(start9[:6],start) or not _same(request9[:6],native_request) or not _same(env.command,dispatch['dispatched_action9'][:6]):
        raise NumericAdmissionRejected('Actual native state changed before context capture')
    if preparation.get('original_velocity_deg_s')!=velocity.tolist() or preparation.get('original_period_s')!=period or preparation.get('controller_settings_modified') is not False:
        raise NumericAdmissionRejected('Actual native profile differs from dispatch preparation')
    identity={key:deepcopy(target.provider_identity[key]) for key in ('implementation_sha256','environment_implementation_sha256','target_model_sha256','engine_version')}
    return dict(schema=SCHEMA,formula=FORMULA,source_kind='simulation',source_identity=identity,
        actual_start9=start9.tolist(),native_command_before6=start.tolist(),actual_requested9=request9.tolist(),native_requested6=native_request.tolist(),
        intended_endpoint6=endpoint.tolist(),actual_dispatched9=deepcopy(dispatch['dispatched_action9']),velocity_deg_s=velocity.tolist(),period_s=period,timestep_s=dt,substeps=n,
        receipt_id=dispatch['receipt_id'],pre_action_stamp=deepcopy(dispatch['pre_action_stamp']),dispatch_stamp=deepcopy(dispatch['dispatch_stamp']),
        physical_io=dispatch['physical_io'],finite_dynamics_step_completed=dispatch['finite_dynamics_step_completed'],controller_settings_modified=False,
        endpoint_basis='computed_from_actual_native_start_and_request_using_unchanged_original_endpoint_equation')


class NumericAdmissionDispatcher(TemporalChunkDispatcher):
    """Explicit v2 evaluation component; original frozen module is untouched."""
    def __init__(self,*args,admission_expectation,**kwargs):
        super().__init__(*args,**kwargs);self.admission_expectation=deepcopy(admission_expectation)
    def next(self,rgb,prior9,stamp,*,previous_command_context=None):
        literal=_vector(prior9,9,'prior9')
        network_prior,admission=admit_prior9(literal,stamp,previous_command_context,self.admission_expectation)
        raw,metadata=super().next(rgb,network_prior,stamp)
        # Increment decoding always retains the literal raw prior. Only the
        # strict preprocessing check can be bypassed by an actual proof.
        if self.config['labels']=='DISPATCHED_INCREMENT9':
            raw=decode_increment9(metadata['selected_normalized_output9'],literal,velocity6=self.rate['velocity_deg_s'],period_s=self.rate['period_s'])
        metadata['numeric_admission']=admission;metadata['raw_literal_prior9_preserved']=True
        return raw,metadata
