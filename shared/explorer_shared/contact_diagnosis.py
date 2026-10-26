"""ENGINEERING_GLUE: source-bound, forward-free V9 contact forensics/probe plan.

This reads retained JSON/XML/source bytes only. It never imports a simulator,
executes archived Python, repairs historical outcomes, or admits development
data to an optimizer. Contact magnitudes cannot identify signed force direction.
"""
from __future__ import annotations

from bisect import bisect_left
import hashlib
import json
import math
from pathlib import Path, PurePosixPath
import re
import xml.etree.ElementTree as ET

SCHEMA = 'explorer_saved_contact_diagnosis_v1'
PROBE_SCHEMA = 'explorer_controlled_contact_probe_protocol_v1'
ARCHIVED_ENV_SHA = 'a0bf6c275002d6a9ce457d1e38cbbc188b8edd13cfe1adbfa3026d533c8ffe8a'
ARCHIVED_PROVIDER_SHA = '04ce865a2c4bab5d22ba7a7af5d9990e60dad0e9b1018ed649bda9ed4c445700'
SUBSTEP_COLUMNS = ('simulation_time_s', 'pad_left_normal_magnitude_N', 'pad_right_normal_magnitude_N',
    'bin_bottom_normal_magnitude_N', 'object_mean_x_m', 'object_mean_y_m', 'object_mean_z_m',
    'minimum_object_point_z_m', 'maximum_object_point_z_m', 'maximum_object_point_speed_m_s')


class ContactEvidenceRejected(ValueError):
    pass


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _relative(value):
    if not isinstance(value, str) or not value or '\\' in value:
        raise ContactEvidenceRejected('Portable relative evidence member required')
    path = PurePosixPath(value)
    if path.is_absolute() or any(p in ('.', '..') for p in path.parts) or str(path) != value:
        raise ContactEvidenceRejected('Evidence member escapes declared root')
    return value


def _read(path, *, expected=None, maximum=16 * 1024**2):
    path = Path(path)
    if path.is_symlink() or path.resolve() != path.absolute() or not path.is_file() or path.stat().st_size > maximum:
        raise ContactEvidenceRejected('Bounded nonaliased regular evidence file required: ' + str(path))
    raw = path.read_bytes()
    if len(raw) > maximum or expected is not None and sha(raw) != expected:
        raise ContactEvidenceRejected('Retained evidence SHA/size changed: ' + str(path))
    return raw


def _finite(values):
    return isinstance(values, list) and all(type(v) in (int, float) and math.isfinite(v) for v in values)


def geometry_from_xml(raw):
    """Read actual slab top/midplane, floor and mass; do not infer a collider fix."""
    if len(raw) > 1024**2:
        raise ContactEvidenceRejected('Bounded scene XML required')
    try:
        root = ET.fromstring(raw)
        if root.find('.//include') is not None:
            raise ValueError('External XML include is not an inspected closed scene')
        bottom = root.find(".//body[@name='bin']/geom[@name='bin_bottom']")
        container = root.find(".//body[@name='bin']")
        floor = root.find(".//geom[@name='floor']")
        body = root.find(".//body[@name='candy']")
        if bottom is None or container is None or floor is None or body is None:
            raise ValueError('Actual named object/slab/floor required')
        if bottom.get('type') != 'box' or floor.get('type') != 'plane':
            raise ValueError('Only captured box-bottom/plane semantics supported')
        if any(node.get(key) for node in (container, bottom, floor) for key in ('quat', 'euler', 'axisangle', 'xyaxes', 'zaxis')):
            raise ValueError('Unrotated captured support frames required')
        pos = [float(v) for v in bottom.get('pos').split()]
        size = [float(v) for v in bottom.get('size').split()]
        base = [float(v) for v in container.get('pos').split()]
        ground = [float(v) for v in floor.get('pos', '0 0 0').split()]
        gravity = [float(v) for v in root.find('option').get('gravity').split()]
        mass = float(body.find('inertial').get('mass'))
        if any(len(v) != 3 or not _finite(v) for v in (pos, size, base, ground, gravity)) or min(size) <= 0 or not math.isfinite(mass) or mass <= 0:
            raise ValueError('Finite positive geometry/mass required')
        mid = base[2] + pos[2]
        return dict(xml_sha256=sha(raw), bin_bottom_world_midplane_z_m=mid,
            bin_bottom_world_top_z_m=mid + size[2], bin_bottom_world_lower_z_m=mid - size[2],
            bin_bottom_half_size_m=size, floor_world_z_m=ground[2], object_mass_kg=mass,
            gravity_m_s2=gravity, weight_N=mass * math.sqrt(sum(v*v for v in gravity)),
            raw_option_attributes=dict(root.find('option').attrib),
            contact_parameters_read_only_no_new_override=True)
    except (ET.ParseError, TypeError, ValueError, AttributeError) as error:
        raise ContactEvidenceRejected('Unsupported captured scene geometry') from error


