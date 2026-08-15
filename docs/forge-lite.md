# Forge Lite operating contract

**Status:** noncanonical engineering draft.

Forge Lite is the software delivery loop for Foundry and Conclave. It is deliberately separate from the therapeutic workflow: merging code cannot approve a Foundry Program gate, and a Conclave recommendation cannot merge code.

## Durable objects

| Object | Canonical location | Purpose |
|---|---|---|
| Milestone plan | `PLANS.md` | Human-readable M0–M9 sequence and dependencies |
| Milestone truth | `forge/state/checkpoints.json` | Machine-readable gate criteria, crosswalk, approval, and durable evidence |
| Work item | GitHub issue plus `forge/work-items/<id>.json` | One bounded objective, scope, tests, retry policy, and authority boundaries |
| Implementation | `agent/<id>-<slug>` branch | Isolated, replaceable worktree state |
| Review packet | Draft pull request | Diff, deterministic evidence, independent challenge, rollback plan |
| Release evidence | Protected baseline commit and Actions run | Addressable proof used by a checkpoint |

## Scheduler behavior

An hourly watcher may resume the loop, but it is not a continuously trusted process. Each run reconstructs state from GitHub, claims no more than one eligible item, and leaves a durable trail before exiting. Claim expiry provides crash recovery.

The watcher remains silent during routine work. It reports only:

- a milestone whose every criterion and dependency is verified on the protected baseline; or
- a genuine security/authority blocker that prevents any remaining independent work.

It must never declare success from a local worktree, chat message, model assertion, open draft PR, or passing test that is not bound to a commit and CI run.

## Independence

Implementation and independent review require different actor/run identities. Review checks the declared acceptance criteria, changed paths, test evidence, failure behavior, security boundary, and checkpoint delta. A human or separately protected service retains merge authority.

## Secrets

Issues, branches, logs, fixtures, and prompts carry secret names only. Previously exposed values are considered revoked. Repository-history scanning and provider rotation remain explicit M1 evidence requirements.

Legacy P0–P7 identifiers are archived crosswalk inputs. New work items use M0–M9 milestone IDs. Neither namespace changes therapeutic Program stages F0–F12.

## Protected Foundry actions

Forge may implement software for approvals and commit services, but it cannot exercise their authority. Human approval remains mandatory for Program stage changes, route changes, evidence promotion, spend, external outreach, transactions, model/policy changes, candidate nomination, regulatory submissions, and clinical use.
