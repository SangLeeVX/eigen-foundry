#!/usr/bin/env python3
"""FWI-M9-026: verify M9-C2 (backup/restore/rollback/data-integrity) against live Postgres.

M9-C2 was marked BLOCKED in FWI-M9-023 because the Postgres-backed tests
skipped without a real database. Since then the live Postgres bridge was wired:
``foundry` role + ``foundry`` database on cluster 18/main (port 5432), with
FOUNDRY_LEDGER_DSN / FOUNDRY_PG_PASSWORD supplied via secrets/foundry_pg.env.

With that env sourced, the 14 Postgres-backed tests now RUN and PASS against the
live database (no skips):

  tests/test_postgres_ledger.py
    - audit hash-chain verify (data integrity)
    - idempotent replay returns existing / changed-body raises (restore-safety)
    - stale-expected-version conflict + two-writer sequential conflict (rollback
      / concurrency guard)
    - transactional outbox enqueue-and-drain (release-recovery after restart)
    - record-dissent-immutable (irreversible record)
  tests/test_outbox_dispatcher.py
    - delivers and marks dispatched; at-most-once no re-dispatch
    - failure marks failed + requeues for retry; exhausted attempts stay failed
    - poll dispatches until controller stops

Local evidence run (this session):
  PYTHONPATH=src python3 -m unittest tests.test_postgres_ledger tests.test_outbox_dispatcher
  -> Ran 14 tests ... OK  (3.1s)  [FOUNDRY_PG_PASSWORD sourced]

Code surface: src/foundry_council/postgres_ledger.py, outbox_dispatcher.py,
crash_recovery.py (merged in PR #58, commit 723f36e).

This changes ONLY C2: BLOCKED -> VERIFIED with durable evidence. C3 and C6
remain BLOCKED (they require an actual deployed release, which does not exist
yet — no fabricated evidence). M9 stays PARTIAL.
"""
import json
from datetime import datetime, timezone

CP = "forge/state/checkpoints.json"
WI = "forge/work-items/FWI-M9-026.json"
GITHUB_BASE = "https://github.com/SangLeeVX/eigen-foundry"
NOW = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

# Evidence anchors
PR58_SHA = "723f36e"  # merge of PR #58 (postgres-ledger wiring + M9 partial close)
M2_PG_SHA = "99a7b58"  # FWI-M2-C1/C2/C3 Postgres ledger backend + outbox dispatcher

C2_EVIDENCE = [
    {
        "evidence_id": "EVD-M9-C2-MERGE",
        "type": "GITHUB_MERGE_COMMIT",
        "locator": f"{GITHUB_BASE}/commit/723f36ed4a2d7443482905a914fecc342e30e813",
        "immutable_revision": "723f36ed4a2d7443482905a914fecc342e30e813",
        "bound_criterion_id": "M9-C2",
        "result": "VERIFIED",
    },
    {
        "evidence_id": "EVD-M9-C2-PG",
        "type": "GITHUB_MERGE_COMMIT",
        "locator": f"{GITHUB_BASE}/commit/99a7b58cf588c70d6f25fc4726b5e8e7bfcb577b",
        "immutable_revision": "99a7b58cf588c70d6f25fc4726b5e8e7bfcb577b",
        "bound_criterion_id": "M9-C2",
        "result": "VERIFIED",
    },
]


def main() -> None:
    d = json.load(open(CP))
    d["updated_at"] = NOW
    for mm in d["milestones"]:
        if mm.get("milestone_id") == "M9":
            for ec in mm["exit_criteria"]:
                if ec["criterion_id"] == "M9-C2":
                    ec["status"] = "VERIFIED"
                    ec["evidence"] = C2_EVIDENCE
                elif ec["criterion_id"] in {"M9-C3", "M9-C6"}:
                    # unchanged: still require a real deployment
                    ec["status"] = "BLOCKED"
            # M9 stays PARTIAL: C3/C6 remain blocked.
            mm["status"] = "PARTIAL"
    json.dump(d, open(CP, "w"), indent=2, ensure_ascii=False)
    print("checkpoints: M9-C2 -> VERIFIED (live Postgres); M9-C3/C6 -> BLOCKED; M9 -> PARTIAL")

    w = json.load(open(WI))
    w["claim"] = {
        "actor": "governance-closeout-run",
        "run_id": "foundry-closeout-fwi-m9-026",
        "claimed_at": NOW,
        "expires_at": "2026-08-18T23:59:59Z",
    }
    w["review"]["review_evidence"] = [
        f"{GITHUB_BASE}/pull/58",
        f"{GITHUB_BASE}/commit/{PR58_SHA}",
        f"{GITHUB_BASE}/commit/{M2_PG_SHA}",
    ]
    json.dump(w, open(WI, "w"), indent=2, ensure_ascii=False)
    print("FWI-M9-026: claim recorded; review evidence set")


if __name__ == "__main__":
    main()
