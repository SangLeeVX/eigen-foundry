#!/usr/bin/env python3
"""Close M8 (Development lifecycle support) against the merged build reality.

All four M8 criteria are already satisfied by merged, CI-green code (PR #44);
this records durable evidence and marks M8 COMPLETED (pure reconciliation — I
verified every criterion maps to tested code; no M5-C5/M7-C6-style gap).

Evidence (GITHUB_MERGE_COMMIT + GITHUB_ACTION_RUN):
  M8-C1  F9-F12 policies + artifact contracts (IND-enabling, human proof,
         clinical proof, registration, externalization, lifecycle, termination)
         -> PR #44 3cc2ce1 (policy.py F9-F12 gates + route_policy + termination_workflow;
         run 95022735597).
  M8-C2  clinical/regulatory/safety/quality/legal/finance/executive approval
         paths explicit + enforced
         -> PR #44 3cc2ce1 (policy.py required-approver roles incl. regulatory/
         safety_lead/ip_legal/finance; ApprovalConsole multi-party enforce).
  M8-C3  integration boundaries, failure routes, hold/expiry, transitions pass
         synthetic/retrospective dry run
         -> PR #44 3cc2ce1 (test_m8_lifecycle + termination_workflow dry-run).
  M8-C4  no software dry run changes or is reported as real advancement
         -> PR #44 3cc2ce1 (stage_runner/production_ops MODEL_PREDICTION + dry-run-only).

Also closes FWI-M8-022 (A1-A3 VERIFIED, review evidence, REVIEWED).
"""
import json
from datetime import datetime, timezone

CP = "forge/state/checkpoints.json"
WI = "forge/work-items/FWI-M8-022.json"
GITHUB_BASE = "https://github.com/SangLeeVX/eigen-foundry"
NOW = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

PR44 = "3cc2ce1402209b9f9f825ddb77650b67cfcfb53c"   # dev-lifecycle (M8-C1..C4)
RUN44 = "95022735597"


def mv(cid, sha, suffix="MERGE"):
    return [{
        "evidence_id": f"EVD-{cid}-{suffix}",
        "type": "GITHUB_MERGE_COMMIT",
        "locator": f"{GITHUB_BASE}/commit/{sha}",
        "immutable_revision": sha,
        "bound_criterion_id": cid,
        "result": "VERIFIED",
    }]


def av(cid):
    return [{
        "evidence_id": f"EVD-{cid}-CI",
        "type": "GITHUB_ACTION_RUN",
        "locator": f"{GITHUB_BASE}/actions/runs/{RUN44}",
        "immutable_revision": f"run-{RUN44}@{PR44}",
        "bound_criterion_id": cid,
        "result": "PASS",
    }]


EVIDENCE = {
    "M8-C1": mv("M8-C1", PR44) + av("M8-C1"),
    "M8-C2": mv("M8-C2", PR44) + av("M8-C2"),
    "M8-C3": mv("M8-C3", PR44) + av("M8-C3"),
    "M8-C4": mv("M8-C4", PR44) + av("M8-C4"),
}

# ------------------------------------------------------------------ checkpoints
d = json.load(open(CP))
d["updated_at"] = NOW
for mm in d["milestones"]:
    if mm.get("milestone_id") == "M8":
        for ec in mm["exit_criteria"]:
            ec["status"] = "VERIFIED"
            ec["evidence"] = EVIDENCE.get(ec["criterion_id"], [])
        mm["status"] = "COMPLETED"   # dep M7 COMPLETED, no open M8 blockers
json.dump(d, open(CP, "w"), indent=2, ensure_ascii=False)
print("checkpoints: M8-C1..C4 -> VERIFIED; M8 -> COMPLETED")

# ------------------------------------------------------------------ work item
w = json.load(open(WI))
w["claim"] = {
    "actor": "governance-closeout-run",
    "run_id": "foundry-closeout-fwi-m8-022",
    "claimed_at": NOW,
    "expires_at": "2026-08-16T23:59:59Z",
}
w["review"]["review_evidence"] = [
    f"{GITHUB_BASE}/pull/44",
    f"{GITHUB_BASE}/commit/{PR44}",
]
w["acceptance_criteria"] = [
    {"criterion_id": "FWI-M8-022-A1",
     "description": "M8-C1..C4 recorded VERIFIED with exact-revision merge-commit + action-run evidence.",
     "status": "VERIFIED",
     "evidence": [f"{GITHUB_BASE}/commit/{PR44}"]},
    {"criterion_id": "FWI-M8-022-A2",
     "description": "M8 recorded COMPLETED; dependency M7 COMPLETED; no open M8 blockers.",
     "status": "VERIFIED",
     "evidence": [f"{GITHUB_BASE}/commit/{PR44}"]},
    {"criterion_id": "FWI-M8-022-A3",
     "description": "Contracts, secret scan, history-secret scan, schema drift, wheel, full test suite clean.",
     "status": "VERIFIED",
     "evidence": [f"{GITHUB_BASE}/actions/runs/{RUN44}"]},
]
w["status"] = "REVIEWED"
json.dump(w, open(WI, "w"), indent=2, ensure_ascii=False)
print("FWI-M8-022: A1-A3 -> VERIFIED; status -> REVIEWED")
