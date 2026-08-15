from __future__ import annotations

import hashlib
import uuid
from contextlib import contextmanager
from typing import Any, Iterator

import psycopg
from psycopg import Connection
from psycopg.rows import dict_row

from .errors import IdempotencyKeyReused, NotFound, StateConflict
from .ledger import (
    _assert_f0_route_invariant,
    _assert_single_commit_path,
    _dissent_canonical,
    _dissent_digest,
)
from .models import (
    Approval,
    AuditEvent,
    CouncilSession,
    Dissent,
    GateDecision,
    ProgramRecord,
)

_IMMUTABLE_TRIGGER_FN = """
CREATE OR REPLACE FUNCTION foundry_reject_mutation() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION '% are immutable', TG_TABLE_NAME;
END;
$$ LANGUAGE plpgsql;
"""

_IMMUTABLE_TABLES = (
    "program_versions",
    "council_session_versions",
    "approvals",
    "audit_events",
    "gate_decisions",
    "dissents",
)

# The outbox is payload-immutable but must allow the PENDING->DISPATCHED
# transition performed by drain_outbox. A dedicated trigger permits only that
# status transition and rejects any payload/identity mutation.
_OUTBOX_TRIGGER_FN = """
CREATE OR REPLACE FUNCTION foundry_outbox_guard() RETURNS trigger AS $$
DECLARE
    allowed_statuses TEXT[] := ARRAY['PENDING','DISPATCHED','FAILED'];
BEGIN
    IF NEW.outbox_id <> OLD.outbox_id OR
       NEW.aggregate_type <> OLD.aggregate_type OR
       NEW.aggregate_id <> OLD.aggregate_id OR
       NEW.event_id <> OLD.event_id OR
       NEW.payload_json <> OLD.payload_json OR
       NEW.created_at <> OLD.created_at THEN
        RAISE EXCEPTION 'outbox rows are immutable once written';
    END IF;
    IF NEW.status <> OLD.status THEN
        IF NOT (OLD.status = ANY(allowed_statuses) AND NEW.status = ANY(allowed_statuses)) THEN
            RAISE EXCEPTION 'invalid outbox status value';
        END IF;
        -- Dispatch requires a timestamp; failure/requeue transitions are free-form.
        IF NEW.status = 'DISPATCHED' AND NEW.dispatched_at IS NULL THEN
            RAISE EXCEPTION 'dispatch requires dispatched_at timestamp';
        END IF;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""

_SCHEMA = """
CREATE TABLE IF NOT EXISTS programs (
    program_id TEXT PRIMARY KEY,
    state_version INTEGER NOT NULL,
    payload_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS program_versions (
    program_id TEXT NOT NULL,
    state_version INTEGER NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (program_id, state_version)
);

CREATE TABLE IF NOT EXISTS council_sessions (
    session_id TEXT PRIMARY KEY,
    program_id TEXT NOT NULL REFERENCES programs(program_id),
    state_version INTEGER NOT NULL,
    payload_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS council_session_versions (
    session_id TEXT NOT NULL,
    state_version INTEGER NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (session_id, state_version)
);

CREATE TABLE IF NOT EXISTS approvals (
    approval_id TEXT PRIMARY KEY,
    request_id TEXT NOT NULL,
    session_id TEXT NOT NULL REFERENCES council_sessions(session_id),
    program_id TEXT NOT NULL REFERENCES programs(program_id),
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS dissents (
    dissent_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES council_sessions(session_id),
    immutable_digest TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (session_id, dissent_id)
);

CREATE TABLE IF NOT EXISTS gate_decisions (
    decision_id TEXT PRIMARY KEY,
    program_id TEXT NOT NULL REFERENCES programs(program_id),
    session_id TEXT NOT NULL REFERENCES council_sessions(session_id),
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_events (
    sequence BIGINT GENERATED ALWAYS AS IDENTITY,
    event_id TEXT NOT NULL UNIQUE,
    idempotency_key TEXT NOT NULL UNIQUE,
    request_digest TEXT NOT NULL,
    aggregate_type TEXT NOT NULL,
    aggregate_id TEXT NOT NULL,
    aggregate_version INTEGER NOT NULL,
    payload_json TEXT NOT NULL,
    previous_hash TEXT,
    event_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (sequence)
);

CREATE TABLE IF NOT EXISTS outbox (
    outbox_id TEXT PRIMARY KEY,
    aggregate_type TEXT NOT NULL,
    aggregate_id TEXT NOT NULL,
    event_id TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'PENDING',
    attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    created_at TEXT NOT NULL,
    dispatched_at TEXT
);
"""


class PostgresLedger:
    """Postgres backend for the Foundry ledger with immutability guards, a
    hash-chained audit log, idempotent replay, optimistic concurrency, and a
    transactional outbox."""

    def __init__(self, dsn: str) -> None:
        self.dsn = dsn
        self.initialize()

    @contextmanager
    def _connection(self) -> Iterator[Connection]:
        connection: Connection = psycopg.connect(self.dsn)
        connection.row_factory = dict_row
        connection.autocommit = False
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def initialize(self) -> None:
        with self._connection() as connection:
            connection.execute(_IMMUTABLE_TRIGGER_FN)
            connection.execute(_OUTBOX_TRIGGER_FN)
            connection.execute(_SCHEMA)
            for table in _IMMUTABLE_TABLES:
                connection.execute(
                    f"DROP TRIGGER IF EXISTS {table}_no_update ON {table}"
                )
                connection.execute(
                    f"""CREATE TRIGGER {table}_no_update
                        BEFORE UPDATE ON {table}
                        FOR EACH ROW EXECUTE FUNCTION foundry_reject_mutation()"""
                )
                connection.execute(
                    f"DROP TRIGGER IF EXISTS {table}_no_delete ON {table}"
                )
                connection.execute(
                    f"""CREATE TRIGGER {table}_no_delete
                        BEFORE DELETE ON {table}
                        FOR EACH ROW EXECUTE FUNCTION foundry_reject_mutation()"""
                )
            connection.execute("DROP TRIGGER IF EXISTS outbox_guard_update ON outbox")
            connection.execute(
                """CREATE TRIGGER outbox_guard_update
                    BEFORE UPDATE ON outbox
                    FOR EACH ROW EXECUTE FUNCTION foundry_outbox_guard()"""
            )
            connection.execute("DROP TRIGGER IF EXISTS outbox_guard_delete ON outbox")
            connection.execute(
                """CREATE TRIGGER outbox_guard_delete
                    BEFORE DELETE ON outbox
                    FOR EACH ROW EXECUTE FUNCTION foundry_reject_mutation()"""
            )

    @staticmethod
    def _request_digest(event: AuditEvent) -> str:
        from .ledger import SQLiteLedger

        return SQLiteLedger._request_digest(event)

    def _idempotent_replay(self, connection: Connection, event: AuditEvent) -> bool:
        row = connection.execute(
            "SELECT request_digest FROM audit_events WHERE idempotency_key = %s",
            (event.idempotency_key,),
        ).fetchone()
        if row is None:
            return False
        if row["request_digest"] != self._request_digest(event):
            raise IdempotencyKeyReused(
                "idempotency key was already used for a different command",
                idempotency_key=event.idempotency_key,
            )
        return True

    def is_idempotent_replay(self, event: AuditEvent) -> bool:
        with self._connection() as connection:
            return self._idempotent_replay(connection, event)

    def _append_event(self, connection: Connection, event: AuditEvent) -> str:
        previous = connection.execute(
            """
            SELECT event_hash FROM audit_events
            WHERE aggregate_type = %s AND aggregate_id = %s
            ORDER BY sequence DESC LIMIT 1
            """,
            (event.aggregate_type, event.aggregate_id),
        ).fetchone()
        previous_hash = previous["event_hash"] if previous else None
        payload_json = event.model_dump_json()
        chain_material = f"{previous_hash or ''}|{payload_json}".encode()
        event_hash = hashlib.sha256(chain_material).hexdigest()
        connection.execute(
            """
            INSERT INTO audit_events (
                event_id, idempotency_key, request_digest, aggregate_type,
                aggregate_id, aggregate_version, payload_json, previous_hash,
                event_hash, created_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                event.event_id,
                event.idempotency_key,
                self._request_digest(event),
                event.aggregate_type,
                event.aggregate_id,
                event.aggregate_version,
                payload_json,
                previous_hash,
                event_hash,
                event.timestamp.isoformat(),
            ),
        )
        return event_hash

    def enqueue_outbox(
        self, event: AuditEvent, aggregate_type: str, aggregate_id: str
    ) -> str:
        """Append the event and enqueue an outbox row in the same transaction."""
        outbox_id = str(uuid.uuid4())
        with self._connection() as connection:
            self._append_event(connection, event)
            connection.execute(
                """
                INSERT INTO outbox (outbox_id, aggregate_type, aggregate_id,
                    event_id, payload_json, created_at)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (
                    outbox_id,
                    aggregate_type,
                    aggregate_id,
                    event.event_id,
                    event.model_dump_json(),
                    event.timestamp.isoformat(),
                ),
            )
        return outbox_id

    def drain_outbox(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT outbox_id, aggregate_type, aggregate_id, event_id,
                       payload_json, created_at
                FROM outbox
                WHERE status = 'PENDING'
                ORDER BY created_at
                LIMIT %s
                FOR UPDATE SKIP LOCKED
                """,
                (limit,),
            ).fetchall()
            results: list[dict[str, Any]] = []
            for row in rows:
                dispatched_at = _utc_now_iso()
                connection.execute(
                    """
                    UPDATE outbox SET status = 'DISPATCHED', dispatched_at = %s
                    WHERE outbox_id = %s
                    """,
                    (dispatched_at, row["outbox_id"]),
                )
                results.append(
                    {
                        "outbox_id": row["outbox_id"],
                        "aggregate_type": row["aggregate_type"],
                        "aggregate_id": row["aggregate_id"],
                        "event_id": row["event_id"],
                        "payload_json": row["payload_json"],
                        "created_at": row["created_at"],
                        "status": "DISPATCHED",
                        "dispatched_at": dispatched_at,
                    }
                )
        return results

    def mark_outbox_failed(self, outbox_id: str, error: str) -> None:
        """Record a delivery failure: status -> FAILED, increment attempts,
        store the error message."""
        with self._connection() as connection:
            connection.execute(
                """UPDATE outbox SET status = 'FAILED', last_error = %s,
                    attempts = attempts + 1 WHERE outbox_id = %s""",
                (error[:4000], outbox_id),
            )

    def requeue_outbox(self, outbox_id: str) -> None:
        """Requeue a FAILED outbox row back to PENDING for another attempt."""
        with self._connection() as connection:
            connection.execute(
                "UPDATE outbox SET status = 'PENDING', dispatched_at = NULL WHERE outbox_id = %s",
                (outbox_id,),
            )

    def get_outbox_by_id(self, outbox_id: str) -> tuple[dict[str, Any], ...]:
        """Return outbox rows for a single outbox_id (usually zero or one)."""
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT outbox_id, aggregate_type, aggregate_id, event_id,
                       payload_json, status, attempts, last_error, created_at, dispatched_at
                FROM outbox
                WHERE outbox_id = %s
                """,
                (outbox_id,),
            ).fetchall()
        return tuple(dict(row) for row in rows)

    def get_outbox(self, aggregate_id: str) -> tuple[dict[str, Any], ...]:
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT outbox_id, aggregate_type, aggregate_id, event_id,
                       payload_json, status, attempts, last_error, created_at, dispatched_at
                FROM outbox
                WHERE aggregate_id = %s
                ORDER BY created_at
                """,
                (aggregate_id,),
            ).fetchall()
        return tuple(dict(row) for row in rows)

    def create_program(self, program: ProgramRecord, event: AuditEvent) -> ProgramRecord:
        _assert_f0_route_invariant(program)
        with self._connection() as connection:
            if self._idempotent_replay(connection, event):
                return self.get_program(program.program_id)
            try:
                connection.execute(
                    "INSERT INTO programs VALUES (%s, %s, %s, %s)",
                    (program.program_id, program.state_version, program.model_dump_json(), program.updated_at.isoformat()),
                )
                connection.execute(
                    "INSERT INTO program_versions VALUES (%s, %s, %s, %s)",
                    (program.program_id, program.state_version, program.model_dump_json(), program.updated_at.isoformat()),
                )
            except psycopg.errors.UniqueViolation as exc:
                raise StateConflict("program already exists", program_id=program.program_id) from exc
            self._append_event(connection, event)
        return program

    def get_program(self, program_id: str) -> ProgramRecord:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT payload_json FROM programs WHERE program_id = %s", (program_id,)
            ).fetchone()
        if row is None:
            raise NotFound("program not found", program_id=program_id)
        return ProgramRecord.model_validate_json(row["payload_json"])

    def save_program(
        self, program: ProgramRecord, expected_version: int, event: AuditEvent
    ) -> ProgramRecord:
        _assert_f0_route_invariant(program)
        with self._connection() as connection:
            if self._idempotent_replay(connection, event):
                return self.get_program(program.program_id)
            row = connection.execute(
                "SELECT state_version, payload_json FROM programs WHERE program_id = %s FOR UPDATE",
                (program.program_id,),
            ).fetchone()
            if row is None:
                raise NotFound("program not found", program_id=program.program_id)
            _assert_single_commit_path(
                ProgramRecord.model_validate_json(row["payload_json"]),
                program,
            )
            if row["state_version"] != expected_version or program.state_version != expected_version + 1:
                raise StateConflict(
                    "program changed after it was read",
                    expected_version=expected_version,
                    actual_version=row["state_version"],
                )
            connection.execute(
                "UPDATE programs SET state_version = %s, payload_json = %s, updated_at = %s WHERE program_id = %s",
                (program.state_version, program.model_dump_json(), program.updated_at.isoformat(), program.program_id),
            )
            connection.execute(
                "INSERT INTO program_versions VALUES (%s, %s, %s, %s)",
                (program.program_id, program.state_version, program.model_dump_json(), program.updated_at.isoformat()),
            )
            self._append_event(connection, event)
        return program

    def create_session(
        self, session: CouncilSession, expected_program_version: int, event: AuditEvent
    ) -> CouncilSession:
        with self._connection() as connection:
            if self._idempotent_replay(connection, event):
                return self.get_session(session.session_id)
            program = connection.execute(
                "SELECT state_version FROM programs WHERE program_id = %s FOR SHARE",
                (session.program_id,),
            ).fetchone()
            if program is None:
                raise NotFound("program not found", program_id=session.program_id)
            if program["state_version"] != expected_program_version:
                raise StateConflict(
                    "Program changed while the council session was being constituted",
                    expected_version=expected_program_version,
                    actual_version=program["state_version"],
                )
            try:
                connection.execute(
                    "INSERT INTO council_sessions VALUES (%s, %s, %s, %s, %s)",
                    (
                        session.session_id,
                        session.program_id,
                        session.state_version,
                        session.model_dump_json(),
                        session.updated_at.isoformat(),
                    ),
                )
                connection.execute(
                    "INSERT INTO council_session_versions VALUES (%s, %s, %s, %s)",
                    (session.session_id, session.state_version, session.model_dump_json(), session.updated_at.isoformat()),
                )
            except psycopg.errors.UniqueViolation as exc:
                raise StateConflict("council session already exists", session_id=session.session_id) from exc
            self._append_event(connection, event)
        return session

    def get_session(self, session_id: str) -> CouncilSession:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT payload_json FROM council_sessions WHERE session_id = %s", (session_id,)
            ).fetchone()
        if row is None:
            raise NotFound("council session not found", session_id=session_id)
        session = CouncilSession.model_validate_json(row["payload_json"])
        return self._reconcile_dissents(session)

    def _reconcile_dissents(self, session: CouncilSession) -> CouncilSession:
        ledger_dissents = self.get_dissents(session.session_id)
        if not ledger_dissents and not session.dissent:
            return session
        ledger_by_id = {d.dissent_id: d for d in ledger_dissents}
        session_by_id = {d.dissent_id: d for d in session.dissent}
        if set(ledger_by_id) != set(session_by_id):
            raise StateConflict(
                "council session dissent state diverged from the immutable dissent ledger",
                session_id=session.session_id,
            )
        for dissent_id, record in ledger_by_id.items():
            live = session_by_id[dissent_id]
            if _dissent_digest(live) != _dissent_digest(record):
                raise StateConflict(
                    "accepted dissent was rewritten",
                    dissent_id=dissent_id,
                    session_id=session.session_id,
                )
            if live.model_dump(mode="json") != record.model_dump(mode="json"):
                raise StateConflict(
                    "accepted dissent was mutated",
                    dissent_id=dissent_id,
                    session_id=session.session_id,
                )
        return session

    def get_dissents(self, session_id: str) -> list[Dissent]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT payload_json FROM dissents WHERE session_id = %s ORDER BY created_at", (session_id,)
            ).fetchall()
        return [Dissent.model_validate_json(row["payload_json"]) for row in rows]

    def record_dissent(self, dissent: Dissent, session_id: str) -> None:
        with self._connection() as connection:
            try:
                connection.execute(
                    "INSERT INTO dissents (dissent_id, session_id, immutable_digest, payload_json, created_at) "
                    "VALUES (%s, %s, %s, %s, %s)",
                    (
                        dissent.dissent_id,
                        session_id,
                        _dissent_digest(dissent),
                        dissent.model_dump_json(),
                        dissent.submitted_at.isoformat(),
                    ),
                )
            except psycopg.errors.UniqueViolation as exc:
                raise StateConflict("dissent already recorded", dissent_id=dissent.dissent_id) from exc

    def save_session(
        self, session: CouncilSession, expected_version: int, event: AuditEvent, record_dissents: bool = False
    ) -> CouncilSession:
        with self._connection() as connection:
            if self._idempotent_replay(connection, event):
                return self.get_session(session.session_id)
            self._update_session(connection, session, expected_version)
            if record_dissents:
                self._append_dissents(connection, session)
            self._append_event(connection, event)
        return session

    def _append_dissents(self, connection: Connection, session: CouncilSession) -> None:
        existing = {
            row[0]
            for row in connection.execute(
                "SELECT dissent_id FROM dissents WHERE session_id = %s", (session.session_id,)
            ).fetchall()
        }
        for dissent in session.dissent:
            if dissent.dissent_id in existing:
                continue
            connection.execute(
                "INSERT INTO dissents (dissent_id, session_id, immutable_digest, payload_json, created_at) "
                "VALUES (%s, %s, %s, %s, %s)",
                (
                    dissent.dissent_id,
                    session.session_id,
                    _dissent_digest(dissent),
                    dissent.model_dump_json(),
                    dissent.submitted_at.isoformat(),
                ),
            )

    def _update_session(
        self, connection: Connection, session: CouncilSession, expected_version: int
    ) -> None:
        row = connection.execute(
            "SELECT state_version FROM council_sessions WHERE session_id = %s FOR UPDATE",
            (session.session_id,),
        ).fetchone()
        if row is None:
            raise NotFound("council session not found", session_id=session.session_id)
        if row["state_version"] != expected_version or session.state_version != expected_version + 1:
            raise StateConflict(
                "council session changed after it was read",
                expected_version=expected_version,
                actual_version=row["state_version"],
            )
        connection.execute(
            "UPDATE council_sessions SET state_version = %s, payload_json = %s, updated_at = %s WHERE session_id = %s",
            (session.state_version, session.model_dump_json(), session.updated_at.isoformat(), session.session_id),
        )
        connection.execute(
            "INSERT INTO council_session_versions VALUES (%s, %s, %s, %s)",
            (session.session_id, session.state_version, session.model_dump_json(), session.updated_at.isoformat()),
        )

    def record_approval(
        self,
        session: CouncilSession,
        approval: Approval,
        expected_session_version: int,
        event: AuditEvent,
    ) -> CouncilSession:
        with self._connection() as connection:
            if self._idempotent_replay(connection, event):
                return self.get_session(session.session_id)
            self._update_session(connection, session, expected_session_version)
            try:
                connection.execute(
                    "INSERT INTO approvals VALUES (%s, %s, %s, %s, %s, %s)",
                    (
                        approval.approval_id,
                        approval.request_id,
                        approval.session_id,
                        approval.program_id,
                        approval.model_dump_json(),
                        approval.decided_at.isoformat(),
                    ),
                )
            except psycopg.errors.UniqueViolation as exc:
                raise StateConflict("approval already exists", approval_id=approval.approval_id) from exc
            self._append_event(connection, event)
        return session

    def get_approvals(self, session_id: str) -> tuple[Approval, ...]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT payload_json FROM approvals WHERE session_id = %s ORDER BY created_at, approval_id",
                (session_id,),
            ).fetchall()
        return tuple(Approval.model_validate_json(row["payload_json"]) for row in rows)

    def commit_program_and_session(
        self,
        program: ProgramRecord,
        session: CouncilSession,
        expected_program_version: int,
        expected_session_version: int,
        program_event: AuditEvent,
        session_event: AuditEvent,
        decision: GateDecision,
    ) -> tuple[ProgramRecord, CouncilSession]:
        with self._connection() as connection:
            if self._idempotent_replay(connection, program_event):
                return self.get_program(program.program_id), self.get_session(session.session_id)
            if self._idempotent_replay(connection, session_event):
                raise StateConflict("partial decision replay detected", session_id=session.session_id)

            program_row = connection.execute(
                "SELECT state_version FROM programs WHERE program_id = %s FOR UPDATE", (program.program_id,)
            ).fetchone()
            if program_row is None:
                raise NotFound("program not found", program_id=program.program_id)
            if (
                program_row["state_version"] != expected_program_version
                or program.state_version != expected_program_version + 1
            ):
                raise StateConflict(
                    "program changed after review was frozen",
                    expected_version=expected_program_version,
                    actual_version=program_row["state_version"],
                )

            self._update_session(connection, session, expected_session_version)
            try:
                connection.execute(
                    "INSERT INTO gate_decisions VALUES (%s, %s, %s, %s, %s)",
                    (
                        decision.decision_id,
                        decision.program_id,
                        decision.session_id,
                        decision.model_dump_json(),
                        decision.committed_at.isoformat(),
                    ),
                )
            except psycopg.errors.UniqueViolation as exc:
                raise StateConflict("gate decision already exists", decision_id=decision.decision_id) from exc
            connection.execute(
                "UPDATE programs SET state_version = %s, payload_json = %s, updated_at = %s WHERE program_id = %s",
                (program.state_version, program.model_dump_json(), program.updated_at.isoformat(), program.program_id),
            )
            connection.execute(
                "INSERT INTO program_versions VALUES (%s, %s, %s, %s)",
                (program.program_id, program.state_version, program.model_dump_json(), program.updated_at.isoformat()),
            )
            self._append_event(connection, program_event)
            self._append_event(connection, session_event)
        return program, session

    def get_gate_decision(self, decision_id: str) -> GateDecision:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT payload_json FROM gate_decisions WHERE decision_id = %s", (decision_id,)
            ).fetchone()
        if row is None:
            raise NotFound("gate decision not found", decision_id=decision_id)
        return GateDecision.model_validate_json(row["payload_json"])

    def get_program_version(self, program_id: str, state_version: int) -> ProgramRecord:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT payload_json FROM program_versions WHERE program_id = %s AND state_version = %s",
                (program_id, state_version),
            ).fetchone()
        if row is None:
            raise NotFound("Program version not found", program_id=program_id, state_version=state_version)
        return ProgramRecord.model_validate_json(row["payload_json"])

    def get_session_version(self, session_id: str, state_version: int) -> CouncilSession:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT payload_json FROM council_session_versions WHERE session_id = %s AND state_version = %s",
                (session_id, state_version),
            ).fetchone()
        if row is None:
            raise NotFound("Council session version not found", session_id=session_id, state_version=state_version)
        return CouncilSession.model_validate_json(row["payload_json"])

    def list_events(self, aggregate_type: str, aggregate_id: str) -> tuple[AuditEvent, ...]:
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT payload_json FROM audit_events
                WHERE aggregate_type = %s AND aggregate_id = %s
                ORDER BY sequence
                """,
                (aggregate_type, aggregate_id),
            ).fetchall()
        return tuple(AuditEvent.model_validate_json(row["payload_json"]) for row in rows)

    def verify_audit_chain(self, aggregate_type: str, aggregate_id: str) -> bool:
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT payload_json, previous_hash, event_hash FROM audit_events
                WHERE aggregate_type = %s AND aggregate_id = %s
                ORDER BY sequence
                """,
                (aggregate_type, aggregate_id),
            ).fetchall()
        if not rows:
            return False
        previous_hash: str | None = None
        for row in rows:
            if row["previous_hash"] != previous_hash:
                return False
            expected = hashlib.sha256(f"{previous_hash or ''}|{row['payload_json']}".encode()).hexdigest()
            if row["event_hash"] != expected:
                return False
            previous_hash = row["event_hash"]
        return True


def _utc_now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()