def analyze_trace(result, trace, geometry):
    """Align every actual native substep to its recorded action/phase interval."""
    evaluation = result['independent_evaluation']
    rows = evaluation['substep_contact_trace']
    if not isinstance(trace, list) or not trace or len(trace) > 10000 or not isinstance(rows, list) or not 0 < len(rows) <= 100000:
        raise ContactEvidenceRejected('Bounded nonempty action/substep trace required')
    ends, starts = [], []
    for index, action in enumerate(trace):
        dispatch = action['dispatch']
        before, after = dispatch['pre_action_stamp'], dispatch['dispatch_stamp']
        if (action['index'] != index or before['boot_id'] != after['boot_id'] or before['clock_domain'] != after['clock_domain'] or
                index and (before['boot_id'] != trace[0]['dispatch']['pre_action_stamp']['boot_id'] or before['clock_domain'] != trace[0]['dispatch']['pre_action_stamp']['clock_domain'])):
            raise ContactEvidenceRejected('Native action index/clock continuity differs')
        start, end = before['monotonic_ns'] / 1e9, after['monotonic_ns'] / 1e9
        if not 0 <= start < end or index and abs(start - ends[-1]) > 1e-8:
            raise ContactEvidenceRejected('Native intervals have a gap/overlap or invalid clock')
        if not isinstance(action['phase_id'], str) or not action['phase_id']:
            raise ContactEvidenceRejected('Original explicit phase label required')
        native = dispatch['native_integration_state']
        if not _finite(native) or abs(native[0] - end) > 1e-8:
            raise ContactEvidenceRejected('Native saved integration time differs from dispatch')
        starts.append(start); ends.append(end)
    aligned, counts, last = [], [0] * len(trace), -1.
    for row in rows:
        if not _finite(row) or len(row) != len(SUBSTEP_COLUMNS) or row[0] <= last:
            raise ContactEvidenceRejected('Exact finite increasing saved substep columns required')
        # Boundaries belong to the completed preceding action, never its future.
        index = bisect_left(ends, row[0] - 1e-8)
        if index == len(trace) or row[0] <= starts[index] + 1e-9:
            raise ContactEvidenceRejected('Substep falls outside its recorded dispatch interval')
        if row[7] > row[8] or min(row[1:4]) < 0:
            raise ContactEvidenceRejected('Saved nonnegative force-magnitude/bounds contract differs')
        aligned.append((row, index, trace[index]['phase_id'])); counts[index] += 1; last = row[0]
    dt = float(geometry['raw_option_attributes']['timestep'])
    if not math.isfinite(dt) or dt <= 0:
        raise ContactEvidenceRejected('Finite positive original timestep required')
    if any(abs((end-start)/dt-count) > 1e-6 for start,end,count in zip(starts,ends,counts)):
        raise ContactEvidenceRejected('Missing/duplicate native substep within recorded action')

    def event(entry):
        if entry is None:
            return None
        row, index, phase = entry
        return dict(action_index=index, phase_id=phase, values=dict(zip(SUBSTEP_COLUMNS, row)))

    release = [entry for entry in aligned if entry[2] == 'release']
    if not release:
        raise ContactEvidenceRejected('Original release-phase evidence required')
    def first(predicate):
        return event(next((entry for entry in release if predicate(entry[0])), None))
    phases = {}
    for phase in dict.fromkeys(action['phase_id'] for action in trace):
        subset = [entry[0] for entry in aligned if entry[2] == phase]
        phases[phase] = dict(substeps=len(subset), start_s=subset[0][0]-dt, end_s=subset[-1][0],
            minimum_point_z_m=min(row[7] for row in subset), maximum_support_magnitude_N=max(row[3] for row in subset),
            maximum_speed_m_s=max(row[9] for row in subset))
    terminal = rows[-1]
    return dict(actions=len(trace), substeps=len(rows), substeps_per_action=sorted(set(counts)),
        original_end_to_end_success=evaluation['end_to_end_success'],
        original_terminal_goal_verified=evaluation['terminal_goal_verified'], per_phase=phases,
        first_free_in_release=first(lambda row: row[1] <= 1e-4 and row[2] <= 1e-4),
        first_bin_contact_in_release=first(lambda row: row[3] > 1e-4),
        first_floor_crossing_in_release=first(lambda row: row[7] < geometry['floor_world_z_m']),
        first_center_below_bottom_midplane=first(lambda row: row[6] < geometry['bin_bottom_world_midplane_z_m']),
        first_support_above_20N=first(lambda row: row[3] > 20),
        terminal=dict(zip(SUBSTEP_COLUMNS, terminal)),
        terminal_support_over_weight=terminal[3] / geometry['weight_N'],
        original_contact_pairs_normals_solver_per_step='NOT_RECORDED',
        signed_world_support_force='NOT_IDENTIFIABLE_FROM_SCALAR_MAGNITUDES')


