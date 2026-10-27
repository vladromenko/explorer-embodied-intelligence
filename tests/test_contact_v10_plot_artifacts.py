"""Actual source/CSV/figure proof relationships; no render or private source IO."""
import csv
import hashlib
import json
from pathlib import Path

ROOT=Path(__file__).parents[1];PAPER=ROOT/'research/paper';FIGURES=PAPER/'figures'


def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def test_actual_plot_artifacts_match_source_locked_double_render_and_resource_proof():
    proof=json.loads((FIGURES/'contact_development_v10_PROVENANCE.json').read_bytes())
    build=json.loads((PAPER/'BUILD_CONTACT_V10_FIGURE_VERIFICATION.json').read_bytes())
    assert proof['plot_script_sha256']==sha(ROOT/'scripts/plot_contact_probe_v10.py')
    assert proof['public_receipt_sha256']==sha(ROOT/'research/audits/transfer-contact-probe-v10-results-20261027.json')
    assert len(proof['input_files'])==84 and proof['matched_source_cases_per_condition']==4 and proof['attempts']==16
    assert proof['total_verified_contact_substeps']==14000 and proof['discarded_constructor_reset_steps']==3200
    assert proof['repeated_render_same_process_and_environment_only'] and all(proof['repeated_render_bytes_equal'].values())
    for name in ('contact_development_v10.pdf','contact_development_v10.png'):
        assert sha(FIGURES/name)==proof['output_sha256']['render1/'+name]==proof['output_sha256']['render2/'+name]
    for name,expected in build['artifact_sha256'].items():assert sha(FIGURES/name)==expected
    resources=json.loads((FIGURES/'contact_development_v10_RESOURCES.json').read_bytes())
    assert resources['failure'] is None and 0<resources['memory_peak_bytes']<=512*1024**2 and resources['swap_peak_bytes']==0
    assert not proof['producer_raw_hash_claim_added'] and proof['raw_bytes_rehashed_by_plotter']
    assert not proof['heldout_generalization_test'] and not proof['physics_accuracy_validated'] and not proof['learned_repair_tested']


def test_actual_CSV_retains_all_matched_signed_metrics_and_no_population_inference():
    with (FIGURES/'contact_development_v10.csv').open(newline='') as stream:rows=list(csv.DictReader(stream))
    with (FIGURES/'contact_development_v10_paired_counts.csv').open(newline='') as stream:counts=list(csv.DictReader(stream))
    report=json.loads((ROOT/'research/audits/transfer-contact-probe-v10-results-20261027.json').read_bytes())['independent_report']
    attempts={row['attempt_id']:row for row in report['attempts']};assert len(rows)==len(attempts)==16
    assert {row['attempt_id'] for row in rows}==set(attempts)
    for row in rows:
        source=attempts[row['attempt_id']];analysis=source['analysis']
        assert float(row['terminal_mean_bottom_world_Z_N'])==analysis['terminal_mean_bottom_world_Z_N']
        assert float(row['maximum_recorded_contact_penetration_mm'])==1000*max(0,-analysis['minimum_pair_signed_distance_m'])
        assert row['contact_recovered']==str(analysis['recomputed_contact_recovered'])
    assert [int(row['matched_source_cases']) for row in counts]==[4]*4
    assert [int(row['contact_recovered']) for row in counts]==[2,3,4,4]
    assert [(int(row['gained_contact_recovery']),int(row['lost_contact_recovery'])) for row in counts]==[(0,0),(1,0),(2,0),(2,0)]


def test_full_paper_compile_stays_unverified_and_historical_build_is_unchanged():
    build=json.loads((PAPER/'BUILD_CONTACT_V10_FIGURE_VERIFICATION.json').read_bytes())
    assert build['standalone_figure_compiled'] and build['pdf_pages']==1 and build['fonts_embedded'] and 'CID TrueType' in build['font_type']
    assert not build['current_full_paper_compiled'] and build['current_full_paper_page_count'] is None and not build['submission_ready']
    assert build['previous_full_paper_build_receipt_sha256']==sha(PAPER/'BUILD_VERIFICATION.json') and build['previous_full_paper_build_is_historical']
    for name,expected in build['current_source_sha256'].items():assert sha(PAPER/name)==expected
