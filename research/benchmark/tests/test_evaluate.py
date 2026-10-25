import copy
import importlib.util
from pathlib import Path

import pytest

path = Path(__file__).parents[1] / "evaluate.py"
spec = importlib.util.spec_from_file_location("explorer_research_eval", path)
evaluation = importlib.util.module_from_spec(spec)
spec.loader.exec_module(evaluation)


def protocol():
    p = dict(protocol_id="test-accounting", schema_version="1.0", task_ids=["reach"], conditions=["target_only"],
             success_predicates={"reach": "independent target-distance evidence"}, exclusion_reasons=["camera_corrupt_before_start"],
             frozen_at="2026-10-26T00:00:00+03:00", split_manifest={"disjoint_by": ["episode_ids", "session_ids"],
             "train": {"episode_ids": ["train-1"], "session_ids": ["train-session"]},
             "development": {"episode_ids": ["dev-1"], "session_ids": ["dev-session"]},
             "sealed_test": {"episode_ids": ["test-1"], "session_ids": ["test-session"]}})
    p["sha256"] = evaluation.protocol_hash(p)
    return p


def trial(p, i, outcome, domain="SIMULATION"):
    return dict(trial_id=f"trial-{i}", run_id="run-1", task_id="reach", condition="target_only", source_domain=domain,
                protocol_sha256=p["sha256"], outcome=outcome, validity="VALID", checkpoint_sha256="a" * 64,
                evidence_artifact_ids=["artifact-1"] if outcome == "SUCCESS" else [])


def test_unknowns_are_not_silently_dropped_and_physical_not_pooled_with_sim():
    p = protocol()
    result = evaluation.summarize_trials(p, [trial(p, 1, "SUCCESS"), trial(p, 2, "UNKNOWN"), trial(p, 3, "FAILURE", "PHYSICAL")])
    sim = next(group for group in result["groups"] if group["source_domain"] == "SIMULATION")
    assert sim["success_rate_identification_bounds"] == [.5, 1.]
    assert sim["attempts"] == 2
    assert len(result["groups"]) == 2


def test_episode_and_session_split_leakage_rejected():
    p = protocol()
    p["split_manifest"]["sealed_test"]["session_ids"] = ["train-session"]
    p["sha256"] = evaluation.protocol_hash(p)
    with pytest.raises(ValueError, match="Split leakage"):
        evaluation.summarize_trials(p, [])


def test_duplicate_trials_and_unregistered_exclusions_rejected():
    p = protocol()
    t = trial(p, 1, "FAILURE")
    with pytest.raises(ValueError, match="Duplicate"):
        evaluation.summarize_trials(p, [t, copy.deepcopy(t)])
    t.update(validity="INVALID", exclusion_reason="policy_failed")
    with pytest.raises(ValueError, match="preregistered"):
        evaluation.summarize_trials(p, [t])


def test_protocol_content_cannot_change_after_trial_collection():
    p = protocol()
    p["success_predicates"]["reach"] = "easier predicate selected after seeing outcomes"
    with pytest.raises(ValueError, match="hash"):
        evaluation.summarize_trials(p, [])


def test_wilson_known_reference_and_empty_data():
    lo, hi = evaluation.wilson_interval(1, 2)
    assert lo == pytest.approx(.0945312, abs=1e-6)
    assert hi == pytest.approx(.9054688, abs=1e-6)
    assert evaluation.wilson_interval(0, 0) is None


def test_missing_retention_data_not_imputed_as_failure():
    result = evaluation.retention_metrics([{"a": .8}, {"a": .7, "b": .9}, {"a": .6, "b": None, "c": .5}], ["a", "b", "c"])
    assert result["forgetting_by_task"]["a"] == pytest.approx(.2)
    assert result["forgetting_by_task"]["b"] is None
    assert result["missing_tasks"] == ["b"]


def test_symbolic_composition_requires_target_compatibility_and_domain_evidence():
    cards = [dict(skill_id="pick", compatible_robot_ids=["robot-a"], required_capabilities=["arm"],
                  validated_domains=["SIMULATION"], preconditions=["object_on_table"], postconditions=["holding_object"]),
             dict(skill_id="place", compatible_robot_ids=["robot-a"], required_capabilities=["arm"],
                  validated_domains=["SIMULATION"], preconditions=["holding_object"], postconditions=["object_in_box"])]
    assert evaluation.compose_skills(cards, {"object_on_table"}, {"object_in_box"}, "robot-a", {"arm"}, "SIMULATION") == ["pick", "place"]
    assert evaluation.compose_skills(cards, {"object_on_table"}, {"object_in_box"}, "robot-a", {"arm"}, "PHYSICAL") is None
    assert evaluation.compose_skills(cards, {"object_on_table"}, {"object_in_box"}, "robot-b", {"arm"}, "SIMULATION") is None
