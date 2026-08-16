#!/usr/bin/env python3
"""Capture the ACTUAL model outputs from a live DeepSeek Working Conclave run.

Runs the live conclave and records each case-captain's FULL submitted claim
(statement, state, materiality, context, gate_impact, claim_id, evidence_refs)
by wrapping the governed ``submit_blind_opinion`` surface. We inspect the
quality of the model's SCIENTIFIC thinking, not just whether JSON validated.

Review/inspection harness only — never advances a Program stage.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from foundry_council.live_seat_model import LiveSeatUnavailable  # noqa: E402
from foundry_council.working_conclave import WorkingConclave  # noqa: E402


def main() -> int:
    os.environ.setdefault("FOUNDRY_SEAT_MODEL", "live")
    os.environ["FOUNDRY_SEAT_PROVIDER"] = "deepseek"

    captured: list[dict] = []

    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "capture.db"
        wc = WorkingConclave(db, seed=7)

        # Wrap the governed submit surface to snapshot the captain's claim.
        service = wc.service
        original = service.submit_blind_opinion

        def wrapped(session_id, opinion, claims, *args, **kwargs):
            for claim in claims:
                captured.append({
                    "case": opinion.case.value,
                    "claim_id": claim.claim_id,
                    "statement": claim.statement,
                    "state": claim.state.value,
                    "materiality": claim.materiality.value,
                    "context": claim.context,
                    "gate_impact": claim.gate_impact,
                    "evidence_refs": [getattr(e, "object_id", str(e)) for e in (claim.evidence_refs or ())],
                    "proposed_falsifier": claim.proposed_falsifier,
                })
            return original(session_id, opinion, claims, *args, **kwargs)

        service.submit_blind_opinion = wrapped  # type: ignore[method-assign]
        try:
            trace = wc.run()
        finally:
            service.submit_blind_opinion = original  # type: ignore[method-assign]

        print("audit_chains_valid:", trace.audit_chains_valid)
        wc.ledger.close() if hasattr(wc.ledger, "close") else None

        if not captured:
            print("ERROR: no captain claims captured")
            return 2

        out = ROOT / "live_runs" / "captured_claims.json"
        out.write_text(json.dumps(captured, indent=2), encoding="utf-8")
        print(f"captured {len(captured)} captain claims -> {out}")
        print()
        print("=" * 78)
        for c in captured:
            print(f"\n### CASE: {c['case']}")
            print(f"claim_id   : {c['claim_id']}")
            print(f"state      : {c['state']}")
            print(f"materiality: {c['materiality']}")
            print(f"statement  : {c['statement']}")
            print(f"context    : {c['context']}")
            print(f"gate_impact: {c['gate_impact']}")
            print(f"evidence   : {c['evidence_refs']}")
            if c.get("proposed_falsifier"):
                print(f"falsifier  : {c['proposed_falsifier'][:140]}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except LiveSeatUnavailable as exc:
        print(f"LIVE UNAVAILABLE: {exc}", file=sys.stderr)
        raise SystemExit(1)
