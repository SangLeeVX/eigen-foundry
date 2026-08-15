# Production implementation plan

The repository is the governance kernel. Production requires five controlled increments.

## Increment 1 — Ledger service and identity

**Target:** 1–2 weeks with one backend engineer.

- Move SQLite tables to Postgres migrations.
- Add OIDC, MFA-aware human approval, program-scoped RBAC/ABAC, and service identities.
- Wrap `CouncilService` in the command API.
- Add immutable object storage, transactional outbox, retries, tracing, and alerts.
- Add approval expiry, session expiry, hold expiry, and stale-input workers.
- Port the current tests and add crash, restore, and two-writer database tests.

**Exit:** exactly one server-controlled path changes formal Program stage; no agent can reach it.

## Increment 2 — Deterministic agent runtime

**Target:** 1–2 weeks.

- Add a versioned prompt/model registry and run manifests.
- Build seat-specific structured input envelopes and output validators.
- Enforce per-phase tool allowlists and output size/time limits.
- Quarantine retrieved content and preserve provenance, hashes, and extraction versions.
- Run deterministic mock agents and replay tests before connecting live models.

**Exit:** an F0 session can be regenerated from frozen inputs with every run attributable.

## Increment 3 — EigenField evidence adapter

**Target:** 1–2 weeks, dependent on approved read contracts.

- Implement `search_evidence` and `get_evidence_snapshot` adapters.
- Normalize atomic claim locators, context, uncertainty, contradictions, and dependency clusters.
- Separate model predictions from observed and validated evidence.
- Block new evidence from entering frozen sessions.
- Add stale-evidence events when EigenField versions change.

**Exit:** CRC or PRAD can produce an F0–F2 packet without reconstructing chat history.

## Increment 4 — Decisive work and learn-back

**Target:** 2 weeks plus ELN/LIMS integration availability.

- Add work-order, protocol, prediction, QC, result, and failure-attribution records.
- Add spend approval and data/result-rights gates.
- Preserve positive, negative, null, contradictory, and failed-QC results.
- Require data-quality approval before evidence promotion or model-training eligibility.
- Reopen a successor council session after accepted learn-back.

**Exit:** one decisive diligence action and one experiment complete the governed loop without auto-advancement.

## Increment 5 — Route councils and portfolio loop

**Target:** 2–3 weeks.

- Add independent advocates for existing asset, repositioning, rescue, combination, known-target/new-candidate, and de novo routes.
- Compare routes against one frozen TPP and future standard of care.
- Add rights, IP/FTO, CMC, regulatory, commercial, BD, and capital adapters.
- Add work-in-process, correlated-risk, reserves, and catalyst views.
- Start the Opportunity Radar only after these loops are stable.

**Exit:** one existing-asset/rescue route and one de novo route complete intake-to-decision dry runs.

## Release gates

P0 production acceptance requires:

- authorization, self-review, prompt-injection, secret-handling, state-conflict, idempotency, crash, rollback, and restore tests;
- immutable frozen snapshots, approvals, decisions, and audit events;
- version-bound human approval and atomic commit;
- complete CRC and PRAD current-gate regeneration;
- one third-party-readable Program package;
- no undefined functional approval policy in any enabled gate.

## Recommended team

- 1 backend/control-plane engineer
- 1 agent/runtime engineer
- 0.5 data/evidence engineer
- Foundry scientific/product/control/execution/investment owners for policy acceptance
- Security and legal review before external data, transactions, or regulated workflows

The kernel can be productionized in roughly 6–9 focused weeks with timely system access and decision-owner availability. Real program coverage expands after that; council membership is not the bottleneck. Governance usually is.

