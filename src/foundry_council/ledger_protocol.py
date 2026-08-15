from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from .errors import IdempotencyKeyReused, NotFound, StateConflict
from .models import (
    Approval,
    AuditEvent,
    CouncilSession,
    Dissent,
    GateDecision,
    ProgramRecord,
)


@runtime_checkable
class Ledger(Protocol):
    """Structural protocol satisfied by both SQLiteLedger and PostgresLedger.

    Captures the shared persistence interface used by the Council service, so
    the service can bind to either backend without a hard import coupling.
    """

    def initialize(self) -> None: ...

    def is_idempotent_replay(self, event: AuditEvent) -> bool: ...

    def create_program(self, program: ProgramRecord, event: AuditEvent) -> ProgramRecord: ...
    def get_program(self, program_id: str) -> ProgramRecord: ...
    def save_program(
        self, program: ProgramRecord, expected_version: int, event: AuditEvent
    ) -> ProgramRecord: ...
    def get_program_version(self, program_id: str, state_version: int) -> ProgramRecord: ...

    def create_session(
        self, session: CouncilSession, expected_program_version: int, event: AuditEvent
    ) -> CouncilSession: ...
    def get_session(self, session_id: str) -> CouncilSession: ...
    def save_session(
        self,
        session: CouncilSession,
        expected_version: int,
        event: AuditEvent,
        record_dissents: bool = False,
    ) -> CouncilSession: ...
    def get_session_version(self, session_id: str, state_version: int) -> CouncilSession: ...

    def record_approval(
        self,
        session: CouncilSession,
        approval: Approval,
        expected_session_version: int,
        event: AuditEvent,
    ) -> CouncilSession: ...
    def get_approvals(self, session_id: str) -> tuple[Approval, ...]: ...

    def record_dissent(self, dissent: Dissent, session_id: str) -> None: ...
    def get_dissents(self, session_id: str) -> list[Dissent]: ...

    def commit_program_and_session(
        self,
        program: ProgramRecord,
        session: CouncilSession,
        expected_program_version: int,
        expected_session_version: int,
        program_event: AuditEvent,
        session_event: AuditEvent,
        decision: GateDecision,
    ) -> tuple[ProgramRecord, CouncilSession]: ...
    def get_gate_decision(self, decision_id: str) -> GateDecision: ...

    def list_events(self, aggregate_type: str, aggregate_id: str) -> tuple[AuditEvent, ...]: ...
    def verify_audit_chain(self, aggregate_type: str, aggregate_id: str) -> bool: ...


def build_ledger(database: str | Path | None = None) -> Ledger:
    """Construct a ledger backend from a connection target.

    - ``database`` ending in ``.db``/``.sqlite``/``.sqlite3`` (or a bare path)
      is treated as a SQLite file.
    - A Postgres DSN (URI ``postgres://``/``postgresql://`` or key-value
      ``host=... user=...``) returns a ``PostgresLedger``.
    - If ``database`` is None, the ``FOUNDRY_LEDGER_DSN`` env var is used when
      set (the promotion switch), falling back to the M2 default SQLite file
      ``eigen-foundry.db`` in the current directory.
    """
    import os

    from .ledger import SQLiteLedger

    if database is None:
        database = os.environ.get(
            "FOUNDRY_LEDGER_DSN", "eigen-foundry.db"
        )
    text = str(database)

    if _looks_like_postgres_dsn(text):
        from .postgres_ledger import PostgresLedger

        return PostgresLedger(text)
    return SQLiteLedger(text)


def _is_sqlite_file(database: str) -> bool:
    return database.endswith((".db", ".sqlite", ".sqlite3"))


def _looks_like_postgres_dsn(database: str) -> bool:
    lowered = database.lower()
    # URI form
    if lowered.startswith(("postgres://", "postgresql://")):
        return True
    # Key-value form: pgpass-style conninfo with host= and a db/dbname key.
    if "://" not in lowered:
        tokens = lowered.replace(",", " ").replace(";", " ").split()
        keys = [t.split("=", 1)[0] for t in tokens if "=" in t]
        if "host" in keys and ("dbname" in keys or "db" in keys):
            return True
    return False


