# Eigen Foundry build specification

**Build mandate:** `BUILD-EIGEN-FOUNDRY-003`  
**Classification:** `NONCANONICAL ENGINEERING DRAFT`  
**Scope:** governance kernel, Forge Lite delivery loop, and evidence-backed M0–M9 product milestones

This specification governs software delivery. It does not approve a therapeutic program, promote scientific evidence, authorize spend, or change a formal Foundry stage.

## Target outcome

Produce a private, reproducible, auditable control plane with three distinct responsibilities:

- **Forge** builds, tests, reviews, and releases software through GitHub work items and pull requests.
- **Foundry** holds versioned program, evidence-pointer, task, approval, and decision state.
- **Conclave** performs bounded, independently challenged deliberation inside Foundry; deterministic policy decides admissibility and humans approve protected decisions.

The current repository is only an F0 governance kernel and a SQLite development ledger inside `M1 — Trustworthy foundation / PARTIAL`. It is not a working Conclave or Foundry.

## Non-negotiable invariants

1. GitHub `main` is the canonical engineering baseline. Chat and local worktrees are not durable state.
2. Every change starts from one bounded Forge work item and reaches `main` through CI plus review.
3. The authoring run cannot be the sole reviewer and no agent can approve its own work.
4. Automation may draft, test, challenge, and open pull requests. It may not merge protected changes, approve program gates, spend, contact third parties, promote evidence, or make clinical or production decisions.
5. Model output is untrusted proposal data. A deterministic policy service remains the only admissibility authority; a restricted commit service remains the only stage-change path.
6. Credentials live only in an approved secret manager or GitHub encrypted secrets. Never copy secrets from chat, issues, logs, fixtures, commits, or pull-request text.
7. Exposed credentials are treated as revoked. Integration stays blocked until a human rotates and installs replacements.
8. Milestone completion requires committed evidence for every exit criterion, completed dependencies, and no applicable open blocker. Narrative claims do not satisfy a gate.
9. Unknown evidence remains `UNKNOWN`; it is never represented as a measured null or an inferred pass.
10. Failures, dissent, nulls, retries, and rollback evidence are preserved and attributable.

## Reproducible build

Supported baseline: CPython 3.12.

```bash
make bootstrap
make check
```

`requirements-ci.lock` pins the CI environment. `pyproject.toml` carries the supported runtime range. CI executes the same secret scan, contract validation, unit-test, schema-drift, and wheel-build steps used locally.

## Forge Lite control loop

```text
GitHub issue -> work-item JSON -> isolated branch -> bounded implementation
             -> deterministic validation -> independent review -> draft PR
             -> human-protected merge -> milestone evidence update
```

Each loop iteration must:

1. Read `PLANS.md`, `forge/state/checkpoints.json`, and open GitHub work items from the canonical repository.
2. Select at most one `READY` item whose dependencies are complete.
3. Create an `agent/<work-item-id>-<slug>` branch and mark the item `CLAIMED` with an opaque run identifier.
4. Change only declared paths and stop on authority, dependency, state-conflict, or secret boundaries.
5. Run every declared validation command and attach exact command, result, commit SHA, and artifact pointer.
6. Open or update a draft pull request. A different actor/run performs review.
7. Retry only idempotent operations, within the item's retry budget. Preserve the terminal failure reason.
8. Update a milestone checkpoint only after its evidence is present on the protected baseline.

The loop is resumable because durable state is in GitHub. It must not depend on a long-lived local process or remembered chat context.

## M1 trustworthy-foundation exit

M1 is complete only when all of these are verified on the protected baseline:

- recovered source is committed to the intended private repository;
- a clean checkout installs and passes tests on Python 3.12;
- CI, contract validation, package build, and secret scan pass;
- branch protection and required review/check settings are recorded;
- exposed DeepSeek and EigenField credentials are rotated and replacements exist only in an approved secret store;
- no secret exists in repository history;
- all four audited semantic defects fail closed with deterministic regressions;
- one authorized Forge item reaches independent review without another prompt;
- forced crash, lease expiry, resume, replay, protected-path refusal, kill switch, and budget controls pass; and
- the milestone record contains commit- and run-addressable evidence.

Until then, M1 remains `PARTIAL` and live integrations remain blocked.

## Failure and recovery

- `STATE_CONFLICT`: refresh canonical state and rebase; never overwrite another run.
- deterministic test failure: keep the PR draft, attach failure evidence, and use a successor work item if scope changes.
- runner crash: an expired claim returns to `READY`; a new run resumes from GitHub state.
- secret finding: stop, quarantine the change, rotate the credential if real, and require a clean-history verification.
- upstream outage or rate limit: retry only reads/idempotent writes with bounded backoff.
- protected-action requirement: record `AUTHORITY_BLOCKED`, continue independent work, and report the blocker at the next checkpoint.

## Evidence format

Milestone truth lives in `forge/state/checkpoints.json`. An exit criterion can be `VERIFIED` only when its typed evidence binds the criterion and exact immutable revision. Local output, chat, and unmerged draft claims are supporting context, never final milestone proof. Software milestones never change therapeutic Program stages `F0`–`F12`.
