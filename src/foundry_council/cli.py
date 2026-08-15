from __future__ import annotations

import argparse
import json
from pathlib import Path

from .errors import FoundryError
from .ledger_protocol import build_ledger


def _add_source(args: argparse.Namespace) -> None:
    """Resolve the ledger backend from either a SQLite --db path or a Postgres --dsn."""
    if getattr(args, "dsn", None):
        return build_ledger(args.dsn)
    return build_ledger(getattr(args, "db"))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Eigen Foundry Council ledger utility")
    subcommands = parser.add_subparsers(dest="command", required=True)

    initialize = subcommands.add_parser("init-db", help="create the SQLite MVP ledger")
    initialize.add_argument("--db", type=Path, required=False)
    initialize.add_argument("--dsn", type=str, required=False, help="Postgres connection string")

    program = subcommands.add_parser("show-program", help="print a canonical Program snapshot")
    program.add_argument("--db", type=Path, required=False)
    program.add_argument("--dsn", type=str, required=False, help="Postgres connection string")
    program.add_argument("program_id")

    session = subcommands.add_parser("show-session", help="print an internal council aggregate")
    session.add_argument("--db", type=Path, required=False)
    session.add_argument("--dsn", type=str, required=False, help="Postgres connection string")
    session.add_argument("session_id")

    audit = subcommands.add_parser("verify-audit", help="verify an aggregate audit hash chain")
    audit.add_argument("--db", type=Path, required=False)
    audit.add_argument("--dsn", type=str, required=False, help="Postgres connection string")
    audit.add_argument("aggregate_type", choices=("PROGRAM", "COUNCIL_SESSION", "APPROVAL"))
    audit.add_argument("aggregate_id")

    status = subcommands.add_parser("status", help="operator summary of durable ledger state")
    status.add_argument("--db", type=Path, required=False)
    status.add_argument("--dsn", type=str, required=False, help="Postgres connection string")
    return parser


def _status_report(ledger) -> dict:
    program_ids = ledger.list_program_ids()
    session_ids = ledger.list_session_ids()
    programs = []
    for pid in program_ids:
        program = ledger.get_program(pid)
        programs.append({
            "program_id": pid,
            "stage": program.stage.value,
            "status": program.status.value,
            "state_version": program.state_version,
            "title": program.title,
        })
    sessions = []
    for sid in session_ids:
        session = ledger.get_session(sid)
        sessions.append({
            "session_id": sid,
            "program_id": session.program_id,
            "phase": session.phase.value,
            "state_version": session.state_version,
        })
    # Verify the audit hash chain across every aggregate so the operator sees
    # durable, untampered state (M2 exit criterion: operator sees durable state).
    program_chain_ok = all(ledger.verify_audit_chain("PROGRAM", pid) for pid in program_ids)
    session_chain_ok = all(ledger.verify_audit_chain("COUNCIL_SESSION", sid) for sid in session_ids)
    return {
        "backend": type(ledger).__name__,
        "program_count": len(program_ids),
        "session_count": len(session_ids),
        "programs": programs,
        "sessions": sessions,
        "audit_chains_valid": {"program": program_chain_ok, "session": session_chain_ok},
    }


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        ledger = _add_source(args)
        if args.command == "init-db":
            target = args.dsn if getattr(args, "dsn", None) else str(args.db.resolve())
            result = {"status": "initialized", "database": target}
        elif args.command == "status":
            result = _status_report(ledger)
        elif args.command == "show-program":
            result = ledger.get_program(args.program_id).model_dump(mode="json")
        elif args.command == "show-session":
            result = ledger.get_session(args.session_id).model_dump(mode="json")
        else:
            valid = ledger.verify_audit_chain(args.aggregate_type, args.aggregate_id)
            result = {"valid": valid, "aggregate_type": args.aggregate_type, "aggregate_id": args.aggregate_id}
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    except FoundryError as error:
        print(json.dumps(error.as_dict(), indent=2, sort_keys=True))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