def inspect_campaign(folder, *, expected_receipt_sha256):
    """Verify retained archive objects, then read four original saved trajectories."""
    folder = Path(folder).absolute()
    receipt_raw = _read(folder/'campaign-qualification.json', expected=expected_receipt_sha256)
    receipt = json.loads(receipt_raw)
    protocol_raw = _read(folder/'frozen-protocol.json', expected=receipt['frozen_protocol_file_sha256'])
    protocol = json.loads(protocol_raw)
    content = dict(protocol); checksum = content.pop('sha256')
    if sha(canonical(content)) != checksum or checksum != receipt['frozen_protocol_sha256']:
        raise ContactEvidenceRejected('Frozen protocol content SHA changed')
    _read(folder/'benchmark-report.json', expected=receipt['benchmark_report_sha256'])
    attempts = receipt['attempts']
    if len(attempts) != 4 or [a['scenario'] for a in attempts] != protocol['scenarios']:
        raise ContactEvidenceRejected('All four frozen scenes, in original order, required')
    outputs, sources = [], {}
    for attempt in attempts:
        identity = _relative(attempt['scenario']['id'])
        root = folder/identity
        manifest_raw = _read(root/'source-archive/manifest.json', expected=attempt['source_archive_manifest_sha256'])
        archive = json.loads(manifest_raw)
        entries = archive['files']; by_locator = {}; files = {}
        if not isinstance(entries, list) or not 0 < len(entries) <= 1024:
            raise ContactEvidenceRejected('Bounded actual archive source inventory required')
        for entry in entries:
            name = _relative(entry['file'])
            if entry['original_locator'] in by_locator or name in files and files[name] != (entry['sha256'], entry['bytes']):
                raise ContactEvidenceRejected('Conflicting archive member/original locator')
            raw = _read(root/'source-archive'/name, expected=entry['sha256'], maximum=64*1024**2)
            if len(raw) != entry['bytes']:
                raise ContactEvidenceRejected('Retained archive member size changed')
            files[name] = (entry['sha256'], entry['bytes']); by_locator[entry['original_locator']] = entry
        selected = {}
        for suffix, expected in (('/manipulation_env.py', ARCHIVED_ENV_SHA), ('/skill_transfer_mujoco.py', ARCHIVED_PROVIDER_SHA)):
            match = [entry for key,entry in by_locator.items() if key.endswith(suffix)]
            if len(match) != 1 or match[0]['sha256'] != expected:
                raise ContactEvidenceRejected('Known archived substep/evaluator source contract required')
            selected[suffix.rsplit('/',1)[1]] = match[0]['sha256']
        xml_entries = [entry for key,entry in by_locator.items() if key.endswith('/target-model.xml')]
        if len(xml_entries) != 1:
            raise ContactEvidenceRejected('One retained original target XML required')
        xml_raw = _read(root/'target-model.xml', expected=xml_entries[0]['sha256'], maximum=1024**2)
        result_raw = _read(root/'target/result.json', expected=attempt['result_sha256'])
        trace_raw = _read(root/'target/target-trace.json', expected=attempt['target_trace_sha256'])
        result, trace = json.loads(result_raw), json.loads(trace_raw)
        if result.get('physical_io') is not False or result.get('learned_target_actor_executed') is not False:
            raise ContactEvidenceRejected('Original SCRIPTED_SIM scope must stay explicit')
        geometry = geometry_from_xml(xml_raw); analysis = analyze_trace(result, trace, geometry)
        if (analysis['original_end_to_end_success'] != attempt['independent_end_to_end'] or
                analysis['original_terminal_goal_verified'] != attempt['terminal_goal_verified'] or
                result['full_target_simulation_success'] != attempt['full_task_success']):
            raise ContactEvidenceRejected('Original independently reported outcomes disagree')
        bindings = dict(result_sha256=sha(result_raw), target_trace_sha256=sha(trace_raw), target_model_xml_sha256=sha(xml_raw),
            source_archive_manifest_sha256=sha(manifest_raw), archived_source_sha256=selected)
        selected = trace[317]
        final = selected['dispatch']['independent_evaluator']['final']
        if (selected['phase_id'] != 'release' or selected['dispatch']['dispatch_stamp']['monotonic_ns'] != 16100000000 or
                final['free_of_fingers'] is not True or final['support_force_N'] != 0 or
                final['height_bounds_m'][0] <= geometry['bin_bottom_world_top_z_m']):
            raise ContactEvidenceRejected('Predeclared index317 is not a free preimpact state')
        native = selected['dispatch']['native_integration_state']
        outputs.append(dict(scenario=attempt['scenario'], original_full_task_success=attempt['full_task_success'],
            geometry=geometry, bindings=bindings, analysis=analysis,
            new_initial_condition_source=dict(original_action_index=317, original_phase_id=selected['phase_id'],
                original_dispatch_stamp=selected['dispatch']['dispatch_stamp'], original_native_state_size=len(native),
                original_native_state_json_sha256=sha(canonical(native)), original_contact_facts=final)))
        sources[identity] = bindings
    # Recheck source inventories after analysis rather than treating a later file
    # at the same locator as the source of computed values.
    for entry in outputs:
        root=folder/entry['scenario']['id']; bindings=entry['bindings']
        for name,key in (('target/result.json','result_sha256'), ('target/target-trace.json','target_trace_sha256'), ('target-model.xml','target_model_xml_sha256'), ('source-archive/manifest.json','source_archive_manifest_sha256')):
            _read(root/name, expected=bindings[key])
        archive = json.loads(_read(root/'source-archive/manifest.json', expected=bindings['source_archive_manifest_sha256']))
        for source in archive['files']:
            _read(root/'source-archive'/_relative(source['file']), expected=source['sha256'], maximum=64*1024**2)
    return dict(schema=SCHEMA, original_campaign_receipt_sha256=sha(receipt_raw),
        original_protocol_file_sha256=sha(protocol_raw), original_protocol_content_sha256=checksum,
        original_benchmark_report_sha256=receipt['benchmark_report_sha256'], attempts=outputs,
        original_sources_rechecked_unchanged=True, substep_columns=list(SUBSTEP_COLUMNS),
        bin_support_semantics='sum_nonnegative_contact_normal_magnitudes_for_object_bin_bottom_only',
        support_is_not_signed_world_vertical_force=True, diagnosis='AMBIGUOUS',
        candidate_cause='high_free_drop_with_thin_slab_contact_branch_and_opposing_floor_bottom_contacts',
        unobserved_alternatives=['other_robot_geometry_contact', 'constraint_solver_or_contact_parameter_sensitivity'],
        dynamics_steps_executed=0, render_calls=0, physical_io=False, learned_policy_executed=False,
        optimizer_admission=False, old_outcomes_amended=False, H4_test=False,
        source_helper_sha256=sha(Path(__file__).read_bytes()))


