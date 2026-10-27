"""Pure metadata: paired denominator, signed metrics and refusal of altered scope."""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest

ROOT=Path(__file__).parents[1]
SCRIPT=ROOT/'scripts/plot_contact_probe_v10.py'
spec=importlib.util.spec_from_file_location('contact_plot',SCRIPT)
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)


def fixture():
    report=json.loads((ROOT/'research/audits/transfer-contact-probe-v10-results-20261027.json').read_bytes())['independent_report']
    cases=sorted({row['original_case'] for row in report['attempts']})
    return report,cases


def test_actual_sixteen_attempts_keep_four_paired_cases_not_fourteen_thousand_independent_trials():
    report,cases=fixture();rows,summary=module.flatten(report,cases)
    assert len(rows)==16 and sum(row['verified_substeps'] for row in rows)==14000
    assert [row['matched_source_cases'] for row in summary]==[4]*4
    assert [row['contact_recovered'] for row in summary]==[2,3,4,4]
    assert [(row['gained_contact_recovery'],row['lost_contact_recovery']) for row in summary]==[(0,0),(1,0),(2,0),(2,0)]
    assert all(sum(row[key] for key in ('both_recovered','gained_contact_recovery','lost_contact_recovery','both_not_recovered'))==4 for row in summary)
    baseline=[row for row in rows if row['condition']=='captured-physics' and not row['contact_recovered']]
    assert all(row['terminal_mean_bottom_world_Z_N']< -20 for row in baseline)
    assert all(row['terminal_mean_floor_world_Z_N']>20 for row in baseline)
    assert all(row['raw_SHA_binding_basis']==module.RAW_BASIS for row in rows)


@pytest.mark.parametrize('change',[
    lambda r:r['attempts'].append(deepcopy(r['attempts'][0])),
    lambda r:r['attempts'].pop(),
    lambda r:r['attempts'][0]['analysis'].update(recomputed_contact_recovered=True),
    lambda r:r['attempts'][0]['analysis'].update(terminal_mean_bottom_world_Z_N=float('nan')),
    lambda r:r.update(physics_accuracy_validated=True)])
def test_duplicate_missing_mislabelled_nonfinite_or_broadened_claim_is_rejected(change):
    report,cases=fixture();change(report)
    with pytest.raises(module.PlotRejected):module.flatten(report,cases)


def test_penetration_uses_negative_part_not_absolute_distance():
    report,cases=fixture();attempt=report['attempts'][0];attempt['analysis']['minimum_pair_signed_distance_m']=.002
    rows,_=module.flatten(report,cases);chosen=next(row for row in rows if row['attempt_id']==attempt['attempt_id'])
    assert chosen['minimum_contact_signed_distance_m']==.002 and chosen['maximum_recorded_contact_penetration_mm']==0


def test_import_does_not_allocate_matplotlib_numpy_physics_or_policy():
    code='import runpy,sys;runpy.run_path('+repr(str(SCRIPT))+');assert not ({"matplotlib","numpy","mujoco","torch","OpenGL"}&set(sys.modules))'
    outcome=subprocess.run([sys.executable,'-c',code],capture_output=True,text=True)
    assert outcome.returncode==0,outcome.stdout+outcome.stderr
