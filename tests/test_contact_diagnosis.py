"""Small explicit saved JSON/XML fixtures; no simulator/policy/device imports."""
from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys
import xml.etree.ElementTree as ET

import pytest

from explorer_shared import contact_diagnosis as contact


XML = b'''<mujoco><option timestep="0.002" integrator="discrete" gravity="0 0 -9.81" iterations="70"/>
<worldbody><geom name="floor" type="plane"/><body name="bin" pos="0.2 0.1 0">
<geom name="bin_bottom" type="box" pos="0 0 .004" size=".073 .063 .004" friction=".8 .005 .0001"/>
<geom name="bin_side" type="box" size=".01 .01 .01"/></body><body name="candy">
<inertial mass=".0126"/><geom name="candy_contact" type="box" size=".02 .012 .01"/></body></worldbody></mujoco>'''


def saved_trace():
    trace=[]
    for index in range(2):
        before=dict(boot_id='fixture-boot',clock_domain='fixture-domain',monotonic_ns=index*4000000)
        after=dict(before,monotonic_ns=(index+1)*4000000)
        trace.append(dict(index=index,phase_id='release',dispatch=dict(pre_action_stamp=before,dispatch_stamp=after,native_integration_state=[(index+1)*.004,1.])))
    rows = [
        [.002, 0.,0.,0., .2,.1,.2, .19,.21, .1],
        [.004, 0.,0.,1., .2,.1,.015, .005,.025, 2.],
        [.006, 0.,0.,32., .2,.1,.003, -.007,.013, .5],
        [.008, 0.,0.,33., .2,.1,0., -.01,.01, 0.],
    ]
    result=dict(independent_evaluation=dict(substep_contact_trace=rows,end_to_end_success=False,terminal_goal_verified=False))
    return result,trace


def test_saved_contact_counts_phase_alignment_and_scalar_force_does_not_forge_direction():
    result,trace=saved_trace(); actual=contact.analyze_trace(result,trace,contact.geometry_from_xml(XML))
    assert actual['actions']==2 and actual['substeps']==4 and actual['substeps_per_action']==[2]
    assert actual['first_bin_contact_in_release']['action_index']==0  # exact boundary completes action0
    assert actual['first_center_below_bottom_midplane']['action_index']==1
    assert actual['first_center_below_bottom_midplane']['values']['simulation_time_s']==.006
    assert actual['first_support_above_20N']['values']['bin_bottom_normal_magnitude_N']==32.
    assert actual['terminal_support_over_weight']==pytest.approx(33/(.0126*9.81))
    assert actual['signed_world_support_force']=='NOT_IDENTIFIABLE_FROM_SCALAR_MAGNITUDES'
    assert actual['original_contact_pairs_normals_solver_per_step']=='NOT_RECORDED'
    assert actual['original_end_to_end_success'] is False


@pytest.mark.parametrize('mutation', ['missing','duplicate','clock','state-time','nonfinite','negative-magnitude','interval-gap'])
def test_missing_or_inconsistent_original_substeps_rejected(mutation):
    result,trace=saved_trace();rows=result['independent_evaluation']['substep_contact_trace']
    if mutation=='missing':rows.pop(1)
    if mutation=='duplicate':rows.insert(1,deepcopy(rows[0]))
    if mutation=='clock':trace[1]['dispatch']['pre_action_stamp']['boot_id']='other'
    if mutation=='state-time':trace[1]['dispatch']['native_integration_state'][0]=.010
    if mutation=='nonfinite':rows[2][3]=float('nan')
    if mutation=='negative-magnitude':rows[2][3]=-32
    if mutation=='interval-gap':trace[1]['dispatch']['pre_action_stamp']['monotonic_ns']+=1000000
    with pytest.raises(contact.ContactEvidenceRejected):contact.analyze_trace(result,trace,contact.geometry_from_xml(XML))


def test_unseen_success_is_not_relabelled_by_historical_release_contact():
    result,trace=saved_trace(); result['independent_evaluation'].update(end_to_end_success=True,terminal_goal_verified=False)
    actual=contact.analyze_trace(result,trace,contact.geometry_from_xml(XML))
    assert actual['original_end_to_end_success'] is True and actual['original_terminal_goal_verified'] is False


def flatten_xml(raw):
    return [(node.tag,tuple(sorted(node.attrib.items()))) for node in ET.fromstring(raw).iter()]


def test_model_intervention_changes_only_declared_timestep_or_slab_bottom_with_fixed_top():
    original=flatten_xml(XML); before=contact.geometry_from_xml(XML)
    assert contact.copied_condition_xml(XML,'captured-physics')==XML
    assert contact.copied_condition_xml(XML,'low-drop-rest')==XML
    smaller=contact.copied_condition_xml(XML,'quarter-timestep');after=flatten_xml(smaller)
    differences=[(a,b) for a,b in zip(original,after) if a!=b]
    assert len(differences)==1 and differences[0][0][0]=='option'
    assert contact.geometry_from_xml(smaller)['raw_option_attributes']['timestep']=='0.0005'
    thicker=contact.copied_condition_xml(XML,'thick-bottom-same-top'); after=flatten_xml(thicker)
    differences=[(a,b) for a,b in zip(original,after) if a!=b]
    assert len(differences)==1 and dict(differences[0][0][1])['name']=='bin_bottom'
    actual=contact.geometry_from_xml(thicker)
    assert actual['bin_bottom_world_top_z_m']==pytest.approx(before['bin_bottom_world_top_z_m'])
    assert actual['bin_bottom_world_midplane_z_m']==pytest.approx(-.012)
    assert actual['bin_bottom_world_lower_z_m']==pytest.approx(-.032)
    assert actual['object_mass_kg']==before['object_mass_kg']
    assert XML.endswith(b'</mujoco>')  # caller's immutable input remains original
    with pytest.raises(contact.ContactEvidenceRejected):contact.copied_condition_xml(XML,'retune-until-success')


