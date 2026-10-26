# Cross-source/lifecycle review

Reviewed source snapshot: `d8b9561`, with scheduler SHA recorded in
`GPU_PIN_MISMATCH_SOFTWARE_PROOF.json`. This review starts from a separate
worktree and performs only isolated software tests/file reads. No production
process, service, active training job, device or controller setting is changed.
Full Explorer acceptance remains IN_PROGRESS.

## Findings and fixes

1. **P1 — GPU qualification can refer to a different implementation from the
   pinned worker source.** `supervise` selects `executable_for(job)` before
   resolving the job release. That selector reads the current scheduler's
   `manipulation_student.py`, while `execution_environment` may point the worker
   to a previous release. The isolated proof admitted a fixture B interpreter
   using B's receipt, then selected unqualified A in PYTHONPATH. No GPU process
   was executed. A deployment can also reject a still-qualified old source.

   New `explorer.qualification_bindings` resolves the persisted source first,
   archives content-addressed actual receipts/results, and matches the recorded
   pipeline source hashes, state/action dimensions and runtime identity to that
   exact execution release. A qualified old binding is retained after current
   deployment/qualification changes. The root scheduler still needs the explicit
   wiring below; the helper does not modify services or create qualification.

2. **P2 — cancellation did not forbid subsequent operator challenges for the
   same attempt.** `cancel_run` previously visited only rows that already existed.
   A new wait created just after cancellation could still be confirmed.
   `OperatorEvidenceInbox` now persists a run/attempt-scoped revocation under
   the same file lock, checks it at challenge creation, confirmation and consume,
   and prevents later waits from recreating authority in the cancelled attempt.
   New attempts still use new session IDs.

3. **P2 — a confirmed question could be consumed after its deadline.** The
   consume branch preceded the deadline check. A worker paused while the API
   confirmed could return that evidence after the phase's requested timeout.
   Deadline is now checked before consume, while retaining the actual recorded
   confirmation time and actor. The coordinator's independent TTL/cancellation
   checks already provided another layer; no physical exploit is claimed.

4. **P2 — future camera timestamps were accepted as fresh.** `CameraSource.latest`
   checked only age <= stale_s. It now requires `0 <= age <= stale_s`, preserving
   the same existing stale threshold. This is a causal/schema correction, not a
   controller or motor-safety setting. Tests never open a real camera.

The existing Nano pending-STOP counter was inspected: it excludes start while
any stop request is outstanding; concurrent failures remain uncertain until an
explicit subsequent successful STOP wave. No evidence of a stale motion retry
was found in those covered paths. Current M3 handover remains blocked without
native dispatch evidence and a native release-permit boundary; accepted API,
busy=false, command echo and operator outcome do not qualify hardware success.

## Exact GPU integration contract for root

The new helper is `00-control-center/src/explorer/qualification_bindings.py`.
It uses only the standard library and never imports torch or starts a GPU job.

Dispatch order:

```python
release = resolve_job_release(config, store, job)
environment = execution_environment(config, store, job)  # idempotent same pin
executable = executable_for(job, release)                # explicit resolved source
```

In the root-owned `executable_for`, only the CUDA branch calls:

```python
selection = qualified_gpu_binding(
    job, release,
    Path.home()/'.local/share/explorer/gpu-venv/bin/python',
    Path.home()/'.local/share/explorer/state/qualification/immutable-source-bindings',
    required_binding_sha256=trusted_stored_binding_or_none,
)
return selection['executable']
```

The selected binding SHA/path and source commit should be pinned/stored by the
server alongside job execution provenance and retained in the final result.
Client configuration must not be allowed to assign those internal provenance
fields. A resumed job must validate the stored binding again; it must not fall
back to a mutable latest receipt or current checkout.

Binding publication is explicit **after actual qualification**, outside the
scheduler:

```python
publish_gpu_binding(
    actual_qualification_json,
    immutable_staged_release,           # <releases>/<40-char SHA>/RELEASE_COMMIT
    'manipulation_student',
    gpu_python,
    immutable_binding_registry,
)
```

The release can be staged with git archive without switching `current` or
restarting any service. Run the genuine GPU integration against that staged
source with a permanent pytest basetemp. The resulting actual receipt must have
`passed=true`, nonzero actual optimizer_steps, weights_changed,
state_dimension/action_dimension, and either:

- the existing nine-axis format: SHA-bound `result_path/result_sha256`, saved
  official processors actually executed, and the real result's
  `provenance.context.source_sha256`; or
- a newly measured explicit `qualified_job_kinds` list plus
  `pipeline_source_sha256` captured from the tested pipeline.

The original nine-axis result already records ten actual implementation/config
hashes. Those can honestly bind matching old releases. Changed leaf files must
be qualified again. A synthetic six-axis backend-only passed flag cannot qualify
an arbitrary reach/manipulation/recorded-training graph; a six-axis recorded
training receipt requires explicit graph and dimensions. CPU dispatch remains
unchanged.

The runtime fingerprint covers interpreter, pyvenv configuration and installed
distribution records, compared with the versions recorded by the actual result.
It explicitly does not claim a full OS/driver/ELF attestation; actual CUDA/device
checks in the worker remain necessary. This helper creates no hardware, task
success or novelty claim.

## Meaningful regression evidence

Focused tests cover old-qualified/new-deployed source preservation, rejection of
current-source evidence for unqualified old code, shared implementation and
runtime-record changes, immutable receipt corruption, archived result retention,
missing original release, backend-only rejection, cancellation before a later
wait, cancellation after API confirmation but before worker consume, confirmed
question expiry during a scheduling pause, and future/stale camera timestamps.
The software-only mismatch proof records actual observed old selector behaviour.
