#!/usr/bin/env python3
"""Reject unregistered scientific modules and unsupported verification claims.

Engineering helpers may use ENGINEERING_GLUE with technical references. The
checker does not claim to establish scientific novelty or semantic correctness.
"""
from __future__ import annotations

import argparse
import ast
import csv
import json
import re
from pathlib import Path


REQUIRED = {"method_id", "implementation_id", "function_or_module", "implementation_type", "sources",
            "upstream_repo", "upstream_commit", "code_license", "model_license", "data_license",
            "changes_and_reason", "verification", "test_evidence"}
TYPES = {"UPSTREAM_UNMODIFIED", "ADAPTED", "REIMPLEMENTED", "ORIGINAL", "ENGINEERING_GLUE"}


def load_registry(path: Path) -> dict:
    text = path.read_text()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        try:
            import yaml
        except ImportError as exc:
            raise ValueError("Non-JSON YAML registry requires PyYAML") from exc
        return yaml.safe_load(text)


def check(root: Path, explicit_paths: list[str] | None = None) -> list[str]:
    registry = load_registry(root / "research/methods/METHOD_REGISTRY.yaml")
    errors = []
    entries = registry.get("methods", [])
    ids = set()
    implementations = set()
    by_path = {}
    for entry in entries:
        missing = REQUIRED - entry.keys()
        if missing:
            errors.append(f"Method record missing fields: {sorted(missing)}")
        method = entry.get("method_id", "MISSING")
        implementation = entry.get("implementation_id", "MISSING")
        if implementation in implementations:
            errors.append(f"Duplicate implementation_id: {implementation}")
        implementations.add(implementation)
        ids.add(method)
        if entry.get("implementation_type") not in TYPES:
            errors.append(f"{method}: unrecognized implementation_type")
        if entry.get("implementation_type") in {"UPSTREAM_UNMODIFIED", "ADAPTED"}:
            if not re.fullmatch(r"[0-9a-fA-F]{40}", entry.get("upstream_commit", "")):
                errors.append(f"{method}: reused upstream code requires an exact 40-character commit SHA")
        if not entry.get("sources") or not entry.get("changes_and_reason"):
            errors.append(f"{method}: source and implementation rationale required")
        verification = entry.get("verification", {})
        if verification.get("hardware_tested") and not entry.get("hardware_run_ids"):
            errors.append(f"{method}: hardware-tested claim requires hardware run IDs")
        if verification.get("software_tested") and not entry.get("test_evidence"):
            errors.append(f"{method}: tested claim requires actual test evidence")
        filename = entry.get("function_or_module", "").split(":", 1)[0]
        if filename.endswith(".py"):
            by_path.setdefault(filename, set()).add(method)
            if not (root / filename).is_file():
                errors.append(f"{method}: registered source does not exist: {filename}")
    paths = set(explicit_paths or [])
    if explicit_paths is None:
        for pattern in registry.get("scientific_module_globs", []):
            paths.update(str(path.relative_to(root)) for path in root.glob(pattern)
                         if path.is_file() and path.suffix == ".py" and "tests" not in path.parts and path.name != "__init__.py")
        for exclusion in registry.get("preserved_legacy_exclusions", []):
            if not exclusion.get("reason") or exclusion.get("scientific_qualification_claimed") is not False:
                errors.append("A preserved legacy exclusion needs a reason and explicit absence of scientific qualification")
            else:
                paths.difference_update(str(path.relative_to(root)) for path in root.glob(exclusion["glob"])
                                        if path.is_file())
    # Registered modules always receive header checks, even outside discovery globs
    # or inside a preserved snapshot. An exclusion cannot qualify a legacy method.
    paths.update(by_path)
    for filename in sorted(paths):
        path = root / filename
        if path.is_file() and path.suffix == ".py":
            tree = ast.parse(path.read_text(), filename=filename)
            doc = ast.get_docstring(tree) or ""
            registered = by_path.get(filename, set())
            if not registered:
                errors.append(f"Scientific module has no provenance record: {filename}")
            elif not any(method in doc for method in registered):
                errors.append(f"Scientific module docstring must name registered method_id: {filename}")
    with (root / "research/methods/CODE_PROVENANCE.csv").open(newline="") as f:
        for row in csv.DictReader(f):
            if row["method_id"] not in ids or row["implementation_id"] not in implementations:
                errors.append(f"CSV references unknown method/implementation: {row}")
    return errors


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--paths", nargs="*")
    args = parser.parse_args()
    errors = check(args.root, args.paths)
    print(json.dumps(dict(status="FAIL" if errors else "PASS", errors=errors), indent=2))
    if errors:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
