# Protocol freeze and evidence rules

Freeze and hash task definitions, outcome predicates, baseline revisions, permitted exclusions, sampling, seeds, trial order, object/scene/session splits and compute budgets before the corresponding sealed evaluation. Pilot runs are development evidence, never silently relabeled as sealed tests.

The unit is a complete independent task attempt, not a camera frame. Retain every attempt, failure, intervention and safety stop. Unknown outcomes remain unknown. Report the observed success count, total started valid attempts, unknown fraction, exclusion reasons and intervals; do not condition success reporting only on favorable known outcomes. Report physical and simulation groups separately.

Success predicates use independent world outcomes with explicit evidence. End-effector distance alone does not mean a pick-and-place completed. A closed gripper is not proof of grasp. A handover is complete only when transfer and the receiver's final action are independently confirmed. If the available sensors cannot establish a condition, annotate `UNKNOWN` or require operator confirmation.

Train/development/sealed_test manifests are disjoint by source episode and collection session. For a claimed new-object/scene test, require disjoint object/scene identifiers too. Compute normalization statistics on train only. Future actions, privileged simulator truth and outcome annotations must not enter deployable observations.

Start with a small engineering pilot, then choose and freeze trial counts using uncertainty and practical power considerations. Twenty trials per condition is an example, not an automatic adequacy guarantee. Use multiple sessions/scenes/seeds where the claim requires them. Preserve trial order to inspect drift.

Transfer conditions include target-only, source-only where meaningful, source+target at explicitly counted 5/10/25 demonstrations only if enough valid data exist, FK/IK and effect/feature alternatives. Raw cross-robot joint replay is a simulation baseline only. Keep the target robot, scene and action contract matched when comparing demonstration interfaces 02/03.

For retention, evaluate previously accepted tasks after each new dataset/model update; retain the incumbent until a predefined regression gate passes. Composition evaluation needs both independent component evaluations and the complete new task. Cause-directed repair requires known injected causes in simulation and an explicit abstention option for confounded real failures.

Use `research/benchmark/evaluate.py` to validate a frozen JSON protocol and aggregate JSONL trial records. `protocol_hash()` computes its content SHA256; `assert_disjoint_splits()` prevents accidental session leakage. No example numerical trial file is shipped as a claimed experiment.

Current operational restriction (integrator context, 26 October 2026): the user prohibits ANY chassis motion. Simulation and archived-data analysis do not authorize chassis commands. The integrator reports that the exact M3Pro manual requires the unit OFF while charging; no charging/motion experiment is authorized here. The CPU Nano ROS image/cache path has now been restored; fresh transport does not establish a usable scene. The integrator reports near-black native RGB (mean about 0.24/255, maximum 11) and about 74% valid depth; these quality numbers are not contained in the archived cache receipt. Nano GPU bootstrap/ACR failure persists. Prior homebrain ACT GPU checks do not qualify the Nano GPU, wrist-image quality, exposure timing or clock alignment. No physical learned-policy task test has been completed.
