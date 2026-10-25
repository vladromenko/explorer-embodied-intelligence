"""method_id=ENG-EVAL-001; deterministic evaluation accounting, not a learned method.

Original engineering implementation of preregistered trial accounting, Wilson
intervals and retention. All figures require measured trial records; missing
outcomes remain unknown. See research/methods/METHOD_REGISTRY.yaml.
"""
from __future__ import annotations

import argparse
from collections import defaultdict, deque
import hashlib
import json
import math
import re
from pathlib import Path
from statistics import NormalDist, mean
from typing import Any, Iterable


OUTCOMES = {"SUCCESS", "FAILURE", "UNKNOWN"}


def protocol_hash(protocol: dict[str, Any]) -> str:
    """Hash the actual frozen protocol, without a self-referential hash field."""
    body = {key: value for key, value in protocol.items() if key != "sha256"}
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def verify_protocol(protocol: dict[str, Any]) -> None:
    required = {"protocol_id", "schema_version", "task_ids", "conditions", "success_predicates",
                "split_manifest", "exclusion_reasons", "frozen_at", "sha256"}
    missing = required - protocol.keys()
    if missing:
        raise ValueError(f"Protocol missing fields: {sorted(missing)}")
    if not protocol["frozen_at"] or protocol["sha256"] != protocol_hash(protocol):
        raise ValueError("Protocol must be frozen and its content hash must match")
    if not protocol["task_ids"] or not protocol["conditions"]:
        raise ValueError("A protocol needs tasks and comparison conditions")
    assert_disjoint_splits(protocol["split_manifest"])


def assert_disjoint_splits(splits: dict[str, Any]) -> None:
    """Reject episode/session leakage; optional object/scene splits are explicit."""
    names = [name for name in ("train", "development", "sealed_test") if name in splits]
    if set(names) != {"train", "development", "sealed_test"}:
        raise ValueError("train, development and sealed_test manifests are required")
    dimensions = splits.get("disjoint_by", ["episode_ids", "session_ids"])
    if not {"episode_ids", "session_ids"}.issubset(dimensions):
        raise ValueError("Episode and session disjointness cannot be disabled")
    for dimension in dimensions:
        for index, left in enumerate(names):
            if dimension not in splits[left]:
                raise ValueError(f"Split {left} has no {dimension}")
            for right in names[index + 1:]:
                overlap = set(splits[left][dimension]) & set(splits[right].get(dimension, []))
                if overlap:
                    raise ValueError(f"Split leakage in {dimension}: {left}/{right}: {sorted(overlap)}")


def wilson_interval(successes: int, attempts: int, confidence: float = .95) -> tuple[float, float] | None:
    if attempts < 0 or successes < 0 or successes > attempts:
        raise ValueError("Invalid Bernoulli counts")
    if not 0 < confidence < 1:
        raise ValueError("confidence must be between zero and one")
    if attempts == 0:
        return None
    z = NormalDist().inv_cdf((1 + confidence) / 2)
    p = successes / attempts
    denominator = 1 + z * z / attempts
    center = (p + z * z / (2 * attempts)) / denominator
    margin = z * math.sqrt(p * (1 - p) / attempts + z * z / (4 * attempts * attempts)) / denominator
    return max(0., center - margin), min(1., center + margin)


def summarize_trials(protocol: dict[str, Any], trials: Iterable[dict[str, Any]]) -> dict[str, Any]:
    verify_protocol(protocol)
    grouped: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    seen: set[str] = set()
    exclusions: list[dict[str, str]] = []
    run_ids: set[str] = set()
    for trial in trials:
        required = {"trial_id", "run_id", "task_id", "condition", "protocol_sha256", "outcome",
                    "validity", "source_domain", "checkpoint_sha256", "evidence_artifact_ids"}
        if required - trial.keys():
            raise ValueError(f"Trial missing fields: {sorted(required - trial.keys())}")
        if trial["trial_id"] in seen:
            raise ValueError("Duplicate trial_id; retries are separate attempts")
        seen.add(trial["trial_id"])
        if trial["protocol_sha256"] != protocol["sha256"]:
            raise ValueError("Trial refers to a different protocol revision")
        if trial["task_id"] not in protocol["task_ids"] or trial["condition"] not in protocol["conditions"]:
            raise ValueError("Trial is outside frozen task/condition scope")
        if trial["outcome"] not in OUTCOMES:
            raise ValueError("Outcome must be SUCCESS, FAILURE or UNKNOWN")
        if trial["source_domain"] not in {"SIMULATION", "PHYSICAL", "OFFLINE_REPLAY"}:
            raise ValueError("Simulation and physical outcomes need distinct source domains")
        if not trial["run_id"] or not re.fullmatch(r"[0-9a-fA-F]{64}", trial["checkpoint_sha256"]):
            raise ValueError("Run and checkpoint provenance are mandatory")
        if trial["outcome"] == "SUCCESS" and not trial["evidence_artifact_ids"]:
            raise ValueError("A claimed success needs outcome evidence")
        run_ids.add(trial["run_id"])
        if trial["validity"] == "INVALID":
            reason = trial.get("exclusion_reason")
            if reason not in protocol["exclusion_reasons"]:
                raise ValueError("Exclusion was not preregistered")
            exclusions.append({"trial_id": trial["trial_id"], "reason": reason})
        elif trial["validity"] == "VALID":
            grouped[(trial["task_id"], trial["condition"], trial["source_domain"])].append(trial)
        else:
            raise ValueError("Trial validity must be VALID or INVALID")
    groups = []
    for (task, condition, domain), rows in sorted(grouped.items()):
        counts = {outcome: sum(row["outcome"] == outcome for row in rows) for outcome in sorted(OUTCOMES)}
        n = len(rows)
        known = counts["SUCCESS"] + counts["FAILURE"]
        groups.append(dict(task_id=task, condition=condition, source_domain=domain,
            attempts=n, counts=counts, confirmed_success_rate=counts["SUCCESS"] / n,
            success_rate_identification_bounds=[counts["SUCCESS"] / n, (counts["SUCCESS"] + counts["UNKNOWN"]) / n],
            conditional_on_known_success_rate=counts["SUCCESS"] / known if known else None,
            known_outcome_wilson95=wilson_interval(counts["SUCCESS"], known),
            safety_stops=sum(bool(row.get("safety_stop")) for row in rows),
            interventions=sum(bool(row.get("intervention")) for row in rows),
            trial_ids=[row["trial_id"] for row in rows]))
    return dict(schema_version="1.0", protocol_id=protocol["protocol_id"], protocol_sha256=protocol["sha256"],
                total_recorded_attempts=len(seen), excluded=exclusions, run_ids=sorted(run_ids), groups=groups,
                inference_note="Episodes are experimental units; frames are not independent trials. Unknown outcomes remain explicit.")


