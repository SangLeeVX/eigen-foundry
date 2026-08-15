#!/usr/bin/env python3
"""FWI-M2 promotion runbook: cut over the Foundry ledger from SQLite to Postgres.

Performs a verified, idempotent backfill of a SQLite ledger into a Postgres
ledger while preserving the audit hash chain, then (optionally) records the
cutover so the app starts on Postgres via FOUNDRY_LEDGER_DSN.

Usage:
    python3 promote_ledger.py --sqlite path/to/ledger.db [options]

Options:
    --dsn <postgres-dsn>   Target Postgres DSN (URI or key-value) OR
                           FOUNDRY_LEDGER_DSN env var (default).
    --dry-run              Report what would be migrated without writing.
    --verify               Verify the migrated audit hash chains (default on).
    --emit-dsn <path>      Write FOUNDRY_LEDGER_DSN=<dsn> to a file (for shell
                           sourcing / deployment) after a successful cutover.
    --no-emit              Skip emitting the DSN (manual cutover).

Governance:
    - Only writes to the target Postgres; never touches protected/main branches.
    - Logs a durable cutover record to the Postgres outbox-adjacent audit trail.
    - The app honours FOUNDRY_LEDGER_DSN via build_ledger() for a clean cutover.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from foundry_council.ledger_protocol import (  # noqa: E402
    build_ledger,
    migrate_sqlite_to_postgres,
)
from foundry_council.postgres_ledger import PostgresLedger  # noqa: E402


def _require_postgres_binary() -> None:
    try:
        import psycopg  # noqa: F401
    except ImportError as exc:  # pragma: no cover - environment check
        raise SystemExit(
            "psycopg is required to run the Postgres promotion. "
            "Install with: pip install 'psycopg[binary]'"
        ) from exc


def _redact_dsn(dsn: str) -> str:
    """Mask credential material in a DSN for log/console output."""
    import re

    return re.sub(r"(password\s*=\s*)[^\s]+", r"\1<redacted>", dsn, flags=re.IGNORECASE)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sqlite", required=True, type=Path, help="SQLite ledger path")
    parser.add_argument("--dsn", default=None, help="Postgres DSN (else FOUNDRY_LEDGER_DSN)")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--no-verify", action="store_true", help="skip hash-chain verification")
    parser.add_argument("--emit-dsn", type=Path, default=None, help="write FOUNDRY_LEDGER_DSN to file")
    parser.add_argument("--no-emit", action="store_true", help="do not emit cutover DSN")
    args = parser.parse_args(argv)

    _require_postgres_binary()

    dsn = args.dsn or os.environ.get("FOUNDRY_LEDGER_DSN")
    if not dsn:
        print("No Postgres DSN: pass --dsn or set FOUNDRY_LEDGER_DSN.", file=sys.stderr)
        return 2

    if not args.sqlite.exists():
        print(f"SQLite ledger not found: {args.sqlite}", file=sys.stderr)
        return 2

    print(f"SQLite source : {args.sqlite.resolve()}")
    print(f"Postgres DSN  : {_redact_dsn(dsn)}")
    if args.dry_run:
        print("[dry-run] would create Postgres ledger and backfill all rows")
        return 0

    target = build_ledger(dsn)
    if not isinstance(target, PostgresLedger):
        print(f"DSN did not resolve to PostgresLedger: {type(target).__name__}", file=sys.stderr)
        return 2

    counts = migrate_sqlite_to_postgres(
        args.sqlite,
        target,
        verify=not args.no_verify,
    )
    print("Migration complete:")
    for name, value in counts.items():
        if value:
            print(f"  {name}: {value}")

    if not args.no_emit:
        if args.emit_dsn:
            args.emit_dsn.write_text(f"FOUNDRY_LEDGER_DSN={dsn}\n")
            print(f"Cutover DSN emitted to {args.emit_dsn}")
        else:
            print("Cutover switch: run with FOUNDRY_LEDGER_DSN=<dsn> (see build_ledger).")

    print("OK: ledger promoted to Postgres with hash-chain preserved.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
