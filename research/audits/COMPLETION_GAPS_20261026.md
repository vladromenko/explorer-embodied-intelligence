# Bounded completion audit against the master request

Continuation note: this historical snapshot is superseded for implementation
status by `_registry/acceptance.json`, `STATUS.md` and the retained runtime jobs
under `research/experiments/runtime-integration-20261026/`. Direct Pi/Nano
transactions, checkpoint-driven ACT transfer, object manipulation/visual ACT,
adaptive demonstration review, controlled fault probes and handover adapters
now have executable software paths. Their presence does not close physical
registration, learned object quality, complete mobile kitchen, broad device
recovery or scientific comparison. The original audit below is retained with
its original scope and date rather than silently relabelled as a new review.

Snapshot: integrated revision `318c958` plus current canonical source inspected
read-only, 26 October 2026. Criteria: the supplied master prompt, especially
§§5–13 and §17. No new device operations or tests are performed by this audit.
The current absolute user prohibition on chassis motion remains controlling.

The working engineering platform and its measured pilots do **not yet satisfy
the master prompt's full SOFTWARE_INTEGRATED acceptance**. The material gaps
below distinguish missing runtime work from missing experiments/physical evidence.

## Software functionality still missing from the integrated path

1. **Learned skill memory/adaptation/composition end to end (§5, §17).**
   `store.py:add_skill/skills` stores and retrieves cards; research helpers can
   evaluate retention and propose symbolic composition. There is no demonstrated
   runtime route that retrieves an acquired effect-level skill, adapts it,
   executes a new composition, independently accepts its result and rechecks
   retained tasks. Registry/lineage fields alone do not complete this criterion.

2. **Full 01 tasks and experiment generation (§6).** `pilot.py:ReachEnv` is a
   fixed-base, three-active-joint reach fixture. Grasp/lift/transport/release,
   deformable sock, complete candy goal, multiworker curriculum, DAgger/recovery,
   controlled fault injection, competing diagnostics and selective-repair
   experiments are not the executed integrated tasks. Initial PPO→official ACT
   reach and one-step world-model baselines are real but narrower.

3. **03 direct cross-robot teaching path (§8).** `ClutchRetargeter` produces
   geometric proposals. The Pi station's live leader→follower loop controls the
   matching SO-101 follower. A Pi→Nano ROSMASTER retarget/execution/recording path
   with the required clutch, separate gripper mapping and correlated command
   evidence is not demonstrated by these modules; `03/module.toml` remains
   `physical_io=false`. This is runtime integration work before physical trials.

4. **04 checkpoint-driven transfer (§9).** `transfer.py` inspects source metadata
   and explicitly returns `processor_runtime_probe_required=true` and
   `source_policy_executed=false`. The virtual job transfers a declared joint
   reference. Matching official LeRobot processor/policy execution followed by
   source FK, object-relative adaptation and an independently evaluated target
   task is not yet the implemented transfer run. Position IK does not supply
   orientation/contact/collision validation.

5. **05 coordinated execution (§10).** The tested handover FSM and virtual
   evidence oracles exist. A runtime coordinator that drives both robot paths,
   correlates an actual object's receiver-hold evidence and releases the giver
   through the existing controllers is not present in the virtual protocol job.
   This software route and its fixture tests can be developed without claiming
   that a physical handover has passed.

6. **Interrupted-training continuation and full resource admission (§13).**
   Scheduler process pause/resume and durable requeue exist. Current learner
   functions create optimizers anew for an interrupted attempt; saved final
   weights/optimizer reload is not update-level checkpoint/RNG/data-position
   continuation after a reboot. Scheduler serialization plus an available-RAM
   threshold does not yet implement the complete disk/thermal/power/CPU-GPU
   reserve policy requested for long campaigns.