def retention_metrics(matrix: list[dict[str, float | None]], task_order: list[str]) -> dict[str, Any]:
    """Return observed forgetting/BWT; never impute a missing evaluation as zero."""
    if not matrix or len(set(task_order)) != len(task_order):
        raise ValueError("Nonempty matrix and unique task order required")
    if len(matrix) > len(task_order):
        raise ValueError("At most one row per completed acquisition stage")
    for row in matrix:
        for task, value in row.items():
            if task not in task_order or value is not None and not 0 <= value <= 1:
                raise ValueError("Retention contains an unknown task or invalid success rate")
    latest = matrix[-1]
    forgetting, backward_transfer = {}, {}
    for acquired_at, task in enumerate(task_order[:len(matrix) - 1]):
        current = latest.get(task)
        historical = [row[task] for row in matrix[acquired_at:-1] if row.get(task) is not None]
        initial = matrix[acquired_at].get(task)
        forgetting[task] = max(historical) - current if historical and current is not None else None
        backward_transfer[task] = current - initial if current is not None and initial is not None else None
    observed_f = [value for value in forgetting.values() if value is not None]
    observed_b = [value for value in backward_transfer.values() if value is not None]
    return dict(forgetting_by_task=forgetting, backward_transfer_by_task=backward_transfer,
                mean_observed_forgetting=mean(observed_f) if observed_f else None,
                mean_observed_backward_transfer=mean(observed_b) if observed_b else None,
                missing_tasks=[task for task, value in forgetting.items() if value is None])


def compose_skills(cards: list[dict[str, Any]], initial_facts: set[str], goal_facts: set[str],
                   robot_id: str, capabilities: set[str], domain: str, max_depth: int = 8) -> list[str] | None:
    """Plan symbolic effects; this returns IDs and has no motor-dispatch capability."""
    if domain not in {"SIMULATION", "PHYSICAL"} or max_depth < 0:
        raise ValueError("Explicit supported domain and nonnegative depth required")
    queue = deque([(frozenset(initial_facts), [])])
    visited = {frozenset(initial_facts)}
    while queue:
        facts, plan = queue.popleft()
        if goal_facts.issubset(facts):
            return plan
        if len(plan) < max_depth:
            for card in sorted(cards, key=lambda item: item["skill_id"]):
                compatible = robot_id in card["compatible_robot_ids"]
                admitted = set(card.get("required_capabilities", [])).issubset(capabilities)
                measured = domain in card.get("validated_domains", [])
                if compatible and admitted and measured and set(card["preconditions"]).issubset(facts):
                    next_facts = frozenset((set(facts) - set(card.get("delete_effects", []))) | set(card["postconditions"]))
                    if next_facts not in visited:
                        visited.add(next_facts)
                        queue.append((next_facts, plan + [card["skill_id"]]))
    return None


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate real run/trial evidence against a frozen protocol")
    parser.add_argument("protocol", type=Path)
    parser.add_argument("trials", type=Path, help="JSONL trial records")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    rows = [json.loads(line) for line in args.trials.read_text().splitlines() if line.strip()]
    report = summarize_trials(json.loads(args.protocol.read_text()), rows)
    rendered = json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    if args.output:
        args.output.write_text(rendered)
    else:
        print(rendered, end="")


if __name__ == "__main__":
    main()
