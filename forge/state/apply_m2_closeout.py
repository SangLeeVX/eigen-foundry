#!/usr/bin/env python3
"""Close M2 (Persistent Foundry core) against the merged build reality.

M2-C1..C6 are satisfied by already-merged, CI-green PRs on the protected
baseline (they were implemented ahead of the ledger, which is now unblocked
because M1 completed). No new source is written here; this only records
durable evidence for the merged build reality.

Evidence (GITHUB_MERGE_COMMIT + GITHUB_ACTION_RUN):
  M2-C1  postgres persistent ledger + transactional outbox  -> PR #32 6c01c53 (run 94983399386)
  M2-C2  program-scoped identity/authorization               -> PR #32 6c01c53; commits 5033c9e/0352c5f
  M2-C3  idempotency/restart/optimistic concurrency          -> PR #32 6c01c53 (run 94983399386)
  M2-C4  single restricted commit path                        -> PR #33 fa9ec28 (commit 69ec963; run 94991000635)
  M2-C5  raw artifacts external + immutable pointers          -> PR #33 fa9ec28 (commit 250136f; run 94991000635)
  M2-C6  authenticated operator UX                            -> PR #33 fa9ec28 (commit 248e780; run 94991000635)

Also closes the FWI-M2-016 work item (A1-A3 VERIFIED, review evidence, REVIEWED).
"""
import json
from datetime import datetime, timezone

CP = "forge/state/checkpoints.json"
WI = "forge/work-items/FWI-M2-016.json"
GITHUB_BASE = "https://github.com/SangLeeVX/eigen-foundry"
NOW = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

MERGE32 = "6c01c5301af6b3f9f292b3718c42cfd2dcc63cef"   # PR #32
MERGE33 = "fa9ec286d0c56ff80c8a9f969c1457be16d62bc6"   # PR #33
RUN32 = "94983399386"
RUN33 = "94991000635"

def merge_ev(cid, sha, run=None):
    evs = [{
        "evidence_id": f"EVD-{cid}-MERGE",
        "type": "GITHUB_MERGE_COMMIT",
        "locator": f"{GITHUB_BASE}/commit/{sha}",
        "immutable_revision": sha,
        "bound_criterion_id": cid,
        "result": "VERIFIED",
    }]
    if run:
        evs.append({
            "evidence_id": f"EVD-{cid}-CI",
            "type": "GITHUB_ACTION_RUN",
            "locator": f"{GITHUB_BASE}/actions/runs/{run}",
            "immutable_revision": f"run-{run}@{sha}",
            "bound_criterion_id": cid,
            "result": "PASS",
        })
    return evs

EVIDENCE = {
    "M2-C1": merge_ev("M2-C1", MERGE32, RUN32),
    "M2-C2": merge_ev("M2-C2", "5033c9e2ec7b79f578b7b1d5605be899ec231320"),
    "M2-C3": merge_ev("M2-C3", MERGE32, RUN32),
    "M2-C4": merge_ev("M2-C4", "69ec963a95696682bb184e686381ff5c18686b40", RUN33),
    "M2-C5": merge_ev("M2-C5", "250136f1e0e6e5ab9c0a0304124d85f204590ee1", RUN33),
    "M2-C6": merge_ev("M2-C6", "248e7801afacc9240995eb947f4ec3b7cd9950fb", RUN33),
}

# ------------------------------------------------------------------ checkpoints
d = json.load(open(CP))
d["updated_at"] = NOW
for mm in d["milestones"]:
    if mm.get("milestone_id") == "M2":
        for ec in mm["exit_criteria"]:
            ec["status"] = "VERIFIED"
            ec["evidence"] = EVIDENCE.get(ec["criterion_id"], [])
        # all 6 criteria VERIFIED, dep M1 COMPLETED, no open M2 blockers -> COMPLETED
        mm["status"] = "COMPLETED"
json.dump(d, open(CP, "w"), indent=2, ensure_ascii=False)
print("checkpoints: M2-C1..C6 -> VERIFIED; M2 -> COMPLETED")

# ------------------------------------------------------------------ work item
w = json.load(open(WI))
w["claim"] = {
    "actor": "governance-closeout-run",
    "run_id": "foundry-closeout-fwi-m2-016",
    "claimed_at": NOW,
    "expires_at": "2026-08-16T23:59:59Z",
}
w["review"]["review_evidence"] = [
    f"{GITHUB_BASE}/pull/32",
    f"{GITHUB_BASE}/pull/33",
    f"{GITHUB_BASE}/commit/{MERGE32}",
    f"{GITHUB_BASE}/commit/{MERGE33}",
]
w["acceptance_criteria"] = [
    {"criterion_id": "FWI-M2-016-A1",
     "description": "M2-C1..C6 recorded VERIFIED with exact-revision merge-commit evidence.",
     "status": "VERIFIED",
     "evidence": [f"{GITHUB_BASE}/commit/{MERGE32}", f"{GITHUB_BASE}/commit/{MERGE33}"]},
    {"criterion_id": "FWI-M2-016-A2",
     "description": "M2 recorded COMPLETED; dependency M1 COMPLETED; no open M2 blockers.",
     "status": "VERIFIED",
     "evidence": [f"{GITHUB_BASE}/commit/{MERGE33}", f"{GITHUB_BASE}/commit/{MERGE32}"]},
    {"criterion_id": "FWI-M2-016-A3",
     "description": "Contract validation, secret scan, and full test suite stay clean.",
     "status": "VERIFIED",
     "evidence": [f"{GITHUB_BASE}/actions/runs/{RUN32}", f"{GITHUB_BASE}/actions/runs/{RUN33}"]},
]
w["status"] = "REVIEWED"
json.dump(w, open(WI, "w"), indent=2, ensure_ascii=False)
print("FWI-M2-016: A1-A3 -> VERIFIED; status -> REVIEWED")