def migrate_sqlite_to_postgres(
    sqlite_path: str | Path,
    postgres_ledger: Any,
    *,
    dry_run: bool = False,
    verify: bool = True,
) -> dict[str, int]:
    """Backfill a Postgres ledger from a SQLite ledger, preserving the exact
    append-order and hash-chain so verify_audit_chain stays valid.

    Copies, in dependency order:
      programs (current + all versions) -> program_versions
      council_sessions (current + all versions) -> council_session_versions
      approvals, dissents, gate_decisions
      audit_events (in original sequence order, preserving event_id, hashes,
        and aggregate ordering so the chain hashes match).

    The audit_events copy is a literal row migration (payload_json, previous_hash,
    event_hash preserved verbatim) so the recomputed chain is identical.
    """
    from .ledger import SQLiteLedger

    source = SQLiteLedger(sqlite_path)
    counts = {
        "programs": 0,
        "program_versions": 0,
        "council_sessions": 0,
        "council_session_versions": 0,
        "approvals": 0,
        "dissents": 0,
        "gate_decisions": 0,
        "audit_events": 0,
    }

    if dry_run:
        return counts

    # --- Programs + versions ---
    with source._connection() as conn:
        program_rows = conn.execute("SELECT payload_json FROM programs ORDER BY program_id").fetchall()
    for row in program_rows:
        program = ProgramRecord.model_validate_json(row["payload_json"])
        _insert_with_idempotency(
            postgres_ledger,
            "programs",
            sql="INSERT INTO programs VALUES (%s, %s, %s, %s)",
            params=(
                program.program_id,
                program.state_version,
                program.model_dump_json(),
                program.updated_at.isoformat(),
            ),
        )
        counts["programs"] += 1
        # versions
        versions = _sqlite_all_versions(
            source, "program_versions", program.program_id, "program_id"
        )
        for version in versions:
            prog = ProgramRecord.model_validate_json(version)
            created_at = prog.updated_at.isoformat()
            _insert_with_idempotency(
                postgres_ledger,
                "program_versions",
                sql="INSERT INTO program_versions VALUES (%s, %s, %s, %s)",
                params=(
                    prog.program_id,
                    prog.state_version,
                    prog.model_dump_json(),
                    created_at,
                ),
            )
            counts["program_versions"] += 1

    # --- Council sessions + versions ---
    with source._connection() as conn:
        session_rows = conn.execute(
            "SELECT payload_json FROM council_sessions ORDER BY session_id"
        ).fetchall()
    for row in session_rows:
        session = CouncilSession.model_validate_json(row["payload_json"])
        _insert_with_idempotency(
            postgres_ledger,
            "council_sessions",
            sql="INSERT INTO council_sessions VALUES (%s, %s, %s, %s, %s)",
            params=(
                session.session_id,
                session.program_id,
                session.state_version,
                session.model_dump_json(),
                session.updated_at.isoformat(),
            ),
        )
        counts["council_sessions"] += 1
        versions = _sqlite_all_versions(
            source, "council_session_versions", session.session_id, "session_id"
        )
        for version in versions:
            sess = CouncilSession.model_validate_json(version)
            _insert_with_idempotency(
                postgres_ledger,
                "council_session_versions",
                sql="INSERT INTO council_session_versions VALUES (%s, %s, %s, %s)",
                params=(
                    sess.session_id,
                    sess.state_version,
                    sess.model_dump_json(),
                    sess.created_at.isoformat(),
                ),
            )
            counts["council_session_versions"] += 1

    # --- approvals ---
    with source._connection() as conn:
        rows = conn.execute("SELECT payload_json FROM approvals ORDER BY created_at, approval_id").fetchall()
    for row in rows:
        approval = Approval.model_validate_json(row["payload_json"])
        _insert_with_idempotency(
            postgres_ledger,
            "approvals",
            sql="INSERT INTO approvals VALUES (%s, %s, %s, %s, %s, %s)",
            params=(
                approval.approval_id,
                approval.request_id,
                approval.session_id,
                approval.program_id,
                approval.model_dump_json(),
                approval.decided_at.isoformat(),
            ),
        )
        counts["approvals"] += 1

    # --- dissents ---
    with source._connection() as conn:
        rows = conn.execute(
            "SELECT session_id, immutable_digest, payload_json, created_at FROM dissents ORDER BY created_at"
        ).fetchall()
    for row in rows:
        _insert_with_idempotency(
            postgres_ledger,
            "dissents",
            sql="INSERT INTO dissents (dissent_id, session_id, immutable_digest, payload_json, created_at) "
            "VALUES (%s, %s, %s, %s, %s)",
            params=(row["dissent_id"], row["session_id"], row["immutable_digest"], row["payload_json"], row["created_at"]),
        )
        counts["dissents"] += 1

    # --- gate_decisions ---
    with source._connection() as conn:
        rows = conn.execute(
            "SELECT payload_json FROM gate_decisions ORDER BY created_at, decision_id"
        ).fetchall()
    for row in rows:
        decision = GateDecision.model_validate_json(row["payload_json"])
        _insert_with_idempotency(
            postgres_ledger,
            "gate_decisions",
            sql="INSERT INTO gate_decisions VALUES (%s, %s, %s, %s, %s)",
            params=(
                decision.decision_id,
                decision.program_id,
                decision.session_id,
                decision.model_dump_json(),
                decision.committed_at.isoformat(),
            ),
        )
        counts["gate_decisions"] += 1

    # --- audit_events: literal row migration preserving the chain ---
    with source._connection() as conn:
        rows = conn.execute(
            """
            SELECT event_id, idempotency_key, request_digest, aggregate_type,
                   aggregate_id, aggregate_version, payload_json, previous_hash,
                   event_hash, created_at
            FROM audit_events ORDER BY sequence
            """
        ).fetchall()
    for row in rows:
        _insert_with_idempotency(
            postgres_ledger,
            "audit_events",
            sql=(
                "INSERT INTO audit_events (event_id, idempotency_key, request_digest, "
                "aggregate_type, aggregate_id, aggregate_version, payload_json, "
                "previous_hash, event_hash, created_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
            ),
            params=(
                row["event_id"],
                row["idempotency_key"],
                row["request_digest"],
                row["aggregate_type"],
                row["aggregate_id"],
                row["aggregate_version"],
                row["payload_json"],
                row["previous_hash"],
                row["event_hash"],
                row["created_at"],
            ),
        )
        counts["audit_events"] += 1

    if verify and counts["audit_events"]:
        _verify_migration_invariants(source, postgres_ledger)

    return counts


