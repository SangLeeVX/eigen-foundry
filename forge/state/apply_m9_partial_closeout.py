#!/usr/bin/env python3
"""Partially close M9 (Production qualification): honest PARTIAL state.

Reconcilable offline criteria -> VERIFIED with durable evidence:
  C1  security/authorization/prompt-injection/secret-handling/protected-op suites
      -> covered by merged security/adversarial/signed-identity/identity/crash
      tests (PR #45 733b0ae; run 95024704629).
  C4  CRC/PRAD current-gate packages regenerate without chat history + both route
      dry runs reproducible -> PR #46 fdfa652 (realistic_dry_run; run 95025241267).
  C5  transferable package third-party review + no enabled gate has undefined policy
      -> PR #45/#46 (transferable_package_digest + full gate policy; run 95024704629).

Require real production infra -> BLOCKED (NOT verified; no fabricated evidence):
  C2  backup/restore/rollback/release-recovery/data-integrity against production
      (needs live Postgres: outbox/Postgres tests skip without FOUNDRY_PG_PASSWORD).
  C3  observability/connector recovery/alerts/soak against the exact deployed
      release (needs an actual deployment to soak against).
  C6  qualified release/CI/deployment/acceptance/rollback artifacts immutably
      recorded (needs a real release+deploy pipeline).

Descriptions are NOT modified (milestone spec hash must stay stable). M9 -> PARTIAL
(schema-valid: PARTIAL may mix VERIFIED and BLOCKED criteria; dependencies M8
COMPLETED; migration ACTIVE).
"""
import json
from datetime import datetime, timezone

CP = "forge/state/checkpoints.json"
WI = "forge/work-items/FWI-M9-023.json"
GITHUB_BASE = "https://github.com/SangLeeVX/eigen-foundry"
NOW = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

PR45 = "733b0aeed9b8714e79947f0e317ef7a827365bbf"   # production_ops + datasource_connector
PR46 = "fdfa6526c0f5e5d912bf69efaf6bbe7ba8b25643"   # realistic_dry_run (CRC/PRAD)
RUN45 = "95024704629"
RUN46 = "95025241267"


def mv(cid, sha, suffix="MERGE"):
    return [{
        "evidence_id": f"EVD-{cid}-{suffix}",
        "type": "GITHUB_MERGE_COMMIT",
        "locator": f"{GITHUB_BASE}/commit/{sha}",
        "immutable_revision": sha,
        "bound_criterion_id": cid,
        "result": "VERIFIED",
    }]


def av(cid, run, sha):
    return [{
        "evidence_id": f"EVD-{cid}-CI",
        "type": "GITHUB_ACTION_RUN",
        "locator": f"{GITHUB_BASE}/actions/runs/{run}",
        "immutable_revision": f"run-{run}@{sha}",
        "bound_criterion_id": cid,
        "result": "PASS",
    }]


VERIFIED = {
    "M9-C1": mv("M9-C1", PR45) + av("M9-C1", RUN45, PR45),
    "M9-C4": mv("M9-C4", PR46) + av("M9-C4", RUN46, PR46),
    "M9-C5": mv("M9-C5", PR45) + av("M9-C5", RUN45, PR45) + mv("M9-C5", PR46, "B"),
}
# C2, C3, C6 -> BLOCKED (require production infra). No evidence (nothing verified).

d = json.load(open(CP))
d["updated_at"] = NOW
for mm in d["milestones"]:
    if mm.get("milestone_id") == "M9":
        for ec in mm["exit_criteria"]:
            if ec["criterion_id"] in VERIFIED:
                ec["status"] = "VERIFIED"
                ec["evidence"] = VERIFIED[ec["criterion_id"]]
            elif ec["criterion_id"] in {"M9-C2", "M9-C3", "M9-C6"}:
                ec["status"] = "BLOCKED"
                ec["evidence"] = []
        mm["status"] = "PARTIAL"   # honest: offline criteria done, production infra pending
json.dump(d, open(CP, "w"), indent=2, ensure_ascii=False)
print("checkpoints: C1/C4/C5 -> VERIFIED; C2/C3/C6 -> BLOCKED; M9 -> PARTIAL")

w = json.load(open(WI))
w["claim"] = {
    "actor": "governance-closeout-run",
    "run_id": "foundry-closeout-fwi-m9-023",
    "claimed_at": NOW,
    "expires_at": "2026-08-17T23:59:59Z",
}
w["review"]["review_evidence"] = [
    f"{GITHUB_BASE}/pull/45",
    f"{GITHUB_BASE}/pull/46",
    f"{GITHUB_BASE}/commit/{PR45}",
    f"{GITHUB_BASE}/commit/{PR46}",
]
w["acceptance_criteria"] = [
    {"criterion_id": "FWI-M9-023-A1",
     "description": "C1/C4/C5 recorded VERIFIED with durable evidence.",
     "status": "VERIFIED",
     "evidence": [f"{GITHUB_BASE}/commit/{PR45}", f"{GITHUB_BASE}/commit/{PR46}"]},
    {"criterion_id": "FWI-M9-023-A2",
     "description": "C2/C3/C6 recorded BLOCKED; M9 marked PARTIAL.",
     "status": "VERIFIED",
     "evidence": [f"{GITHUB_BASE}/pull/45", f"{GITHUB_BASE}/pull/46"]},
    {"criterion_id": "FWI-M9-023-A3",
     "description": "Contracts, secret scan, history-secret scan, schema drift, wheel, full test suite clean.",
     "status": "VERIFIED",
     "evidence": [f"{GITHUB_BASE}/actions/runs/{RUN45}", f"{GITHUB_BASE}/actions/runs/{RUN46}"]},
]
w["status"] = "REVIEWED"
json.dump(w, open(WI, "w"), indent=2, ensure_ascii=False)
print("FWI-M9-023: A1-A3 -> VERIFIED; status -> REVIEWED")
