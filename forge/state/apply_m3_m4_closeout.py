#!/usr/bin/env python3
"""Apply M3 (Synthetic Conclave harness) + M4 (Working Conclave) close-out to
forge/state/checkpoints.json.

Bounds evidence to verifiable facts on protected main (@ fdfa652, PR #46):
  M3 completed: deterministic synthetic harness, all 6 exit criteria verified
                 against merged commit SHAs + green test evidence.
  M4 partial:    orchestration + seat runtime + approval console verified;
                 live model binding (M4-C1) and live-identity auth commit
                 (M4-C4) remain PENDING because seats still run deterministic
                 mock models and no OIDC/service identity is wired.

Evidence bound to:
  - fea8e16  PR #34  FWI-M3 synthetic Conclave harness
  - 0688b75  PR #35  FWI-M4 Working Conclave seat runtime
  - 18c16ea  PR #36  FWI-M4 Working Conclave orchestrator
  - 644b345  PR #37  FWI-M4 authenticated human approval/commit console
  - test suite: 173 passed / 20 skipped / 8 subtests (pytest, PYTHONPATH=src)
    incl. test_synthetic_conclave, test_working_conclave, test_seat_runtime,
    test_approval_console, test_council, test_single_commit_path, test_crash_recovery.
"""
import json
from datetime import datetime, timezone

P = "forge/state/checkpoints.json"
MAIN_FULL = "fdfa6526c0f5e5d912bf69efaf6bbe7ba8b25643"
SHA = {
    "M3": "fea8e16",
    "M4SEAT": "0688b75",
    "M4ORCH": "18c16ea",
    "M4APPROVAL": "644b345",
}

GITHUB_BASE = "https://github.com/SangLeeVX/eigen-foundry"


def merge_evid(cid, suffix, sha, result="VERIFIED"):
    return [{
        "evidence_id": f"EVD-{cid}-{suffix}",
        "type": "GITHUB_MERGE_COMMIT",
        "locator": f"{GITHUB_BASE}/commit/{sha}",
        "immutable_revision": sha,
        "bound_criterion_id": cid,
        "result": result,
    }]


def test_evid(cid, suffix, result="PASS"):
    return [{
        "evidence_id": f"EVD-{cid}-{suffix}",
        "type": "TEST_SUITE",
        "locator": f"{GITHUB_BASE}/blob/{MAIN_FULL}/tests",
        "immutable_revision": MAIN_FULL,
        "bound_criterion_id": cid,
        "result": result,
        "detail": "pytest: 173 passed / 20 skipped / 8 subtests (PYTHONPATH=src)",
    }]


d = json.load(open(P))
d["updated_at"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

for mm in d["milestones"]:
    mid = mm.get("milestone_id")
    if mid == "M3":
        # Each exit criterion + the evidence that proves it on main.
        verified = [
            # M3-C1 corrected evidence/model/null/dissent/successor classification
            ("M3-C1", merge_evid("M3-C1", "corrected", SHA["M3"])),
            # M3-C2 Axiom-first mock seats complete frozen/blind/challenged/red-team path
            ("M3-C2", merge_evid("M3-C2", "f0crucible", SHA["M3"])),
            # M3-C3 exact human approval gates one atomic synthetic commit
            ("M3-C3", merge_evid("M3-C3", "atomic", SHA["M4APPROVAL"])),
            # M3-C4 UNKNOWN/FAIL/dissent/stale/wrong-role/expiry/self-approval fail closed
            ("M3-C4", test_evid("M3-C4", "failclosed")),
            # M3-C5 duplicate/reordered commands do not duplicate or regress
            ("M3-C5", test_evid("M3-C5", "replay")),
            # M3-C6 outputs labeled harness-only
            ("M3-C6", merge_evid("M3-C6", "harnesslabel", SHA["M3"])),
        ]
        for cid, evid in verified:
            for ec in mm["exit_criteria"]:
                if ec.get("criterion_id") == cid:
                    ec["status"] = "VERIFIED"
                    ec["evidence"] = evid
        mm["status"] = "COMPLETED"

    elif mid == "M4":
        m4_update = [
            # M4-C1 live versioned prompts/models + distinct run identity
            ("M4-C1", test_evid("M4-C1", "runid"),
             "PARTIAL", "distinct run identity verified; live model binding pending (seats run deterministic mocks)"),
            # M4-C2 structured outputs, traces, reproducibility, bounded failure
            ("M4-C2", test_evid("M4-C2", "structured"),
             "VERIFIED", "structured-output + bounded-failure fail-closed verified"),
            # M4-C3 live seats reproduce synthetic F0 without gaining authority
            ("M4-C3", test_evid("M4-C3", "noauth"),
             "PARTIAL", "seat reproduction verified on mocks; live-model authority boundary pending"),
            # M4-C4 authenticated humans approve exact immutable packets + atomic commit
            ("M4-C4", merge_evid("M4-C4", "approvalconsole", SHA["M4APPROVAL"]),
             "PARTIAL", "approval console + atomic commit verified; OIDC/service identity not wired"),
            # M4-C5 outage/malformed/timeout/partial-session recovery
            ("M4-C5", test_evid("M4-C5", "recovery"),
             "VERIFIED", "malformed/timeout/partial-session fail-closed verified via crash recovery suite"),
        ]
        for cid, evid, status, note in m4_update:
            for ec in mm["exit_criteria"]:
                if ec.get("criterion_id") == cid:
                    ec["status"] = status
                    ec["evidence"] = evid
                    if note:
                        ec["note"] = note
        mm["status"] = "IN_PROGRESS"

json.dump(d, open(P, "w"), indent=2, ensure_ascii=False)
print("checkpoints updated:")
for mm in d["milestones"]:
    if mm.get("milestone_id") in ("M3", "M4"):
        print(f"  {mm['milestone_id']}: {mm['status']}")
        for ec in mm["exit_criteria"]:
            print(f"    {ec['criterion_id']}: {ec['status']}")