def probe_protocol(analysis, *, campaign_id):
    """A NEW finite engineering experiment, not a V9 replay/full-task denominator."""
    if analysis.get('schema') != SCHEMA or len(analysis.get('attempts', [])) != 4 or not re.fullmatch('[a-z0-9][a-z0-9-]{5,100}', campaign_id):
        raise ContactEvidenceRejected('Source-bound four-case analysis/new campaign ID required')
    if len({entry['scenario']['id'] for entry in analysis['attempts']}) != 4:
        raise ContactEvidenceRejected('Distinct original scenes required, including both outcomes')
    cases = []
    for entry in analysis['attempts']:
        options = entry['geometry']['raw_option_attributes']
        if options.get('timestep') != '0.002' or options.get('integrator') != 'discrete':
            raise ContactEvidenceRejected('Captured baseline timestep/integrator differs')
        cases.append(dict(original_scenario=entry['scenario'], source_bindings=entry['bindings'],
            original_success=entry['original_full_task_success'], initial_native_action_index=317,
            expected_original_dispatch_time_s=16.1,
            initial_condition_source=entry['new_initial_condition_source']))
    conditions = probe_protocol_conditions()
    return dict(schema=PROBE_SCHEMA, campaign_id=campaign_id, diagnosis_sha256=sha(canonical(analysis)), cases=cases,
        conditions=conditions, order='original_case_order_then_declared_condition_order', attempts_planned=16,
        source_domain='SIMULATION', source_method='ARCHIVED_NATIVE_STATE_NEW_CONTACT_INITIAL_CONDITION',
        initial_state_semantics=dict(fresh_reset=True, new_simulation_time_s=0., warmstart='explicit_zero',
            native_state='recorded_mjSTATE_INTEGRATION_transplanted_then_declared_interventions_and_mj_forward',
            retained_original_absolute_time_only_as_provenance=True, original_initial_state_reconstructed=False,
            original_V9_reexecution=False, no_old_outcome_amendment=True, RNG_seed=7401,
            registered_capture_profile='explorer_manipulation_stationary_initial_v1',
            unsupported_profile='FAIL_BEFORE_FIRST_DYNAMICS_STEP', independently_reexecuted_initial_state='UNQUALIFIED_UNTIL_NEW_PID_PROOF'),
        actuation='hold_all_original_native_ctrl_constant_from_selected_state_no_later_V9_actions',
        slab_top='captured_world_top_unchanged_for_every_condition',
        physics_changes='NEW_COPIED_SIM_XML_ONLY_no_original_env_provider_or_defaults_changed',
        duration_s=1., native_steps_total=14000,
        record=['all_initial_native_and_registered_profile_bytes_and_MJB_SHA', 'all_final_native_states',
            'every_substep_object_pose_velocity_and_bounds', 'every_object_contact_geom_pair_body_pair_normal_position_distance',
            'every_contact_local_wrench_and_signed_world_force_on_object',
            'solver_niter_nnz_warnings_and_available_solver_stats', 'all_setup_failures_unknowns_and_started_attempts'],
        contact_acceptance=dict(terminal_object_minimum_z_tolerance_below_slab_top_m=.001,
            terminal_object_max_speed_m_s=.05, terminal_window_s=.2,
            require_zero_object_robot_contacts_in_terminal_window=True,
            support_force_scope='signed_world_force_separate_from_normal_magnitude',
            complete_object_task_success=False),
        interpretation=dict(thin_slab_hypothesis='opposite_signed_floor_bottom_forces_or_bottom_normal_branch_switch_after_midplane_crossing',
            timestep_hypothesis='sensitivity_to_dt_not_proof_of_unique_solver_cause',
            low_drop_hypothesis='contact_recovery_condition_only_not_a_feasible_full_robot_placement_controller',
            remaining_ambiguity='different_interventions_may_share_multiple_mechanisms'),
        resources=dict(cpu_threads=1, memory_max_bytes=2*1024**3, memory_swap_max_bytes=0,
            GPU=False, EGL=False, renderer=False, max_wall_s=360, disk_budget_bytes=6*1024**3,
            sequential_subprocesses=True),
        partition='development_diagnostic_only', optimizer_admission=False, learned_policy_executed=False,
        physical_io=False, scientific_H4_comparison=False, full_task_success_claimed=False,
        retuning_after_first_attempt=False, exclusions_allowed=False, all_started_attempts_retained=True,
        execution_status='NOT_STARTED_REQUIRES_FROZEN_DRIVER_AND_RESOURCE_WINDOW')


