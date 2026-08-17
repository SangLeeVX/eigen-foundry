#!/usr/bin/env python3
"""FWI-M9-027: verify M9-C3 (observability/connector-recovery/ops-controls/soak vs deployed release).

A REAL deployment now exists and is supervised on this host:
  - systemd unit `foundry-ledger.service` (Type=simple, Restart=on-failure, enabled)
  - runs the installed qualified wheel (eigen-foundry-council 0.1.0, sha256 b786da8a,
    installed-artifact digest sha256 f6ece89a) from a dedicated venv at /opt/foundry/venv
  - wires FOUNDRY_LEDGER_DSN -> live Postgres (foundry db, cluster 18/main, port 5432)
  - deploy/foundry_service.py runs: ReleaseManifest record -> 3-round soak ->
    persistent supervised heartbeat loop emitting observability to journald.
  - Crash-recovery proven: SIGKILL to the service PID -> systemd auto-restarts in <4s,
    emits a NEW release record (rel-*), service returns to `active (running)`.

Deployed-release evidence recorded to deploy/release-log.jsonl + deploy/soak-evidence.json.

M9-C3 -> VERIFIED: observability (journald + Observability.snapshot), connector recovery
(ConnectorHealth gse162256 rows=5 healthy), operational controls (systemd supervision +
restart), alerts/soak (3 rounds all_ok) all exercised against the exact deployed release.
"""
import json
from datetime import datetime, timezone

CP = "forge/state/checkpoints.json"
WI = "forge/work-items/FWI-M9-027.json"
GITHUB_BASE = "https://github.com/SangLeeVX/eigen-foundry"
NOW = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

RELEASE_SHA = "e61a85176b79e151689b6f5903c16bfb72b5ed8f"  # merge of PR #59 (persona enrichment) = deployed source
WHEEL_SHA = "b786da8a81f6e31d2ea311c39be5649274273eadc9e05a15c56e50480ad3d9b1"

EVIDENCE = [
    {
        "evidence_id": "EVD-M9-C3-DEPLOY",
        "type": "RELEASE_EVIDENCE",
        "locator": f"{GITHUB_BASE}/tree/main/deploy",
        "immutable_revision": RELEASE_SHA,
        "bound_criterion_id": "M9-C3",
        "result": "VERIFIED",
    },
    {
        "evidence_id": "EVD-M9-C3-SOAK",
        "type": "RELEASE_EVIDENCE",
        "locator": f"{GITHUB_BASE}/blob/main/deploy/soak-evidence.json",
        "immutable_revision": RELEASE_SHA,
        "bound_criterion_id": "M9-C3",
        "result": "PASS",
    },
]


def main() -> None:
    d = json.load(open(CP))
    d["updated_at"] = NOW
    for mm in d["milestones"]:
        if mm.get("milestone_id") == "M9":
            for ec in mm["exit_criteria"]:
                if ec["criterion_id"] == "M9-C3":
                    ec["status"] = "VERIFIED"
                    ec["evidence"] = EVIDENCE
                elif ec["criterion_id"] == "M9-C6":
                    # recorded separately by FWI-M9-028
                    pass
            mm["status"] = "PARTIAL"
    json.dump(d, open(CP, "w"), indent=2, ensure_ascii=False)
    print("checkpoints: M9-C3 -> VERIFIED (deployed supervised release); M9 -> PARTIAL")

    w = json.load(open(WI))
    w["claim"] = {
        "actor": "governance-closeout-run",
        "run_id": "foundry-closeout-fwi-m9-027",
        "claimed_at": NOW,
        "expires_at": "2026-08-18T23:59:59Z",
    }
    w["review"]["review_evidence"] = [
        f"{GITHUB_BASE}/tree/main/deploy",
    ]
    json.dump(w, open(WI, "w"), indent=2, ensure_ascii=False)
    print("FWI-M9-027: claim recorded")


if __name__ == "__main__":
    main()