7. **Outcome-driven research/recording loop (§4–5, §13).** Recording, job controls,
   raw outcomes and offline metrics exist. The integrated task-specific coverage
   wizard, adaptive demonstration recommendations, automatic baseline/ablation
   campaigns, regression-qualified incumbent promotion and cause-directed repair
   are not established by the present job dispatcher. Task/contact verification
   is not supplied by generic Hailo detections or a low one-step image loss.

These are not all consequences of a missing physical operator: much of the
listed orchestration, checkpoint handling and simulation work remains software
work. At report time the integrator has separately assigned DAgger and true ACT restart continuation for implementation; their source integration and real tests must be checked before closing those gaps. The new world-model module is an executed engineering baseline; its lack
of gain on the inspected metrics does not resolve any of H1–H5.

## Evidence/scene/physical work that remains separate

- **Meaningful independent task trials:** the technical current-pose hold and
  stationary-source ACT fit are not an acquired object skill. Task objects,
  demonstrations/corrections, held-out scenes/sessions and independently scored
  full executions are still required for task-quality claims.
- **ROSMASTER observation quality:** CPU transport recovered, but native RGB is
  reported nearly black. Usable wrist-view learning/validation remains limited.
  Pi overview is a separate source; its suitability must be checked for the
  intended SO-101 or shared handover scene. Partial valid depth does not prove
  object pose, spatial registration, grasp ownership or contact force.
- **Geometry/time/feedback:** SO-101 calibrations and CAD/FK are preserved;
  cross-embodiment registration, collision/contact behavior and exposure/clock
  uncertainty need measurements. A command estimate remains a command estimate.
- **Supervised motion acceptance:** the current chassis prohibition prevents
  mobile-task physical qualification. Arm-only work needs the actual scene and
  the operator conditions already established by the integrator; this audit
  adds no motion authorization. No new learned physical-task success is inferred.
- **Comparative science:** fixed protocols, matched strong baselines, ablations,
  multiple independent trainings/trials, retention/composition and identifiable
  failure/repair effects have not been completed. H1–H5 remain UNTESTED. The
  current anonymized paper is an engineering/design draft, not submission-ready.
- **Long-duration platform acceptance:** original-controller arbitration,
  end-to-end stop/Wi-Fi-load behavior, reboot/resume and resource-fault evidence
  must be assembled from the integrator's actual tests. This bounded source audit
  neither invents those results nor overrides existing backup/restore receipts.

## Earned work and current camera correction

- Literature acceptance is earned: 102 primary fulltext reviews, 35 deep reviews,
  source/page/hash audit and method provenance. `REVIEW_STATUS.json` is complete;
  the older root STATUS.md wording saying the review is unfinished is stale.
- Real PPO/ACT optimizer, checkpoint/reload, disjoint development rollouts,
  official v3 readback, physical-source stationary fitting and live shadow
  execution have archived evidence. Corrected reach ACT is 1/6, teacher 6/6,
  no motion 0/6; historical 2/6 remains archived. None is sock/handover success.
- `nano-camera-handoff.json` records the old user perception unit already failed
  with PID 0, exact matching original/backup SHA, then a verified `/dev/null` mask.
  `nano-cpu-cache-live.json` records publishing, a JPEG age of about 0.040 s,
  852 raw frames/1714 registered pairs, GPU false and controller writes 0.
  `controller-settings-recheck.json` records all six controller JSONs unchanged.
  Existing camera/control/MCU PIDs are reported preserved by the integrator.
- Native ROS RGB mean about 0.24/255, maximum 11 and depth about 74% valid are
  **integrator-reported quality-probe context**, absent from those JSON receipts.
  They are not independently extracted image statistics in this audit.
  Nano ACR/GPU bootstrap failure persists. Fresh CPU cache is a narrower success
  than usable visual perception, GPU repair or learned physical-task validation.

The three camera/controller receipts and their exact SHA copies are under
`research/experiments/nano-cpu-cache-20261026/`. The root STATUS.md should also
explicitly retain the latest chassis prohibition; its earlier broad motion
wording is insufficient for current operator guidance. The master-requested
root `START_HERE_RU.md` was not present in the inspected snapshot.
