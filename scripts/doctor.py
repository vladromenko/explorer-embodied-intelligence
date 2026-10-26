#!/usr/bin/env python3
"""ENGINEERING_GLUE: read-only facts from services, SQLite and existing cached node status.

No serial/camera opening, GPU execution, controller imports, service changes or SSH.
Diagnostic age/storage thresholds only affect this report, never device settings.
"""
from __future__ import annotations

import argparse
from contextlib import closing
from datetime import datetime
import json
import math
from pathlib import Path
import re
import shutil
import sqlite3
import stat
import time
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from environment_inventory import digest, package_facts, run, safe_atom
from maintenance_common import MaintenanceError, database_counts, open_readonly_db, read_json

UNITS = {"homebrain": ("explorer-control-center.service", "explorer-worker.service"), "pi": ("explorer-observer.service",)}


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def age_seconds(value, now: float) -> float | None:
    try:
        timestamp = float(value) if isinstance(value, (int, float)) else datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
        age = now - timestamp
        return round(age, 3) if math.isfinite(age) else None
    except (AttributeError, TypeError, ValueError, OverflowError):
        return None


def inventory_facts(inventory: dict, expected_calibration: dict[str, str]) -> dict:
    cameras = []
    now_ns = inventory.get("monotonic_ns")
    for camera in inventory.get("cameras", [])[:16]:
        frame = camera.get("frame") or {}
        stamp = frame.get("monotonic_ns")
        age_ms = (now_ns - stamp) / 1e6 if isinstance(now_ns, int) and isinstance(stamp, int) else None
        fresh = bool(camera.get("available") and frame.get("frame_id") and age_ms is not None and 0 <= age_ms <= float(camera.get("stale_after_s", 2)) * 1000)
        cameras.append({"id": safe_atom(camera.get("id")), "fresh": fresh, "frame_age_ms": age_ms,
                        "frame_id": safe_atom(frame.get("frame_id")), "timestamp_basis": safe_atom(frame.get("timestamp_basis"))})
    station = inventory.get("station") or {}
    measured = station.get("measurements") or {}
    vectors = [measured.get(role) for role in ("leader", "follower")]
    finite = all(isinstance(vector, list) and len(vector) == 6 and all(isinstance(value, (int, float)) and math.isfinite(value) for value in vector) for vector in vectors)
    age = station.get("measurement_age_ms")
    clock_ok = bool(measured.get("clock_domain") and measured.get("clock_domain") == inventory.get("clock_domain"))
    fresh_measured = bool(station.get("available") and finite and clock_ok and isinstance(age, (int, float)) and 0 <= age <= 1000)
    role_hashes = {role: profile.get("calibration_sha256") for role, profile in station.get("roles", {}).items() if role in {"leader", "follower"}}
    verified_calibration = bool(len(role_hashes) == 2 and role_hashes == expected_calibration and len(expected_calibration) == 2
                                and all(re.fullmatch(r"[0-9a-f]{64}", value or "") for value in expected_calibration.values()))
    perception = inventory.get("perception") or {}
    return {"reachable": True, "cameras": cameras,
            "station": {"available": bool(station.get("available")), "fresh_measured_six_axis_feedback": fresh_measured,
                        "finite_two_arm_vectors": finite, "same_boot_clock_domain": clock_ok,
                        "measurement_age_ms": age, "both_original_calibration_hashes_match": verified_calibration,
                        "configuration_writes_reported": station.get("configuration_writes"),
                        "scope": "Existing cached position feedback only; no new bus reads or motion test."},
            "hailo": {"available_reported": bool(perception.get("available")), "state": safe_atom(perception.get("state"))}}


def fetch_inventory(node: dict) -> dict:
    host, port = node.get("host", ""), node.get("port")
    if not isinstance(host, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,253}", host) or not isinstance(port, int) or not 1 <= port <= 65535:
        raise MaintenanceError("Node address is outside the fixed HTTP host/port form")
    token = node.get("token")
    if not isinstance(token, str) or len(token) < 16:
        raise MaintenanceError("Node authentication is not configured")
    request = Request(f"http://{host}:{port}/inventory", headers={"Authorization": "Bearer " + token}, method="GET")
    # Do not send a local bearer token to an environment proxy or follow a redirect.
    with build_opener(ProxyHandler({}), NoRedirect()).open(request, timeout=4) as response:
        raw = response.read(2 * 1024 * 1024 + 1)
    if len(raw) > 2 * 1024 * 1024:
        raise MaintenanceError("Node inventory exceeds its response size bound")
    result = json.loads(raw)
    if not isinstance(result, dict):
        raise MaintenanceError("Node inventory is not an object")
    return result


def outbox_facts(path: Path, now: float) -> dict:
    if not path.is_file():
        return {"available": False, "pending": None, "oldest_age_s": None}
    with closing(open_readonly_db(path)) as database:
        count = database.execute("SELECT count(*) FROM outbox WHERE delivered=0").fetchone()[0]
        rows = database.execute("SELECT payload FROM outbox WHERE delivered=0 ORDER BY rowid LIMIT 1000").fetchall()
    ages = [age_seconds(json.loads(row[0]).get("wall_utc"), now) for row in rows]
    known = [value for value in ages if value is not None]
    return {"available": True, "pending": count, "oldest_age_s": max(known) if known else None,
            "lag_sample_limit": 1000, "lag_covers_all_pending": count <= 1000}


