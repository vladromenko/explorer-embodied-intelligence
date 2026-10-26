"""ENGINEERING_GLUE: server-owned portable scientific Study definitions.

A public request selects a registered Study ID. No caller supplies a source
path, evaluator, source domain, outcome or optimizer partition.
"""
from __future__ import annotations

from pathlib import Path

from explorer_shared.study_evidence import ContextFile,StudyDefinition,StudyRejected,inspect_study
from explorer_shared.study_bundle import export_study_bundle

STUDY_ID='transfer-hover-development-v9-20261026'
CAMPAIGN_ROOT=Path('/mnt/explorer-data/explorer-work/transfer-hover-development-v9-20261026')
WORK_ROOT=CAMPAIGN_ROOT.parent
REPOSITORY_ROOT=Path(__file__).resolve().parents[3]
PROTOCOL_SHA256='7bc5db9056d99de459b299036fa9e240650a190bb270ce481664afba673ba744'
AUDIT_SHA256='d5fc6e98b373022b27e5b36dc0da80c5b8a1080960f2426a7aa32809c261a8c2'
BENCHMARK_SHA256='352c62c3d58207dadb368f9250b5b14195addf12046d533077b9451f1d715f1a'


def registered_studies():
    return [dict(study_id=STUDY_ID,title='Перенос hover-release V9 · четыре SCRIPTED_SIM попытки',
        source_method='SCRIPTED_SIM',source_domain='SIMULATION',attempts=4,
        frozen_protocol_sha256=PROTOCOL_SHA256,target_partition='development',
        target_optimizer_allowed=False,learned_policy_executed=False,physical_io=False,
        declared_original_reexecution_completeness='PARTIAL')]


def registered_definition(study_id):
    if study_id!=STUDY_ID:raise StudyRejected('Unknown server-registered Study ID')
    context={
        'campaign-driver.py':ContextFile(WORK_ROOT/'transfer-hover-development-v9-driver-20261026.py','8674d6f2cb107d18f345b9c6b02aa9cd9a9a3808f36d6cef6d442306887221d4','frozen_campaign_driver_data_only'),
        'preflight-draft-protocol.json':ContextFile(WORK_ROOT/'transfer-hover-development-v9-before-static-preflight-fix-20261026.json','55f2f30b47d97136d61a584a4063690ba5e2ff031d2caf91a21842c7f3fed441','pre_any_trial_amendment'),
        'preflight-draft-driver.py':ContextFile(WORK_ROOT/'transfer-hover-development-v9-driver-20261026-before-static-preflight-fix.py','162fb160ac1d3f5fb590f6c758818cb5c36e8a8a9e3a5664f814f5f0e5163a81','pre_any_trial_amendment'),
        'derived-contact-recomputation.json':ContextFile(WORK_ROOT/'transfer-hover-development-v9-contact-analysis-20261026/contact-recomputation.json','4704ec90c369394fb4e648fa4e250387047e8532dfed97e8af64e1f067fa4b03','derived_forward_only_not_original_force_capture'),
        'derived-contact-driver.py':ContextFile(WORK_ROOT/'transfer-v9-contact-analysis-driver-20261026.py','bac8df39f07a1b00e7ace5d125d1fd8b899ca731d66e088eda9390933a2829a1','derived_contact_driver_data_only')}
    return StudyDefinition(STUDY_ID,CAMPAIGN_ROOT,PROTOCOL_SHA256,
        ContextFile(REPOSITORY_ROOT/'research/audits/transfer-hover-development-v9-results-20261026.json',AUDIT_SHA256,'public_frozen_campaign_audit'),
        ContextFile(REPOSITORY_ROOT/'research/benchmark/evaluate.py',BENCHMARK_SHA256,'allowlisted_pure_statistical_evaluator'),context)


def inspect_registered_study(study_id):
    return inspect_study(registered_definition(study_id))


def export_registered_study(study_id,destination):
    """Worker-only destination; HTTP bodies must never supply this path."""
    return export_study_bundle(inspect_registered_study(study_id),destination)
