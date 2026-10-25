"""ENGINEERING_GLUE job entries; importing the web app never imports MuJoCo."""
from pathlib import Path
import sys


def run_sim_pilot(store, job, config):
    root = Path(config["root"])
    module_path = str(root / "01-autonomous-learning" / "src")
    if module_path not in sys.path:
        sys.path.insert(0, module_path)
    from explorer_sim.pilot import run_pilot
    return run_pilot(store, job, root)


def run_train_act(store, job, config):
    """Train on explicitly selected synchronized episodes; never starts a pilot."""
    module_path = str(Path(config["root"]) / "01-autonomous-learning" / "src")
    if module_path not in sys.path:
        sys.path.insert(0, module_path)
    from explorer_sim.recorded_training import train_recorded_act
    return train_recorded_act(store, job)
