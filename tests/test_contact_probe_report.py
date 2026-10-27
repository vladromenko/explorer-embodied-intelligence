"""Independent report checks on small explicit raw/native fixtures, no physics."""
from copy import deepcopy
import importlib.util
from pathlib import Path

import pytest


@pytest.fixture
def report():
    path=Path(__file__).parents[1]/'scripts/summarize_contact_probes.py'
    spec=importlib.util.spec_from_file_location('contact_probe_report_fixture',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module


def fixture_rows(*,opposing=False):
    weight=.123606;frame=[0.,0.,1.,1.,0.,0.,0.,1.,0.]
    def pair(names,side,normal):
        return dict(geom_names=names,object_side=side,local_wrench=[normal,0.,0.,0.,0.,0.],
            contact_frame_rows=frame,distance_m=-.01,
            world_force_on_object_N=[0.,0.,normal*(1 if side==1 else -1)])
    contacts=[pair(['bin_bottom','candy_contact'],1,weight)]
    if opposing:contacts=[pair(['candy_contact','bin_bottom'],0,33.),pair(['floor','candy_contact'],1,33.+weight)]
    return [dict(index=i,pre_step_time_s=i*.002,post_step_time_s=(i+1)*.002,contacts=deepcopy(contacts),
        bottom_signed_world_force_N=[0.,0.,-33. if opposing else weight],
        floor_signed_world_force_N=[0.,0.,33.+weight if opposing else 0.],
        bottom_normal_magnitude_N=33. if opposing else weight,other_object_contacts=[],
        object=dict(minimum_point_z_m=-.01 if opposing else .008),inside_bottom_xy=True,
        maximum_object_point_speed_m_s=0.,solver=dict(niter=[2,0],warnings=[dict(number=0,lastinfo=0)])) for i in range(500)]


def test_opposing_contact_direction_is_recomputed_and_static_force_balance_does_not_imply_recovery(report):
    actual=report.recompute_contact_rows(fixture_rows(opposing=True),.002,.008,.123606)
    assert actual['recomputed_contact_recovered'] is False
    assert actual['terminal_mean_bottom_world_Z_N']==pytest.approx(-33.)
    assert actual['terminal_mean_floor_world_Z_N']==pytest.approx(33.123606)
    assert actual['terminal_net_world_Z_contact_N']==pytest.approx(.123606)
    event=actual['first_large_opposing_floor_bottom']
    assert event['bottom_contacts'][0]['object_normal_world']==[-0.,-0.,-1.]
    assert actual['maximum_force_conversion_error']==0


def test_actual_terminal_inside_guard_is_not_dropped_after_support_and_settle(report):
    rows=fixture_rows();rows[-1]['inside_bottom_xy']=False
    actual=report.recompute_contact_rows(rows,.002,.008,.123606)
    assert not actual['recomputed_contact_recovered']
    assert actual['terminal_predicates']==dict(supported=True,settled=True,other_contact_free=True,inside=False)


@pytest.mark.parametrize('kind',['clock','missing','force','sum','normal','warning'])
def test_tampered_or_incomplete_raw_measurements_are_rejected(report,kind):
    rows=fixture_rows()
    if kind=='clock':rows[17]['post_step_time_s']+=.0001
    if kind=='missing':rows.pop()
    if kind=='force':rows[17]['contacts'][0]['world_force_on_object_N'][2]+=1
    if kind=='sum':rows[17]['bottom_signed_world_force_N'][2]+=1
    if kind=='normal':rows[17]['bottom_normal_magnitude_N']+=1
    if kind=='warning':rows[17]['solver']['warnings'][0]['number']=1
    with pytest.raises(report.ProbeReportRejected):report.recompute_contact_rows(rows,.002,.008,.123606)


def test_exact_registered_initial_native_bytes_and_all_clocks_are_checked(report,tmp_path):
    import numpy as np
    initial=np.array([0.,2.,3.],dtype=np.float64);array=np.repeat(initial[None,:],4,axis=0);array[:,0]=[0.,.002,.004,.006]
    np.save(tmp_path/'initial.npy',initial,allow_pickle=False);np.save(tmp_path/'states.npy',array,allow_pickle=False)
    assert report.native_check(tmp_path/'states.npy',tmp_path/'initial.npy',3,.002)['rows']==4
    array[0,1]=4.;np.save(tmp_path/'states.npy',array,allow_pickle=False)
    with pytest.raises(report.ProbeReportRejected,match='first row'):report.native_check(tmp_path/'states.npy',tmp_path/'initial.npy',3,.002)
    array[0,1]=2.;array[2,0]=.005;np.save(tmp_path/'states.npy',array,allow_pickle=False)
    with pytest.raises(report.ProbeReportRejected,match='time differs'):report.native_check(tmp_path/'states.npy',tmp_path/'initial.npy',3,.002)


def test_raw_regular_byte_sha_and_aliases_are_checked(report,tmp_path):
    path=tmp_path/'raw.jsonl';path.write_bytes(b'EXPLICIT_RAW_BYTES')
    original=report.checked_file(path);path.write_bytes(b'CHANGED_RAW_BYTES')
    with pytest.raises(report.ProbeReportRejected,match='SHA'):report.checked_file(path,original['sha256'])
    alias=tmp_path/'alias';alias.symlink_to(path)
    with pytest.raises(report.ProbeReportRejected,match='nonaliased'):report.checked_file(alias)
