from __future__ import annotations

import argparse
import json
from pathlib import Path

from .errors import FoundryError
from .ledger import SQLiteLedger


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Eigen Foundry Council ledger utility")
    subcommands = parser.add_subparsers(dest="command", required=True)

    initialize = subcommands.add_parser("init-db", help="create the SQLite MVP ledger")
    initialize.add_argument("--db", type=Path, required=True)

    program = subcommands.add_parser("show-program", help="print a canonical Program snapshot")
    program.add_argument("--db", type=Path, required=True)
    program.add_argument("program_id")

    session = subcommands.add_parser("show-session", help="print an internal council aggregate")
    session.add_argument("--db", type=Path, required=True)
    session.add_argument("session_id")

    audit = subcommands.add_parser("verify-audit", help="verify an aggregate audit hash chain")
    audit.add_argument("--db", type=Path, required=True)
    audit.add_argument("aggregate_type", choices=("PROGRAM", "COUNCIL_SESSION", "APPROVAL"))
    audit.add_argument("aggregate_id")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        ledger = SQLiteLedger(args.db)
        if args.command == "init-db":
            result = {"status": "initialized", "database": str(args.db.resolve())}
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

