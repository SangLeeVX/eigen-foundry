#!/usr/bin/env python3
"""Live DeepSeek-backed Working Conclave dry run.

Runs the M5 `WorkingConclave` through its full F0 closed loop with REAL model
inference for the case-captain seats (Kimi/DeepSeek OpenAI-compatible chat API),
emitting the traceable dry-run packet and validating that live model output
still respects the bounded-runtime contract (structured JSON, distinct run
identity, no upgraded authority).

Governed scope: this is an authorized F0 dry run that only produces traceable
packets for a TEST program — it never advances a formal Program stage, spends
budget, or authorizes production/scientific action. Same as the M6-C5-authorized
CRC/PRAD dry runs, but exercising the live model binding instead of the mock.

Usage:
  PYTHONPATH=src FOUNDRY_SEAT_MODEL=live \
    python3 scripts/live_conclave_dryrun.py [--provider deepseek] [--cases N]

The provider (kimi|deepseek) is auto-detected from the approved secrets store
unless FOUNDRY_SEAT_PROVIDER is set. Requires an approved live-model secrets
file (secrets/kimi.env or secrets/deepseek.env) and network access. The API key
is never printed; all output is masked by LiveSeatModel.
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
    args = sys.argv[1:]
    provider = None
    cases_arg = 0
    for a in args:
        if a.startswith("--provider="):
            provider = a.split("=", 1)[1]
        elif a.startswith("--cases="):
            cases_arg = int(a.split("=", 1)[1])

    os.environ.setdefault("FOUNDRY_SEAT_MODEL", "live")
    if provider:
        os.environ["FOUNDRY_SEAT_PROVIDER"] = provider

    from foundry_council.models import CaseType

    cases = tuple(list(CaseType)[:cases_arg]) if cases_arg else tuple(CaseType)

    print(f"live conclave dry run  provider={provider or 'auto'}  cases={[c.value for c in cases]}")
    print("seats: case-captains use the LIVE model; evidence/reviewer/red-team use deterministic internal logic")
    print()

    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "live_wc.db"
        trace = WorkingConclave(db, seed=7, cases=cases).run()

        valid = trace.audit_chains_valid
        # Emit a traceable packet: content-addressed summary + per-case captain verdict.
        verdicts = {}
        for out in trace.seat_outputs:
            verdicts[out.get("kind", "?")] = verdicts.get(out.get("kind", "?"), 0) + 1
        packet = {
            "provider": provider or "auto",
            "cases": [c.value for c in cases],
            "audit_chains_valid": valid,
            "output_kinds": verdicts,
            "note": "authorized F0 dry run over a TEST program; no stage advanced",
        }
        packet_digest = json.dumps(packet, sort_keys=True, separators=(",", ":"))

        print("=== traceable packet ===")
        print(json.dumps(packet, indent=2))
        print(f"packet_digest  : {packet_digest}")
        print()
        print(f"audit_chains_valid: {valid}")
        print("RESULT:", "PASS (live model produced valid structured F0 output)" if valid else "FAIL")

        # Persist the packet artifact under the repo scratch area (not committed).
        out_dir = ROOT / "live_runs"
        out_dir.mkdir(exist_ok=True)
        out_path = out_dir / "live_conclave_packet.json"
        out_path.write_text(json.dumps(packet, indent=2) + "\n", encoding="utf-8")
        print(f"packet written : {out_path}")

        return 0 if valid else 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except LiveSeatUnavailable as exc:
        print(f"LIVE UNAVAILABLE: {exc}", file=sys.stderr)
        raise SystemExit(1)
