"""Simulation job entry point; importing the web app never imports MuJoCo."""
from pathlib import Path
import sys


def run_sim_pilot(store, job, config):
    root = Path(config["root"])
    module_path = str(root / "01-autonomous-learning" / "src")
    if module_path not in sys.path:
        sys.path.insert(0, module_path)
    from explorer_sim.pilot import run_pilot
    return run_pilot(store, job, root)