@pytest.mark.parametrize('xml', [XML.replace(b'<worldbody>',b'<include file="unknown.xml"/><worldbody>'), XML.replace(b'name="bin"',b'name="bin" quat="1 0 0 0"'),XML.replace(b'.0126',b'nan')])
def test_unmeasured_scene_geometry_is_not_assumed(xml):
    with pytest.raises(contact.ContactEvidenceRejected):contact.geometry_from_xml(xml)


def test_signed_force_uses_transposed_row_axes_and_actual_object_side():
    # contact normal along +worldZ, tangent1 +worldX, tangent2 +worldY
    frame=[0.,0.,1., 1.,0.,0., 0.,1.,0.]; wrench=[10.,2.,3.,0.,0.,0.]
    assert contact.signed_world_force(frame,wrench,object_side=1)==[2.,3.,10.]
    assert contact.signed_world_force(frame,wrench,object_side=0)==[-2.,-3.,-10.]
    with pytest.raises(contact.ContactEvidenceRejected):contact.signed_world_force([0.]*9,wrench,object_side=1)
    with pytest.raises(contact.ContactEvidenceRejected):contact.signed_world_force(frame,wrench,object_side=True)


def analysis_fixture():
    return dict(schema=contact.SCHEMA,attempts=[dict(scenario=dict(id='fixture-case-'+str(i),seed=100+i),
        original_full_task_success=i%2==0,geometry=contact.geometry_from_xml(XML),bindings={'fixture':'MOCK_ONLY'},
        new_initial_condition_source={'fixture':'NOT_REAL_NATIVE_STATE'}) for i in range(4)])


def test_probe_keeps_positive_negative_pairs_and_never_becomes_old_replay_or_optimizer_data():
    analysis=analysis_fixture(); before=contact.canonical(analysis)
    plan=contact.probe_protocol(analysis,campaign_id='new-contact-fixture')
    assert len(plan['cases'])==4 and [c['original_success'] for c in plan['cases']]==[True,False,True,False]
    assert plan['attempts_planned']==16 and len(plan['conditions'])==4
    assert plan['native_steps_total']==sum(round(plan['duration_s']/c['timestep_s']) for c in plan['conditions'])*4
    assert not plan['initial_state_semantics']['original_V9_reexecution']
    assert not plan['initial_state_semantics']['original_initial_state_reconstructed']
    assert not plan['optimizer_admission'] and not plan['full_task_success_claimed'] and not plan['scientific_H4_comparison']
    assert plan['execution_status'].startswith('NOT_STARTED') and not plan['retuning_after_first_attempt']
    assert not plan['resources']['EGL'] and plan['resources']['memory_max_bytes']==2*1024**3
    assert contact.canonical(analysis)==before


def test_probe_does_not_drop_failed_case_or_accept_changed_baseline():
    analysis=analysis_fixture();analysis['attempts'][1]['scenario']['id']=analysis['attempts'][0]['scenario']['id']
    with pytest.raises(contact.ContactEvidenceRejected):contact.probe_protocol(analysis,campaign_id='new-contact-fixture')
    analysis=analysis_fixture();analysis['attempts'][2]['geometry']['raw_option_attributes']['integrator']='Euler'
    with pytest.raises(contact.ContactEvidenceRejected):contact.probe_protocol(analysis,campaign_id='new-contact-fixture')


def test_source_sha_changed_and_alias_escape_fail_before_parsing(tmp_path):
    path=tmp_path/'bytes.json';path.write_bytes(b'{}');expected=contact.sha(path.read_bytes());path.write_bytes(b'[]')
    with pytest.raises(contact.ContactEvidenceRejected):contact._read(path,expected=expected)
    alias=tmp_path/'alias';alias.symlink_to(path)
    with pytest.raises(contact.ContactEvidenceRejected):contact._read(alias)
    for name in ('../secret','/absolute','a/../x','a\\x','./a'):
        with pytest.raises(contact.ContactEvidenceRejected):contact._relative(name)


def test_controlplane_diagnosis_import_is_simulator_and_torch_free():
    script="import sys; import explorer_shared.contact_diagnosis; assert not any(k.split('.')[0] in ('mujoco','torch','numpy','explorer_sim') for k in sys.modules)"
    subprocess.run([sys.executable,'-c',script],check=True,env=dict(os.environ,PYTHONPATH=str(Path(__file__).parents[1]/'shared')))
