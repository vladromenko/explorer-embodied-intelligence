"""Diagnostic unit semantics and actual retained matched-branch evidence."""
import hashlib
import importlib.util
import json
from pathlib import Path

import numpy as np
import pytest

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('diagnose_mobile_hold',ROOT/'scripts/diagnose_mobile_hold.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)


def test_diagnostic_summary_does_not_credit_unheld_travel_or_hide_height_loss():
    trace=np.zeros((8,17));trace[:,0]=np.arange(8)*.002
    trace[:4,1:3]=1.;trace[:,3]=1.;trace[:,5]=[.24]*4+[.02]*4
    trace[:,6]=[0.,.05,.10,.15,.3,.4,.5,.6]
    trace[:,12]=1.;trace[:,14]=.24
    trace[:,15]=.01;trace[:,16]=.03
    result=module.summarize_contacts(trace,np.zeros(3),.24,.002)
    assert result['continuous_bilateral_contact_max_s']==pytest.approx(.008)
    assert result['held_base_displacement_max_m']==pytest.approx(.15)
    assert result['base_displacement_m']==pytest.approx(.6)
    assert result['first_height_loss_after_s']==pytest.approx(.008)
    assert not result['final_bilateral_contact'] and not result['physically_validated']


def test_actual_matched_hold_receipts_share_native_initial_state_and_fixed_arm_requests():
    folder=Path.home()/'.local/share/explorer/state/qualification/mobile-candy-kitchen-20261026/matched-hold-probe-v2'
    if not (folder/'result.json').is_file():pytest.skip('Actual Homebrain matched hold qualification artifact required')
    report=json.loads((folder/'result.json').read_text())
    assert report['implementation_sha256']==module.checksum(ROOT/'scripts/diagnose_mobile_hold.py')
    assert report['initial_snapshot_restoration_is_explicit'] and not report['state_forcing_during_branch']
    assert not report['physical_io']
    initial=[]
    for name,receipt in report['branches'].items():
        archive=folder/name/'trajectory.npz'
        assert module.checksum(archive)==receipt['trajectory_sha256']
        with np.load(archive,allow_pickle=False) as data:
            assert all(np.isfinite(data[key]).all() for key in data.files)
            assert data['native_integration_state'].shape[0]==data['requested_endpoint9'].shape[0]+1
            initial.append(data['native_integration_state'][0].copy())
            assert hashlib.sha256(initial[-1].tobytes()).hexdigest()==receipt['native_initial_state_sha256']
            np.testing.assert_allclose(data['requested_endpoint9'][:,:6],np.tile(data['initial_command9'][:6],(len(data['requested_endpoint9']),1)))
            if name=='stationary_hold':
                np.testing.assert_array_equal(data['requested_endpoint9'][:,6:],0.)
                assert receipt['base_displacement_m']<.00001
            else:
                assert np.max(abs(np.diff(data['requested_endpoint9'][:,6])))/.05 < .02
                assert receipt['base_displacement_m']>.6
        assert not receipt['whole_task_trial'] and not receipt['actuator_or_friction_settings_changed']
        assert receipt['first_height_loss_after_s'] is not None and not receipt['final_bilateral_contact']
    np.testing.assert_array_equal(initial[0],initial[1])
