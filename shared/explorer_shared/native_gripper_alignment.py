"""ENGINEERING_GLUE: admission-only alignment, never a motion mapper/filter.

The unchanged absolute gripper mapper remains authoritative in ACTIVE. This
metadata check only refuses acquire when it would introduce a gripper jump.
"""
import math

from .native_stream03 import AUDITED_FACTORY_SHA256

# Exact audited arm_velocity.py np.rint endpoint + retention around the last
# integer command: abs(endpoint - previous_quantized) < 0.55. Not a new motor
# tolerance, deadband, aperture/contact accuracy or ACTIVE-path operation.
ORIGINAL_SERIALIZATION_BOUND_DEG = .55


def gripper_alignment(geometry, leader, target, *, calibration_sha256, wire_target=None):
    result={'schema':'explorer_native_gripper_alignment_v1','aligned':False,
        'admission_only':True,'active_absolute_mapper_unchanged':True,
        'target_state_source':'command_estimate','measured_target':False,
        'force_or_contact_inferred':False,
        'native_serialization_source_sha256':AUDITED_FACTORY_SHA256['src/arm_velocity.py']}
    try:
        source=float(leader[5]);current=float(target[5]);mapped=float(geometry.gripper_mapper(source))
        if not 0<=source<=100 or not all(math.isfinite(v) for v in (source,current,mapped)):
            raise ValueError('Finite source and command reference required')
        result.update(source_normalized=source,target_command_estimate_deg=current,
            mapped_absolute_deg=mapped,delta_deg=mapped-current)
        coordinates=geometry.context.coordinates
        if coordinates.sha256!=calibration_sha256:
            raise ValueError('Original leader calibration identity mismatch')
        row=coordinates.calibration['gripper'];span=row['range_max']-row['range_min']
        if type(span) is not int or span<=0:raise ValueError('Original encoder range unavailable')
        tick=100./span  # Official RANGE_0_100 normalization, one actual tick.
        changes=[]
        for value in (source-tick,source+tick):
            try:
                if not 0<=value<=100:raise ValueError('Outside original normalized contract')
                other=float(geometry.gripper_mapper(value))
                if not math.isfinite(other):raise ValueError('Nonfinite mapped tick')
                changes.append(abs(other-mapped))
            except ValueError:
                pass  # At a registered endpoint only the inward tick exists.
        if not changes:raise ValueError('Registered one-tick neighborhood unavailable')
        source_bound=max(changes)
        wire=float((wire_target if wire_target is not None else target)[5])
        if not math.isfinite(wire) or not wire.is_integer():
            raise ValueError('Original integer endpoint reference required; no inferred wire pose')
        def serialized(value):
            return wire if abs(value-wire)<ORIGINAL_SERIALIZATION_BOUND_DEG else float(round(value))
        candidates=[mapped,mapped-source_bound,mapped+source_bound]
        same_endpoint=serialized(current)==wire and all(serialized(value)==wire for value in candidates)
        result.update(source_calibration_sha256=calibration_sha256,source_encoder_tick_normalized=tick,
            source_one_tick_bound_deg=source_bound,native_serialization_bound_deg=ORIGINAL_SERIALIZATION_BOUND_DEG,
            current_original_wire_endpoint_deg=wire,serialized_command_estimate_deg=serialized(current),serialized_candidate_deg=serialized(mapped),
            encoder_uncertainty_same_endpoint=same_endpoint,aligned=same_endpoint,
            bound_basis='existing_integer_endpoint_serialization_applied_to_candidate_and_one_tick_uncertainty',
            reason='ALIGNED' if same_endpoint else 'ALIGN_LEADER_GRIPPER_TO_CURRENT_COMMAND_ESTIMATE')
    except (AttributeError,KeyError,TypeError,ValueError,IndexError) as error:
        result.update(reason='GRIPPER_ALIGNMENT_QUANTIZATION_UNAVAILABLE',detail=str(error))
    return result
