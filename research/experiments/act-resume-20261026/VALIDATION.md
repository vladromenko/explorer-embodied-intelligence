# ACT restart: executed engineering validation

Six new restart tests pass. A larger selected learner/adapter suite passed 32 tests
with 4 existing explicit integration skips; 13 research accounting tests also pass.
No device access, deployment, physical-task or H1–H5 validation was performed.

CPU_EQUIVALENCE_PROOF.json is derived from actual saved checkpoint payloads:

| Executed case | Continuous updates | Durable point + restarted updates | Compared final values |
|---|---:|---:|---|
| Stochastic Python/NumPy/Torch Dataset + dropout toy | 12 | 4 + 8 | Model, AdamW, losses, sampler and exercised RNG: bitwise equal |
| Actual official LeRobot ACT 6D/VAE/RGB | 4 | 2 + 2 | Model, AdamW, losses, sampler and exercised RNG: bitwise equal |

Maximum model tensor difference is 0.0 in both cases. Fresh model and Dataset
objects are constructed on restart. Model initialization/RNG are deliberately
changed before the toy restart; the saved state replaces them. Current source
SHA and actual runtime versions are recorded; Torch 2.14.1/torchvision 0.29.1 on
Mac are outside LeRobot 0.6.1 declared upper bounds, an execution-specific
exception rather than general framework qualification. CUDA RNG support is
implemented but not exercised by these CPU tests.

Additional tests cover the original public DataLoader index sequence without
global-RNG consumption, duplicate optimizer-writer rejection, changed source/
settings and checkpoint corruption, and injected failure between fsynced payload
and atomic pointer replacement. The latter resumes the previous valid point
and gives the same final toy parameters/losses. Original inputs are unchanged.

PILOT_RESUME_PROOF.json records an actual CAD/MuJoCo run: 4096 completed PPO
teacher steps, 12 source episodes, ACT interrupted at durable 25, restarted to 30
with only 5 additional updates. The completed teacher ZIP and all 13 dataset files
retain their exact hashes. The completed teacher/data phase is reused; no
mid-PPO optimizer/collector/RNG/physics-trajectory continuation is claimed.

Checkpoint tensors and optimizer files remain in the local paths in the proof
records; only compact proof/receipt/JUnit files are committed. Retained update
history is not total compute across failed attempts; updates newer than the
last durable checkpoint can be replayed. All H1–H5 remain UNTESTED.

The global provenance gate at handoff has an unrelated pending registration
for root-owned mobile_capture.py; the root integrator owns its activation and
will rerun the gate after integration. No PASS is claimed for that global gate.