def copied_condition_xml(raw, condition):
    """Explicit new-model interventions; captured top plane/other settings stay."""
    geometry = geometry_from_xml(raw)
    allowed = {entry['id']:entry for entry in probe_protocol_conditions()}
    if condition not in allowed:
        raise ContactEvidenceRejected('One declared contact condition required')
    entry = allowed[condition]
    if condition in ('captured-physics', 'low-drop-rest'):
        return bytes(raw)
    root = ET.fromstring(raw)
    if condition == 'quarter-timestep':
        root.find('option').set('timestep', str(entry['timestep_s']))
    else:
        bottom = root.find(".//body[@name='bin']/geom[@name='bin_bottom']")
        pos = [float(v) for v in bottom.get('pos').split()]
        size = [float(v) for v in bottom.get('size').split()]
        pos[2] += size[2] - entry['slab_half_height_m']; size[2] = entry['slab_half_height_m']
        bottom.set('pos', ' '.join(map(str,pos))); bottom.set('size', ' '.join(map(str,size)))
    result = ET.tostring(root)
    if abs(geometry_from_xml(result)['bin_bottom_world_top_z_m']-geometry['bin_bottom_world_top_z_m']) > 1e-12:
        raise ContactEvidenceRejected('Declared intervention changed top plane')
    return result