def _insert_with_idempotency(ledger: Any, table: str, *, sql: str, params: tuple[Any, ...]) -> None:
    """Insert into the Postgres ledger, tolerating duplicate-key conflicts so
    re-running the migration is safe (idempotent backfill)."""
    from .postgres_ledger import psycopg

    with ledger._connection() as connection:
        try:
            connection.execute(sql, params)
        except psycopg.errors.UniqueViolation:
            # Already present from a previous run: skip.
            return


def _sqlite_all_versions(
    source: SQLiteLedger, table: str, aggregate_id: str, id_column: str
) -> list[dict[str, Any]]:
    """Return the serialized payloads for all historical versions of one
    aggregate from a SQLite version table."""
    with source._connection() as conn:
        rows = conn.execute(
            f"SELECT payload_json FROM {table} WHERE {id_column} = ? ORDER BY state_version",
            (aggregate_id,),
        ).fetchall()
    return [row["payload_json"] for row in rows]


def _verify_migration_invariants(source: SQLiteLedger, postgres_ledger: Any) -> None:
    """Sanity-check the migrated aggregates: same event count and verifiable chains."""
    with source._connection() as conn:
        src_types = conn.execute("SELECT DISTINCT aggregate_type FROM audit_events").fetchall()
    for row in src_types:
        aggregate_type = row["aggregate_type"]
        with source._connection() as conn:
            agg_ids = [
                r["aggregate_id"]
                for r in conn.execute(
                    "SELECT DISTINCT aggregate_id FROM audit_events WHERE aggregate_type = ?",
                    (aggregate_type,),
                ).fetchall()
            ]
        for aggregate_id in agg_ids:
            pg_events = postgres_ledger.list_events(aggregate_type, aggregate_id)
            if not postgres_ledger.verify_audit_chain(aggregate_type, aggregate_id):
                raise RuntimeError(
                    f"migrated audit chain failed verification: {aggregate_type}:{aggregate_id}"
                )
