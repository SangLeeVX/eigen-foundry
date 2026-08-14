# Eigen Foundry delivery phases

**Plan version:** `2026-08-14.1`  
**Classification:** `NONCANONICAL ENGINEERING PLAN`

These are software-delivery phases, not therapeutic Program stages. Formal Foundry stages remain human-approved `F0`–`F12` records.

| Phase | Status | Objective | Exit checkpoint |
|---|---|---|---|
| P0 — Secure baseline | IN_PROGRESS | Import the recovered kernel, establish private GitHub controls, reproducible CI, and rotated secrets | Clean checkout passes; CI/security evidence and repository controls are recorded; exposed keys are rotated |
| P1 — Repair kernel | PENDING | Correct unknown/null semantics, governed policy migration, F0 route locking, and attributable immutable dissent | Regression tests prove all four defects are closed without weakening prior controls |
| P2 — Forge Lite | PENDING | Run bounded GitHub-native work items with isolated branches, deterministic validation, independent review, retry, crash recovery, and rollback | One `READY` item reaches a reviewed pull request through the loop with complete evidence |
| P3 — Foundry Ledger/API | PENDING | Add Postgres persistence, immutable versions/events, OIDC/MFA-aware authorization, approvals, tasks, outbox, and operator views | Only the restricted commit service can change formal state; restart/two-writer tests pass |
| P4 — Conclave v1 | PENDING | Add a provider-neutral model gateway, independent seats, Red Team, Arbiter, deterministic Axiom, and human approval queue | A synthetic F0 Crucible is reproducible, challenged, policy-evaluated, and human-gated |
| P5 — Eigen integration | PENDING | Add read-only EigenField Evidence Steward and versioned Eigen prediction/evidence adapters | An F0–F2 dry run binds authorized, immutable evidence without converting predictions into observations |
| P6 — Closed Foundry loop | PENDING | Add decisive work orders, result/QC ingestion, failure attribution, learn-back, successor councils, and monitor triggers | One synthetic diligence action and experiment traverse the loop without automatic advancement |
| P7 — Production expansion | PENDING | Add route councils and rights, IP/FTO, CMC, regulatory, commercial, portfolio, backup/restore, and soak controls | Acceptance suite passes; existing-asset/rescue and de novo dry runs are third-party reproducible |

## Phase policy

- Work may proceed in parallel only when dependencies and allowed paths do not overlap.
- A later workstream can be prototyped, but its phase cannot be marked complete before prerequisite gates pass.
- Phase state is machine-readable in `forge/state/checkpoints.json`; this file explains intent only.
- Schedule and staffing estimates are planning assumptions until owners and system access are confirmed.

## Current critical path

1. Establish P0 source and CI on the canonical private repository.
2. Rotate the previously exposed DeepSeek and EigenField credentials; do not reuse them.
3. Complete P1 integrity repairs.
4. Prove the P2 Forge loop on one bounded work item.
5. Build P3/P4 in parallel behind stable command and event contracts.

The human-only credential rotation and repository-policy settings may block P0 completion. All independent build work should continue while those checkpoints remain open.
