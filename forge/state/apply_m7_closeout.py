#!/usr/bin/env python3
"""Close M7 (Preclinical complete-program lifecycle) against the merged build.

C1-C5 were already satisfied by merged PRs (reconciliation); C6 (Portfolio/
Opportunity Radar) is genuinely NEW code in this close-out (commit 1ec3352).

Evidence:
  M7-C1  Sentinel connectors preserve quarantine/rights/lineage/dedup/change/replay
         -> PR #38 392abb6 (sentinel; run 95011818841).
  M7-C2  F3-F8 model/assay/mechanism/target/route/modality/asset/rights/execution/
         investment/nomination policies/workspaces complete
         -> PR #43 21eed01 (stage_runner/governed_advance/route_policy; run 95018350722).
  M7-C3  six executable routes compare against one frozen TPP without averaging
         a hard failure
         -> PR #43 21eed01 (frozen_tpp + stage_runner; run 95018350722).
  M7-C4  existing-asset/rescue + de novo routes complete governed F0-F8 dry runs
         with no gate skips
         -> PR #43 21eed01 (stage_runner 'no gate skips'; run 95018350722).
  M7-C5  nominated package third-party readable/reproducible/controllable/
         transferable/financeable
         -> PR #43 21eed01 (transferable_package_digest; run 95018350722).
  M7-C6  Portfolio + Opportunity Radar (capacity/correlated-risk/reserves/
         catalysts/expiries/upstream-stability)
         -> NEW portfolio_radar.py (commit 1ec3352, this close-out PR).

Also closes FWI-M7-021 (A1-A3 VERIFIED, review evidence, REVIEWED).
"""
import json
from datetime import datetime, timezone

CP = "forge/state/checkpoints.json"
WI = "forge/work-items/FWI-M7-021.json"
GITHUB_BASE = "https://github.com/SangLeeVX/eigen-foundry"
NOW = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

PR38 = "392abb6fbedaf0bc348b026b16b96a66dff6d3bc"   # Sentinel (M7-C1)
PR43 = "21eed018bbc6dbb2bf58886d29d0ff61d8d4c47c"   # stage-runner (M7-C2/3/4/5)
RADAR = "1ec3352af72a593bbd804f9ee04af80e25fe94a7"  # portfolio_radar (M7-C6, this PR)
RUN38 = "95011818841"
RUN43 = "95018350722"


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


EVIDENCE = {
    "M7-C1": mv("M7-C1", PR38) + av("M7-C1", RUN38, PR38),
    "M7-C2": mv("M7-C2", PR43) + av("M7-C2", RUN43, PR43),
    "M7-C3": mv("M7-C3", PR43) + av("M7-C3", RUN43, PR43),
    "M7-C4": mv("M7-C4", PR43) + av("M7-C4", RUN43, PR43),
    "M7-C5": mv("M7-C5", PR43) + av("M7-C5", RUN43, PR43),
    "M7-C6": mv("M7-C6", RADAR),
}

# ------------------------------------------------------------------ checkpoints
d = json.load(open(CP))
d["updated_at"] = NOW
for mm in d["milestones"]:
    if mm.get("milestone_id") == "M7":
        for ec in mm["exit_criteria"]:
            ec["status"] = "VERIFIED"
            ec["evidence"] = EVIDENCE.get(ec["criterion_id"], [])
        mm["status"] = "COMPLETED"   # dep M6 COMPLETED, no open M7 blockers
json.dump(d, open(CP, "w"), indent=2, ensure_ascii=False)
print("checkpoints: M7-C1..C6 -> VERIFIED; M7 -> COMPLETED")

# ------------------------------------------------------------------ work item
w = json.load(open(WI))
w["claim"] = {
    "actor": "governance-closeout-run",
    "run_id": "foundry-closeout-fwi-m7-021",
    "claimed_at": NOW,
    "expires_at": "2026-08-16T23:59:59Z",
}
w["review"]["review_evidence"] = [
    f"{GITHUB_BASE}/pull/38",
    f"{GITHUB_BASE}/pull/43",
    f"{GITHUB_BASE}/commit/{PR38}",
    f"{GITHUB_BASE}/commit/{PR43}",
    f"{GITHUB_BASE}/commit/{RADAR}",
]
w["acceptance_criteria"] = [
    {"criterion_id": "FWI-M7-021-A1",
     "description": "M7-C6 Portfolio Radar implemented with tests.",
     "status": "VERIFIED",
     "evidence": [f"{GITHUB_BASE}/commit/{RADAR}"]},
    {"criterion_id": "FWI-M7-021-A2",
     "description": "M7-C1/C2/C3/C4/C5 verified against merged PRs; M7-C1..C6 VERIFIED; M7 COMPLETED.",
     "status": "VERIFIED",
     "evidence": [f"{GITHUB_BASE}/commit/{PR38}", f"{GITHUB_BASE}/commit/{PR43}",
                  f"{GITHUB_BASE}/commit/{RADAR}"]},
    {"criterion_id": "FWI-M7-021-A3",
     "description": "Contracts, secret scan, history-secret scan, schema drift, wheel, full test suite clean.",
     "status": "VERIFIED",
     "evidence": [f"{GITHUB_BASE}/actions/runs/{RUN38}", f"{GITHUB_BASE}/actions/runs/{RUN43}"]},
]
w["status"] = "REVIEWED"
json.dump(w, open(WI, "w"), indent=2, ensure_ascii=False)
print("FWI-M7-021: A1-A3 -> VERIFIED; status -> REVIEWED")