def probe_protocol_conditions():
    return [
        dict(id='captured-physics', timestep_s=.002, slab_half_height_m=.004, initial_object='captured_free_preimpact'),
        dict(id='quarter-timestep', timestep_s=.0005, slab_half_height_m=.004, initial_object='captured_free_preimpact'),
        dict(id='thick-bottom-same-top', timestep_s=.002, slab_half_height_m=.020, initial_object='captured_free_preimpact'),
        dict(id='low-drop-rest', timestep_s=.002, slab_half_height_m=.004, initial_object='same_xy_orientation_minimum_z_top_plus_0.015_and_object_velocity_zero'),
    ]


def signed_world_force(frame, local_wrench, *, object_side):
    """MuJoCo contact axes are rows, normal points geom0→geom1 (official API).

    Contact force acts on geom1; invert for an object on geom0. Retain the
    original local wrench/frame too, so this declared conversion is auditable.
    """
    if not _finite(frame) or len(frame) != 9 or not _finite(local_wrench) or len(local_wrench) != 6 or type(object_side) is not int or object_side not in (0,1):
        raise ContactEvidenceRejected('Finite original contact frame/wrench and object side required')
    rows = [frame[n:n+3] for n in (0,3,6)]
    for i in range(3):
        for j in range(3):
            if abs(sum(a*b for a,b in zip(rows[i],rows[j])) - int(i==j)) > 1e-6:
                raise ContactEvidenceRejected('Orthonormal original contact axes required')
    sign = 1 if object_side == 1 else -1
    return [sign * sum(frame[3*axis+world] * local_wrench[axis] for axis in range(3)) for world in range(3)]
