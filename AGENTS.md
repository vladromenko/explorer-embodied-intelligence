# SO-101 independent laboratory
Work only in this repository. Other Projects and their environments are read-only.
Never open hardware during development or tests. Hardware start is only an explicit UI action after resource checks.
Never terminate foreign processes or modify cameras/torque/calibration during inspection.
All runtime imports must resolve to this repository, its private .venv, or standard/system libraries; no foreign worktrees/environments.
Primary language for UI/docs/status: Russian. Persist milestones in STATUS.md and runtime/progress.json.
Keep credentials, raw datasets, checkpoints, .venv, caches and runtime logs out of git.
