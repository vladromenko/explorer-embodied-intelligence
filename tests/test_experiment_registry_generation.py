"""Prevent browser/API identities from drifting after the one-time renumber."""
from pathlib import Path
import subprocess
import sys


def test_browser_and_public_json_are_exactly_generated_from_python_registry():
    root=Path(__file__).resolve().parents[1]
    subprocess.run([sys.executable,str(root/'scripts/generate_experiment_registry.py'),'--check'],cwd=root,check=True)
