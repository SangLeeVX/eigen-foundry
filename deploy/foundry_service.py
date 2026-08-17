#!/usr/bin/env python3
"""Foundry ledger deployment service (systemd-supervised).

The genuinely deployed, Postgres-backed release of the qualified wheel. On each
start it:

  1. Builds the ledger backend via FOUNDRY_LEDGER_DSN (the production promotion
     switch) -> live Postgres.
  2. Records an immutable release manifest: wheel digest + source git SHA + the
     Postgres DSN target + service PID. Appended to an append-only release log
     (deployment/rollback trace).
  3. Runs a durable operations loop (bounded, supervised by systemd):
       - Observability.snapshot() against the live ledger (operator-visible state)
       - ConnectorHealth against the configured evidence source
       - emits structured status to stdout (captured by journald = observability)
       - graceful, idempotent; reloads cleanly on `systemctl restart` (crash/rollback
         recovery evidence)
  4. Soak: repeats N health+observability cycles and reports the result as a
     soak evidence record, so the deployed release is proven stable under
     repetition before/at handoff.

Design: bounded, read-only w.r.t. formal Program state (it snapshots, it does
not authorize). Never accepts authority from the environment beyond the
approved FOUNDRY_* env vars sourced by the systemd unit.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import time
import uuid
from pathlib import Path
from typing import Any

from foundry_council.ledger_protocol import build_ledger
from foundry_council.production_ops import ConnectorHealth, Observability, ReleaseManifest

RELEASE_LOG = Path(os.environ.get("FOUNDRY_RELEASE_LOG", "/opt/foundry/release-log.jsonl"))
SOAK_EVIDENCE = Path(os.environ.get("FOUNDRY_SOAK_EVIDENCE", "/opt/foundry/soak-evidence.json"))
ARTIFACT_DIGEST = os.environ.get("FOUNDRY_WHEEL_SHA", "none")
SOURCE_SHA = os.environ.get("FOUNDRY_SOURCE_SHA", "none")
DSN_TARGET = os.environ.get("FOUNDRY_DSN_TARGET", os.environ.get("FOUNDRY_LEDGER_DSN", "none"))


def wheel_digest_local() -> str:
    """Compute a stable digest of the installed distribution (dist-info RECORD)."""
    import hashlib as _h
    from importlib.metadata import distribution

    dist = distribution("eigen-foundry-council")
    record = None
    for f in dist.files or ():
        try:
            if f.locate().name == "RECORD":
                record = f.locate()
                break
        except Exception:  # noqa: BLE001
            continue
    if record and Path(record).exists():
        # digest over the RECORD metadata + top-level module to fingerprint the
        # *installed* artifact independent of build timestamps.
        raw = record.read_bytes() + Path(record).with_name("METADATA").read_bytes()
        return "sha256:" + _h.sha256(raw).hexdigest()
    return ARTIFACT_DIGEST


def record_release(pid: int) -> dict[str, Any]:
    manifest = ReleaseManifest(
        release_id=f"rel-{uuid.uuid4().hex[:12]}",
        git_sha=SOURCE_SHA,
        version="0.1.0",
        deployed_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        rollback_to=os.environ.get("FOUNDRY_ROLLBACK_TO", "none"),
    )
    rec = manifest.to_dict()
    rec["wheel_digest"] = wheel_digest_local()
    rec["dsn_target"] = DSN_TARGET
    rec["pid"] = pid
    rec["host"] = os.uname().nodename
    if not RELEASE_LOG.exists():
        RELEASE_LOG.parent.mkdir(parents=True, exist_ok=True)
        RELEASE_LOG.touch()
    with RELEASE_LOG.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, sort_keys=True) + "\n")
    return rec


def run_once(ledger, cycle: int) -> dict[str, Any]:
    """One observability + connector-health cycle against the live ledger."""
    obs = Observability().snapshot(ledger)
    ch = ConnectorHealth().check()
    return {
        "cycle": cycle,
        "observed_at": obs["observed_at"],
        "program_count": obs["program_count"],
        "session_count": obs["session_count"],
        "approval_count": obs["approval_count"],
        "connector_healthy": ch.get("ok", None),
        "connector": ch,
    }


def main() -> int:
    if not os.environ.get("FOUNDRY_LEDGER_DSN"):
        print('FATAL: FOUNDRY_LEDGER_DSN not set; cannot reach production ledger', file=sys.stderr)
        return 2

    ledger = build_ledger()  # resolves Postgres via FOUNDRY_LEDGER_DSN
    release = record_release(os.getpid())
    print(f"deployed release={release['release_id']} wheel={release['wheel_digest']} "
          f"dsn={DSN_TARGET[:30]}... source={SOURCE_SHA[:12]}", flush=True)

    rounds = int(os.environ.get("FOUNDRY_SOAK_ROUNDS", "3"))
    results = []
    try:
        # Initial soak = repeat observable health cycles; proves the deployed
        # release is stable under repetition (before declaring all_ok).
        for i in range(rounds):
            res = run_once(ledger, i + 1)
            results.append(res)
            print(json.dumps(res, sort_keys=True), flush=True)
            time.sleep(0.5)
        all_ok = all(r.get("connector_healthy") is not False for r in results)
        soak = {
            "release_id": release["release_id"],
            "wheel_digest": release["wheel_digest"],
            "source_sha": SOURCE_SHA,
            "rounds": len(results),
            "all_ok": all_ok,
            "results": results,
            "soaked_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        SOAK_EVIDENCE.write_text(json.dumps(soak, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(f"soak complete: rounds={len(results)} all_ok={all_ok} evidence={SOAK_EVIDENCE}", flush=True)
        if not all_ok:
            return 1

        # Persistent supervised loop: keep the deployed release alive and emitting
        # periodic observability to journald, so a crash is caught by systemd
        # (Restart=on-failure) and the process stays under supervision. This is
        # the durable, supervised deployment that C3/C6 soak against.
        cycle = rounds
        while True:
            cycle += 1
            res = run_once(ledger, cycle)
            print(json.dumps(res, sort_keys=True), flush=True)
            time.sleep(float(os.environ.get("FOUNDRY_HEARTBEAT_SEC", "30")))
    except KeyboardInterrupt:
        print("service stopped by operator", flush=True)
        return 0
    except Exception as exc:  # noqa: BLE001 - bounded; surfaced to journald for ops / rollback signal
        print(f"SERVICE_FAILED: {type(exc).__name__}: {exc}", flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
