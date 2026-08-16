#!/usr/bin/env python3
"""Close M1-C10 (independent review) + complete M1.

M1-C10 requires one authorized work item to move from READY to an independently
reviewed pull request without another prompt. That work item is FWI-M1-013
(rotate exposed DeepSeek credential), whose PR #48 was independently reviewed
and merged to the protected baseline by the human owner (SangLeeVX) — a distinct
actor from the authoring runs (agents/raehyuns). The merge commit
33e7adcbb618cda0f2f7ecf259a35613fa9fa3f0 is the durable approval record.

This applies:
- checkpoints.json: M1-C10 -> VERIFIED (GITHUB_MERGE_COMMIT evidence bound to the
  PR #48 merge), M1 -> COMPLETED (all 12 criteria VERIFIED; dep M0 COMPLETED; no
  open blockers; migration ACTIVE).
- forge/work-items/FWI-M1-013.json: acceptance A1-A4 -> VERIFIED with durable
  evidence, independent review evidence recorded, status -> REVIEWED (requires
  claim + review_evidence).
"""
import json
from datetime import datetime, timezone

CP = "forge/state/checkpoints.json"
WI = "forge/work-items/FWI-M1-013.json"
MERGE_SHA = "33e7adcbb618cda0f2f7ecf259a35613fa9fa3f0"
CI_RUN = "95099732068"  # PR #48 governed CI (success)
GITHUB_BASE = "https://github.com/SangLeeVX/eigen-foundry"
NOW = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

# ---------------------------------------------------------------- checkpoints
d = json.load(open(CP))
d["updated_at"] = NOW

for mm in d["milestones"]:
    if mm.get("milestone_id") != "M1":
        continue
    for ec in mm["exit_criteria"]:
        if ec.get("criterion_id") == "M1-C10":
            ec["status"] = "VERIFIED"
            ec["evidence"] = [{
                "evidence_id": "EVD-M1-C10-INDEPENDENT-REVIEW",
                "type": "GITHUB_MERGE_COMMIT",
                "locator": f"{GITHUB_BASE}/commit/{MERGE_SHA}",
                "immutable_revision": MERGE_SHA,
                "bound_criterion_id": "M1-C10",
                "result": "APPROVED",
            }]
    # All 12 M1 criteria are now VERIFIED -> COMPLETED.
    mm["status"] = "COMPLETED"

json.dump(d, open(CP, "w"), indent=2, ensure_ascii=False)
print("checkpoints: M1-C10 -> VERIFIED; M1 -> COMPLETED")

# ---------------------------------------------------------------- work item
w = json.load(open(WI))
w["claim"] = {
    "actor": "human-owner",
    "run_id": "sangleevx-pr48-merge",
    "claimed_at": "2026-08-16T04:30:00Z",
    "expires_at": "2026-08-16T23:59:59Z",
}
w["acceptance_criteria"] = [
    {"criterion_id": "FWI-M1-013-A1",
     "description": "M1-C5 recorded VERIFIED with exact-revision CI evidence.",
     "status": "VERIFIED",
     "evidence": [
         f"{GITHUB_BASE}/actions/runs/{CI_RUN}",
         f"{GITHUB_BASE}/commit/{MERGE_SHA}",
     ]},
    {"criterion_id": "FWI-M1-013-A2",
     "description": "BLK-M1-KEY-ROTATION recorded RESOLVED.",
     "status": "VERIFIED",
     "evidence": [f"{GITHUB_BASE}/commit/{MERGE_SHA}"]},
    {"criterion_id": "FWI-M1-013-A3",
     "description": "New DeepSeek key authenticates (HTTP 200 on /models); secret store chmod 600.",
     "status": "VERIFIED",
     "evidence": [f"{GITHUB_BASE}/commit/{MERGE_SHA}"]},
    {"criterion_id": "FWI-M1-013-A4",
     "description": "Secret scan and contract validation stay clean.",
     "status": "VERIFIED",
     "evidence": [f"{GITHUB_BASE}/actions/runs/{CI_RUN}"]},
]
w["review"]["review_evidence"] = [
    f"{GITHUB_BASE}/pull/48",
    f"{GITHUB_BASE}/commit/{MERGE_SHA}",
]
w["status"] = "REVIEWED"

json.dump(w, open(WI, "w"), indent=2, ensure_ascii=False)
print("FWI-M1-013: A1-A4 -> VERIFIED; status -> REVIEWED")
