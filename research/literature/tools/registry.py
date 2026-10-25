"""Honest literature accounting: retrieval is not a review or a hardware test."""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path


REVIEW_FIELDS = {"problem", "contribution", "algorithm", "observations", "actions", "embodiment_and_data",
                 "experiments", "baselines", "quantitative_result", "limitations", "code_and_license",
                 "hardware_adaptation", "decision", "evidence_locations", "reviewer", "reviewed_at"}


def validate_review(review: dict, paper: dict) -> list[str]:
    errors = []
    missing = REVIEW_FIELDS - review.keys()
    if missing:
        errors.append(f"{paper['paper_id']}: missing substantive review fields {sorted(missing)}")
    if review.get("paper_id") != paper["paper_id"]:
        errors.append(f"{paper['paper_id']}: review identity mismatch")
    if review.get("source_sha256") != paper.get("fulltext_sha256") or not paper.get("fulltext_sha256"):
        errors.append(f"{paper['paper_id']}: review must bind to retrieved full-text SHA256")
    if paper.get("fulltext_status") != "RETRIEVED" or not review.get("full_text_examined"):
        errors.append(f"{paper['paper_id']}: abstract/retrieval alone is not a full-text review")
    if review.get("decision") not in {"REUSE", "ADAPT", "COMPARE", "EXCLUDE"}:
        errors.append(f"{paper['paper_id']}: invalid reuse decision")
    if not review.get("evidence_locations"):
        errors.append(f"{paper['paper_id']}: page/section evidence required")
    if review.get("deep_review"):
        for field in ("algorithm_detail", "integration_contract", "license_evidence", "required_experiments", "difference_from_explorer"):
            if not review.get(field):
                errors.append(f"{paper['paper_id']}: deep review missing {field}")
    for field in REVIEW_FIELDS - {"quantitative_result"}:
        if field in review and not review[field]:
            errors.append(f"{paper['paper_id']}: empty review field {field}")
    return errors


def report(registry_path: Path) -> dict:
    document = json.loads(registry_path.read_text())
    papers = document["papers"]
    counted = set()
    counts = dict(candidates=len(papers), unique_candidates=0, primary_verified=0,
                  full_text_retrieved=0, full_text_read=0, deep_reviews=0, code_inspected=0, code_executed=0)
    errors = []
    for paper in papers:
        if not paper.get("duplicate_of"):
            canonical = paper["canonical_id"]
            if canonical in counted:
                errors.append(f"Unmarked duplicate canonical identity: {canonical}")
            else:
                counted.add(canonical)
                counts["unique_candidates"] += 1
                counts["primary_verified"] += paper.get("primary_status") == "PRIMARY_VERIFIED"
                counts["full_text_retrieved"] += paper.get("fulltext_status") == "RETRIEVED"
                counts["code_inspected"] += bool(paper.get("code_inspected"))
                counts["code_executed"] += bool(paper.get("code_executed"))
                if paper.get("review_status") == "FULL_TEXT_REVIEWED":
                    review_path = registry_path.parent / paper.get("review_path", "MISSING")
                    if review_path.is_file():
                        review = json.loads(review_path.read_text())
                        review_errors = validate_review(review, paper)
                        errors.extend(review_errors)
                        if not review_errors:
                            counts["full_text_read"] += 1
                            counts["deep_reviews"] += bool(review.get("deep_review"))
                    else:
                        errors.append(f"Review file missing: {review_path}")
    complete = counts["full_text_read"] >= 100 and counts["deep_reviews"] >= 25 and not errors
    return dict(schema_version="1.0", counts=counts, errors=errors,
                status="LITERATURE_REVIEW_COMPLETE" if complete else "LITERATURE_REVIEW_INCOMPLETE",
                acceptance_requirements={"full_text_read": 100, "deep_reviews": 25})


def export_csv(registry_path: Path) -> None:
    rows = json.loads(registry_path.read_text())["papers"]
    keys = sorted({key for row in rows for key in row})
    with registry_path.with_suffix(".csv").open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=keys)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else value
                             for key, value in row.items()})


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--registry", type=Path, default=Path(__file__).parents[1] / "literature_registry.json")
    parser.add_argument("--export-csv", action="store_true")
    args = parser.parse_args()
    if args.export_csv:
        export_csv(args.registry)
    result = report(args.registry)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if result["errors"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
