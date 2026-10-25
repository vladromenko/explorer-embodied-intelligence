# Method provenance

Every scientific module in the configured source scopes needs a registry entry and a short module docstring naming its `method_id`. Run `python scripts/check_provenance.py` in CI after integration. `--paths file.py ...` checks an explicit changed scientific-module set. The integrator registers new training/retargeting modules as they enter the codebase.

`ENGINEERING_GLUE` is valid for accounting, adapters and orchestration. It requires a technical source and implementation rationale, but does not invent a paper or claim scientific novelty. Upstream code must identify its actual pinned revision and license. Code, model and dataset licenses are distinct.

Verification stages are separate booleans: reading a paper does not mean inspecting code, installing it does not mean passing a test, and simulation does not establish hardware validation. A `hardware_tested` claim requires physical run IDs. Registered modules and actual test paths are checked; semantic correctness and novelty still require review and evidence.

Preserved `01-autonomous-learning/src/legacy` files are excluded from automatic discovery only with an explicit absence of scientific qualification. A registered legacy module is still checked, and `--paths` always performs an explicit audit. Importing legacy geometry into a new experiment requires a registered wrapper, source hashes and stated limits; this exclusion does not qualify old learning algorithms.
