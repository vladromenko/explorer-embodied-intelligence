# Falsifiable research questions

These are untested hypotheses, not achieved results. ESMA is a working name for an evidence-grounded skill-memory and adaptation mechanism. Generic orchestration, symbolic planning and source integration are engineering contributions until experiments demonstrate an additional result.

| Hypothesis | Comparison | Independent outcome | Falsification / limitation |
|---|---|---|---|
| H1: compatible mixed sources improve acquisition or generalization | source-specific ACT, target-only ACT, matched-data mixed-source ACT | full-task success on held-out sessions/scenes and matched evaluation resources | no gain, or gain explained by extra target data/compute |
| H2: object/contact effects transfer better than raw joints | joint replay only in simulation, FK/IK retargeting, effect representation, target-only learning | target-world outcome and pose errors in meters/radians, workspace feasibility | unreachable poses, inferior target outcomes, unmeasured object state |
| H3: explicit memory helps reuse/composition and retention | independent specialists, sequential fine-tune, memory retrieval/adaptation, without-memory ablation | new composition completion and old-task retention matrix after each update | no advantage, missing retention evaluation, component skills fail independently |
| H4: identifiable cause-directed repair improves over indiscriminate fine-tuning | full fine-tune, random component repair, targeted repair, abstaining diagnostic method | matched-budget task success, diagnosis confusion matrix and abstention coverage | causes are observationally confounded, no held-out gain, regression on old tasks |
| H5: separately acquired skills compose into verified handover | manual-stage reference, independent skill execution, full composed system | verified receiver possession, giver release and receiver finishing the task | grasp closure/timer alone cannot verify possession; absent force evidence requires operator confirmation |

H1-H5 may fail. Results must distinguish `UNTESTED_HYPOTHESIS`, `MEASURED_NO_GAIN`, `MEASURED_GAIN_WITH_BASELINE`, `REPLICATED`. A successful software test or one physical video cannot determine scientific novelty.
