# Metric identities and review evidence

Names are insufficient to establish comparable evaluations. Record the equation, checkpoint rule, denominator, domain and uncertainty unit with every metric.

- Explorer `retention_metrics` reports final-stage signed backward transfer against each task's learning-stage score, plus forgetting against the maximum measured earlier score. Missing task measurements remain missing.
- LIBERO (P130, PDF p6, equation3) defines forward transfer as an average over eleven current-task training checkpoints, negative backward transfer across subsequent stages, and a success-curve aggregate. It selects the best evaluated current-task checkpoint. These are not aliases for Explorer's final-stage BWT/forgetting.
- LOTUS (P131, PDF p3) uses completed-stage diagonal success for its forward-transfer expression. Its metric name matches LIBERO while its expression differs. An implementation must cite the exact selected protocol.
- AUC values in these papers are task/stage aggregates of success. They are not a directly observed Bernoulli success rate from one physical task.
- Mean±standard error over three training seeds is not the same as a binomial interval over all per-task trials. Shared training checkpoints, sessions and objects can create dependence.
- Explorer's Wilson interval is conditional on known outcomes. The separately reported unknown-outcome identification bounds are not a confidence interval. Preserve attempted and unknown counts.

The six evaluation seeds in `default-pilot-v1` were disjoint from training and imitation data, but belong to development. Their outcomes may guide implementation; they do not become a sealed final test after inspecting them. A new frozen evaluation with untouched sessions/seeds is required for final claims.