def qualification_facts(state: Path) -> list[dict]:
    results = []
    base = state / "qualification"
    paths = sorted(base.glob("*/qualification.json"))[:50] if base.is_dir() else []
    for path in paths:
        if not path.is_symlink():
            try:
                value = read_json(path)
                versions = value.get("framework_versions", {})
                compatible = all(package_facts(name).get("version") == version for name, version in versions.items() if name in {"torch", "torchvision"}) and all(name in versions for name in ("torch", "torchvision"))
                results.append({"evidence": path.relative_to(state).as_posix(), "sha256": digest(path),
                                "passed_recorded": value.get("passed") is True, "finite_recorded": value.get("finite") is True,
                                "weights_changed_recorded": value.get("weights_changed") is True,
                                "framework_versions": {name: safe_atom(versions.get(name)) for name in ("torch", "torchvision")},
                                "matches_installed_versions": compatible, "same_build_identity_proven": False,
                                "scope": "Recorded compact ACT tensor/optimizer/checkpoint test; no physical skill qualification. Versions alone do not prove the same wheel/build."})
            except (OSError, ValueError, MaintenanceError):
                results.append({"evidence": path.relative_to(state).as_posix(), "readable": False})
    return results


def doctor(home: Path, root: Path, node: str, *, no_http: bool = False, controller_dir: Path | None = None) -> dict:
    config_dir, state = home / ".config/explorer", home / ".local/share/explorer/state"
    config = read_json(config_dir / "config.json") if (config_dir / "config.json").is_file() else {}
    state = Path(config.get("data_dir", str(state))).expanduser()
    result = {"schema": "explorer_doctor_v1", "node": node, "read_only": True, "collected_at_unix": time.time(), "services": {}, "nodes": {}}
    for unit in UNITS[node]:
        raw = run(["systemctl", "--user", "show", unit, "--property=ActiveState,SubState,UnitFileState,ExecMainStatus"])
        facts = dict(line.split("=", 1) for line in (raw or "").splitlines() if "=" in line)
        result["services"][unit] = {key: safe_atom(facts.get(key)) for key in ("ActiveState", "SubState", "UnitFileState", "ExecMainStatus")}
    result["config_permissions"] = []
    for path in [config_dir, *(config_dir / name for name in ("config.json", "node.env", "arms.json", "access-code.txt"))]:
        if path.exists():
            mode = stat.S_IMODE(path.stat().st_mode)
            result["config_permissions"].append({"name": path.name, "private": not bool(mode & 0o077) and not path.is_symlink(), "mode": oct(mode)})
    space = shutil.disk_usage(state if state.exists() else home)
    result["storage"] = {"free_bytes": space.free, "total_bytes": space.total, "below_2_gib_diagnostic": space.free < 2 * 1024**3}
    try:
        result["database"] = database_counts(state / "research.sqlite3") if (state / "research.sqlite3").is_file() else {"available": False}
        if result["database"].get("integrity") == "ok":
            with closing(open_readonly_db(state / "research.sqlite3")) as database:
                rows = database.execute("SELECT node_id,received FROM nodes ORDER BY node_id LIMIT 32").fetchall()
            result["central_node_receipt_age_s"] = {safe_atom(name) or "invalid_node_id": age_seconds(received, result["collected_at_unix"]) for name, received in rows}
        result["outbox"] = outbox_facts(home / ".local/share/explorer/node-state/outbox.sqlite3", result["collected_at_unix"])
    except (OSError, ValueError, sqlite3.Error, MaintenanceError) as error:
        result["database_or_outbox_read"] = {"error_type": type(error).__name__}
    calibration_file = root / "_registry/so101-calibration-manifest.json"
    expected = read_json(calibration_file) if calibration_file.is_file() else {}
    hashes = {"leader": expected.get("teleoperators/so_leader/so101_leader.json", {}).get("sha256"),
              "follower": expected.get("robots/so_follower/so101_follower.json", {}).get("sha256")}
    result["local_calibration"] = [{"path": relative, "present": (home / ".cache/huggingface/lerobot/calibration" / relative).is_file(),
                                    "original_hash_matches": (home / ".cache/huggingface/lerobot/calibration" / relative).is_file() and digest(home / ".cache/huggingface/lerobot/calibration" / relative) == row["sha256"]} for relative, row in expected.items()]
    baseline = read_json(root / "_registry/controller-config-baseline.json")
    result["controller_baseline"] = {"checked": controller_dir is not None, "baseline_files": len(baseline), "writes": 0}
    if controller_dir is not None:
        result["controller_baseline"]["matches"] = {name: (controller_dir / name).is_file() and digest(controller_dir / name) == value for name, value in baseline.items()}
    configured = config.get("nodes", [])
    nodes = {row["id"]: row for row in configured if isinstance(row, dict) and row.get("id") in {"pi", "nano"}} if isinstance(configured, list) else configured
    if node == "pi" and not nodes and (config_dir / "node.env").is_file():
        environment = dict(line.split("=", 1) for line in (config_dir / "node.env").read_text().splitlines() if "=" in line)
        nodes = {"pi": {"host": "127.0.0.1", "port": 8091, "token": environment.get("EXPLORER_NODE_TOKEN")}}
    for name, address in ((key, nodes[key]) for key in ("pi", "nano") if key in nodes):
        # Preserve old Nano: this maintenance utility does not contact it. Existing UI bridge remains separate.
        if name == "pi" and not no_http:
            try:
                result["nodes"][name] = inventory_facts(fetch_inventory(address), hashes)
            except Exception as error:
                result["nodes"][name] = {"reachable": False, "error_type": type(error).__name__}
        else:
            result["nodes"][name] = {"contacted": False, "reason": "HTTP skipped or preserved Nano"}
    result["qualification"] = qualification_facts(state)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--node", required=True, choices=sorted(UNITS))
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--no-http", action="store_true")
    parser.add_argument("--controller-dir", type=Path, help="Optional explicit local folder; file hashes only, no controller imports")
    args = parser.parse_args()
    print(json.dumps(doctor(Path.home(), args.root, args.node, no_http=args.no_http, controller_dir=args.controller_dir), indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
