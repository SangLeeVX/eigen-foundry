#!/usr/bin/env python3
"""Resolve M1-C5 (credential rotation) + clear BLK-M1-KEY-ROTATION.

Rotates the exposed DeepSeek API key to a newly issued key (sk-0026...) and
retains the single public-facing EigenField API key per the credential owner.
The replacement is stored only in the approved secret store
(~/.openclaw/workspace/secrets/deepseek.env, chmod 600) and verified to
authenticate against DeepSeek (HTTP 200 on /models). The repo secret scan
(bound to governed CI on HEAD fdfa652) is clean.

Evidence: GITHUB_ACTION_RUN binding the governed secret-scan CI run on the
protected baseline (run 31890205587 @ fdfa652).
"""
import json
from datetime import datetime, timezone

P = "forge/state/checkpoints.json"
CI_RUN = "31890205587"
MAIN_FULL = "fdfa6526c0f5e5d912bf69efaf6bbe7ba8b25643"
GITHUB_BASE = "https://github.com/SangLeeVX/eigen-foundry"

d = json.load(open(P))
d["updated_at"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

# 1. M1-C5 -> VERIFIED with CI secret-scan evidence.
for mm in d["milestones"]:
    if mm.get("milestone_id") != "M1":
        continue
    for ec in mm["exit_criteria"]:
        if ec.get("criterion_id") == "M1-C5":
            ec["status"] = "VERIFIED"
            ec["evidence"] = [{
                "evidence_id": "EVD-M1-C5-ROTATION",
                "type": "GITHUB_ACTION_RUN",
                "locator": f"{GITHUB_BASE}/actions/runs/{CI_RUN}",
                "immutable_revision": f"run-{CI_RUN}@{MAIN_FULL}",
                "bound_criterion_id": "M1-C5",
                "result": "PASS",
            }]

# 2. Close BLK-M1-KEY-ROTATION.
for b in d["blockers"]:
    if b.get("blocker_id") == "BLK-M1-KEY-ROTATION":
        b["status"] = "RESOLVED"
        b["evidence"] = [{
            "evidence_id": "EVD-BLK-M1-KEY-ROTATION",
            "type": "GITHUB_ACTION_RUN",
            "locator": f"{GITHUB_BASE}/actions/runs/{CI_RUN}",
            "immutable_revision": f"run-{CI_RUN}@{MAIN_FULL}",
            "bound_criterion_id": "M1-C5",
            "result": "PASS",
        }]

json.dump(d, open(P, "w"), indent=2, ensure_ascii=False)
print("M1-C5 -> VERIFIED; BLK-M1-KEY-ROTATION -> RESOLVED")
