import importlib.util
import json
from pathlib import Path
import shutil


ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location("provenance_check", ROOT / "scripts/check_provenance.py")
provenance = importlib.util.module_from_spec(spec)
spec.loader.exec_module(provenance)


def setup_root(tmp_path):
    for relative in ["research/methods/METHOD_REGISTRY.yaml", "research/methods/CODE_PROVENANCE.csv", "research/benchmark/evaluate.py"]:
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / relative, target)
    # Isolate the checker invariant from unrelated integrated application modules.
    registry_path = tmp_path / "research/methods/METHOD_REGISTRY.yaml"
    document = json.loads(registry_path.read_text())
    document["methods"] = [entry for entry in document["methods"] if entry["method_id"] == "ENG-EVAL-001"]
    registry_path.write_text(json.dumps(document))
    csv_path = tmp_path / "research/methods/CODE_PROVENANCE.csv"
    lines = csv_path.read_text().splitlines()
    csv_path.write_text("\n".join([lines[0], *[line for line in lines[1:] if ",ENG-EVAL-001," in line]]) + "\n")
    return tmp_path


def test_new_scientific_module_cannot_silently_bypass_provenance(tmp_path):
    root = setup_root(tmp_path)
    (root / "research/benchmark/unregistered.py").write_text('"""Unregistered new algorithm."""\n')
    assert any("no provenance record" in error for error in provenance.check(root))


def test_hardware_tested_claim_requires_physical_run_ids(tmp_path):
    root = setup_root(tmp_path)
    path = root / "research/methods/METHOD_REGISTRY.yaml"
    document = json.loads(path.read_text())
    document["methods"][0]["verification"]["hardware_tested"] = True
    path.write_text(json.dumps(document))
    assert any("hardware run IDs" in error for error in provenance.check(root))


def test_preserved_legacy_exclusion_does_not_hide_explicit_audit(tmp_path):
    root = setup_root(tmp_path)
    path = root / '01-autonomous-learning/src/legacy/model.py'
    path.parent.mkdir(parents=True)
    path.write_text('"""Historical unqualified code."""\n')
    errors = provenance.check(root, ['01-autonomous-learning/src/legacy/model.py'])
    assert any('no provenance record' in error for error in errors)


def test_registered_module_cannot_use_legacy_exclusion_to_skip_header_check(tmp_path):
    root = setup_root(tmp_path)
    path = root / '01-autonomous-learning/src/legacy/model.py'
    path.parent.mkdir(parents=True)
    path.write_text('"""Historical unqualified code."""\n')
    registry_path = root / 'research/methods/METHOD_REGISTRY.yaml'
    document = json.loads(registry_path.read_text())
    record = dict(document['methods'][0], implementation_id='LEGACY-FIXTURE',
                  function_or_module='01-autonomous-learning/src/legacy/model.py')
    document['methods'].append(record)
    registry_path.write_text(json.dumps(document))
    assert any('docstring' in error for error in provenance.check(root))
