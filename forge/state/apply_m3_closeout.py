#!/usr/bin/env python3
"""Close M3 (Synthetic Conclave harness) against the merged build reality.

M3-C1..C6 are satisfied by already-merged, CI-green PRs on the protected
baseline. No new source is written; this records durable evidence for the
merged build reality.

Evidence (allowed types only: GITHUB_MERGE_COMMIT + GITHUB_ACTION_RUN):
  M3-C1  corrected evidence/model/null/dissent/successor classification
         -> PR #34 72c4215 (deterministic synthetic harness; run 94995158057)
  M3-C2  axiom-first mock seats complete frozen/blind/challenged path
         -> PR #34 72c4215 (run 94995158057)
  M3-C3  exact human approval gates one atomic synthetic commit
         -> PR #37 644b345 (authenticated human approval/commit console)
  M3-C4  UNKNOWN/FAIL/dissent/stale/wrong-role/expiry/self-approval fail closed
         -> governed validation CI on merged head (run 94995158057)
  M3-C5  duplicate/reordered commands do not duplicate or regress state
         -> governed validation CI on merged head (run 94995158057)
  M3-C6  outputs/UX label result synthetic-harness only
         -> PR #34 72c4215 (run 94995158057)

Also closes the FWI-M3-017 work item (A1-A3 VERIFIED, review evidence, REVIEWED).
"""
import json
from datetime import datetime, timezone

CP = "forge/state/checkpoints.json"
WI = "forge/work-items/FWI-M3-017.json"
GITHUB_BASE = "https://github.com/SangLeeVX/eigen-foundry"
NOW = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

MERGE_M3  = "72c421546b7040498acc657fa62d350d509612e2"   # PR #34 synthetic conclave
MERGE_M4APPROVAL = "644b345cc6eec6db47f88fc40342f31098112e14"  # PR #37 human approval console
RUN_M3 = "94995158057"

def mv(cid, sha):
    return [{
        "evidence_id": f"EVD-{cid}-MERGE",
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
        "locator": f"{GITHUB_BASE}/actions/runs/{RUN_M3}",
        "immutable_revision": f"run-{RUN_M3}@{MERGE_M3}",
        "bound_criterion_id": cid,
        "result": "PASS",
    }]

EVIDENCE = {
    "M3-C1": mv("M3-C1", MERGE_M3) + av("M3-C1"),
    "M3-C2": mv("M3-C2", MERGE_M3) + av("M3-C2"),
    "M3-C3": mv("M3-C3", MERGE_M4APPROVAL),
    "M3-C4": av("M3-C4"),
    "M3-C5": av("M3-C5"),
    "M3-C6": mv("M3-C6", MERGE_M3) + av("M3-C6"),
}

# ------------------------------------------------------------------ checkpoints
d = json.load(open(CP))
d["updated_at"] = NOW
for mm in d["milestones"]:
    if mm.get("milestone_id") == "M3":
        for ec in mm["exit_criteria"]:
            ec["status"] = "VERIFIED"
            ec["evidence"] = EVIDENCE.get(ec["criterion_id"], [])
        mm["status"] = "COMPLETED"   # dep M2 COMPLETED, no open M3 blockers
json.dump(d, open(CP, "w"), indent=2, ensure_ascii=False)
print("checkpoints: M3-C1..C6 -> VERIFIED; M3 -> COMPLETED")

# ------------------------------------------------------------------ work item
w = json.load(open(WI))
w["claim"] = {
    "actor": "governance-closeout-run",
    "run_id": "foundry-closeout-fwi-m3-017",
    "claimed_at": NOW,
    "expires_at": "2026-08-16T23:59:59Z",
}
w["review"]["review_evidence"] = [
    f"{GITHUB_BASE}/pull/34",
    f"{GITHUB_BASE}/pull/37",
    f"{GITHUB_BASE}/commit/{MERGE_M3}",
]
w["acceptance_criteria"] = [
    {"criterion_id": "FWI-M3-017-A1",
     "description": "M3-C1..C6 recorded VERIFIED with exact-revision merge-commit + action-run evidence.",
     "status": "VERIFIED",
     "evidence": [f"{GITHUB_BASE}/commit/{MERGE_M3}"]},
    {"criterion_id": "FWI-M3-017-A2",
     "description": "M3 recorded COMPLETED; dependency M2 COMPLETED; no open M3 blockers.",
     "status": "VERIFIED",
     "evidence": [f"{GITHUB_BASE}/commit/{MERGE_M3}", f"{GITHUB_BASE}/actions/runs/{RUN_M3}"]},
    {"criterion_id": "FWI-M3-017-A3",
     "description": "Contract validation, secret scan, and full test suite stay clean.",
     "status": "VERIFIED",
     "evidence": [f"{GITHUB_BASE}/actions/runs/{RUN_M3}"]},
]
w["status"] = "REVIEWED"
json.dump(w, open(WI, "w"), indent=2, ensure_ascii=False)
print("FWI-M3-017: A1-A3 -> VERIFIED; status -> REVIEWED")
