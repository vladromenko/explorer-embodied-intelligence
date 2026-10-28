---
name: explorer-project-operator
description: Resume and advance the existing Explorer robotics project from canonical state, preserving scoped evidence and the approved backlog. Use for Explorer continuation, integration, verification, and project recovery; not for unrelated repositories.
---

# Explorer project operator

Follow [AGENTS](../../../AGENTS.md) and the existing work instead of inventing a
new plan from the last chat message. Read [PROJECT_STATE](../../../docs/PROJECT_STATE.md)
and [BACKLOG](../../../docs/BACKLOG.md), then inspect Git/live receipts for the
active ID. Use [AI_OPERATIONS](../../../docs/AI_OPERATIONS.md) for recovery,
ownership, test scope, integration and the milestone/checkpoint cycle.

Select one unblocked atomic item; identify reused modules and the thin change.
Honor the physical/controller constraints in AGENTS. Complete the authorized
software work, verify its actual scope, update state/acceptance/backlog, run
`python scripts/check_project_state.py`, and checkpoint before continuing the
next useful authorized item. Infrastructure completion does not complete Explorer.

Use original master/protocols only for missing or conflicting requirements.
This repo skill does not change model settings or grant physical authority.
Automatic discovery by the current client is unverified; AGENTS and the runbook
remain the required entrypoint even when this skill is not listed automatically.

MVP-FIRST / PHYSICAL-FEEDBACK-FIRST applies to all01–06 and future tasks.
Use the mandatory policy in root AGENTS and AI_OPERATIONS: shortest deployable
user path first; targeted tests +one no-motion smoke +one sufficient milestone
regression, then deploy MVP_USER_TESTABLE / DEPLOYED_AWAITING_PHYSICAL_ACCEPTANCE.
Track MVP BLOCKERS separately from DEFERRED HARDENING; do not take speculative
hardening before basic paths of priority experiments. Physical tests remain
USER-observed; no agent motion, controller/feedback changes or new bus owners.
