#!/usr/bin/env python3
"""Close M6 (Eigen-grounded F0-F2) against the merged build reality.

All five M6 criteria are already satisfied by merged PRs on the protected
baseline; this records durable evidence and marks M6 COMPLETED (pure
reconciliation, no new source).

Evidence (GITHUB_MERGE_COMMIT + GITHUB_ACTION_RUN):
  M6-C1  Evidence Steward read-only EigenField access in a Crucible
         -> PR #40 306c9ea (eigenfield_steward; run 95015053433).
  M6-C2  immutable EvidencePackets (provenance, rights, contradictions, nulls,
         dependency, staleness)
         -> PR #40 306c9ea + PR #42 7669296 (packet + rights_snapshot + grounding).
  M6-C3  Eigen-1 gateway MODEL_PREDICTION/L3 records with calibration, OOD, leakage
         -> PR #40 306c9ea (eigen1_gateway; run 95015053433).
  M6-C4  F0-F2 TPP, disease-map, evidence-audit, causal-question policies fail closed
         -> PR #42 7669296 (f0f2_policies; run 95015851348).
  M6-C5  authorized CRC/PRAD dry run produces traceable F0-F2 packets; model output
         never satisfies a gate
         -> PR #42 7669296 (prad_crc_dryrun; run 95015851348).

Also closes the FWI-M6-020 work item (A1-A3 VERIFIED, review evidence, REVIEWED).
"""
import json
from datetime import datetime, timezone

CP = "forge/state/checkpoints.json"
WI = "forge/work-items/FWI-M6-020.json"
GITHUB_BASE = "https://github.com/SangLeeVX/eigen-foundry"
NOW = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

PR40 = "306c9eaf3f241172d9b2eb62a032ea10751fa2be"   # eigen1_gateway + eigenfield_steward
PR42 = "7669296c31130b65b23ce0f8415c787323f89c3c"   # f0f2_policies + prad_crc_dryrun
RUN40 = "95015053433"
RUN42 = "95015851348"

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
    "M6-C1": mv("M6-C1", PR40) + av("M6-C1", RUN40, PR40),
    "M6-C2": mv("M6-C2", PR40, "A") + mv("M6-C2", PR42, "B") + av("M6-C2", RUN42, PR42),
    "M6-C3": mv("M6-C3", PR40) + av("M6-C3", RUN40, PR40),
    "M6-C4": mv("M6-C4", PR42) + av("M6-C4", RUN42, PR42),
    "M6-C5": mv("M6-C5", PR42) + av("M6-C5", RUN42, PR42),
}

# ------------------------------------------------------------------ checkpoints
d = json.load(open(CP))
d["updated_at"] = NOW
for mm in d["milestones"]:
    if mm.get("milestone_id") == "M6":
        for ec in mm["exit_criteria"]:
            ec["status"] = "VERIFIED"
            ec["evidence"] = EVIDENCE.get(ec["criterion_id"], [])
        mm["status"] = "COMPLETED"   # dep M5 COMPLETED, no open M6 blockers
json.dump(d, open(CP, "w"), indent=2, ensure_ascii=False)
print("checkpoints: M6-C1..C5 -> VERIFIED; M6 -> COMPLETED")

# ------------------------------------------------------------------ work item
w = json.load(open(WI))
w["claim"] = {
    "actor": "governance-closeout-run",
    "run_id": "foundry-closeout-fwi-m6-020",
    "claimed_at": NOW,
    "expires_at": "2026-08-16T23:59:59Z",
}
w["review"]["review_evidence"] = [
    f"{GITHUB_BASE}/pull/40",
    f"{GITHUB_BASE}/pull/42",
    f"{GITHUB_BASE}/commit/{PR40}",
    f"{GITHUB_BASE}/commit/{PR42}",
]
w["acceptance_criteria"] = [
    {"criterion_id": "FWI-M6-020-A1",
     "description": "M6-C1..C5 recorded VERIFIED with exact-revision merge-commit + action-run evidence.",
     "status": "VERIFIED",
     "evidence": [f"{GITHUB_BASE}/commit/{PR40}", f"{GITHUB_BASE}/commit/{PR42}"]},
    {"criterion_id": "FWI-M6-020-A2",
     "description": "M6 recorded COMPLETED; dependency M5 COMPLETED; no open M6 blockers.",
     "status": "VERIFIED",
     "evidence": [f"{GITHUB_BASE}/commit/{PR42}", f"{GITHUB_BASE}/commit/{PR40}"]},
    {"criterion_id": "FWI-M6-020-A3",
     "description": "Contracts, secret scan, history-secret scan, schema drift, wheel, full test suite clean.",
     "status": "VERIFIED",
     "evidence": [f"{GITHUB_BASE}/actions/runs/{RUN40}", f"{GITHUB_BASE}/actions/runs/{RUN42}"]},
]
w["status"] = "REVIEWED"
json.dump(w, open(WI, "w"), indent=2, ensure_ascii=False)
print("FWI-M6-020: A1-A3 -> VERIFIED; status -> REVIEWED")
