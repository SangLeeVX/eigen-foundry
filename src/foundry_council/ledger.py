from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from .errors import IdempotencyKeyReused, NotFound, StateConflict
from .models import (
    Approval,
    AuditEvent,
    CouncilSession,
    Dissent,
    GateDecision,
    ProgramRecord,
    ProgramStage,
    Route,
)


def _assert_f0_route_invariant(program: ProgramRecord) -> None:
    """Defense-in-depth guard at the Ledger persistence boundary.

    Independent of the model validator so that a persistence path which bypasses
    Pydantic model construction (e.g. a direct raw insert) cannot persist an F0
    Program carrying a preselected route."""
    if program.stage is ProgramStage.F0 and program.route is not Route.UNSELECTED:
        raise ValueError(
            "F0 Program route must remain UNSELECTED; route selection is a governed F5 action"
        )


def _dissent_canonical(dissent: Dissent) -> str:
    """Stable canonical serialization of an accepted dissent for immutable
    attribution. Independent of field insertion order and Pydantic dump shape,
    so the digest is identical across construction and the append-only ledger."""
    content = {
        "dissent_id": dissent.dissent_id,
        "agent_id": dissent.agent_id,
        "assignment_id": dissent.assignment_id,
        "role": dissent.role,
        "statement": dissent.statement,
        "materiality": dissent.materiality.value,
        "submitted_at": dissent.submitted_at.isoformat().replace("+00:00", "Z"),
    }
    return json.dumps(content, sort_keys=True, separators=(",", ":"))


def _dissent_digest(dissent: Dissent) -> str:
    return f"sha256:{hashlib.sha256(_dissent_canonical(dissent).encode()).hexdigest()}"


# Formal Program state (stage/status/route) may ONLY be changed by the governed
# commit path (commit_gate_decision -> ledger.commit_program_and_session). This
# is the M2-C4 "single restricted commit path" invariant: every other persistence
# entry point (draft create, policy-binding migration) must keep formal state
# unchanged.
FORMAL_PROGRAM_STATE_FIELDS = ("stage", "status", "route")


def _assert_single_commit_path(stored: ProgramRecord, proposed: ProgramRecord) -> None:
    """Reject any save that would mutate a Program's formal state outside the
    restricted commit path."""
    for field in FORMAL_PROGRAM_STATE_FIELDS:
        before = getattr(stored, field)
        after = getattr(proposed, field)
        if before != after:
            raise ValueError(
                f"formal Program state field '{field}' may only change through the "
                "governed commit path (commit_gate_decision); save_program is not "
                "authorized to change it"
            )


class SQLiteLedger:
    """Transactional MVP ledger with immutable approvals and a hash-chained audit log."""

    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        self.initialize()

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        try:
            yield connection
        finally:
            connection.close()

    def initialize(self) -> None:
        with self._connection() as connection:
            connection.executescript(
                """
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
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id TEXT NOT NULL UNIQUE,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    request_digest TEXT NOT NULL,
                    aggregate_type TEXT NOT NULL,
                    aggregate_id TEXT NOT NULL,
                    aggregate_version INTEGER NOT NULL,
                    payload_json TEXT NOT NULL,
                    previous_hash TEXT,
                    event_hash TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TRIGGER IF NOT EXISTS approvals_no_update
                BEFORE UPDATE ON approvals BEGIN
                    SELECT RAISE(ABORT, 'approvals are immutable');
                END;

                CREATE TRIGGER IF NOT EXISTS approvals_no_delete
                BEFORE DELETE ON approvals BEGIN
                    SELECT RAISE(ABORT, 'approvals are immutable');
                END;

                CREATE TRIGGER IF NOT EXISTS audit_events_no_update
                BEFORE UPDATE ON audit_events BEGIN
                    SELECT RAISE(ABORT, 'audit events are immutable');
                END;

                CREATE TRIGGER IF NOT EXISTS audit_events_no_delete
                BEFORE DELETE ON audit_events BEGIN
                    SELECT RAISE(ABORT, 'audit events are immutable');
                END;

                CREATE TRIGGER IF NOT EXISTS gate_decisions_no_update
                BEFORE UPDATE ON gate_decisions BEGIN
                    SELECT RAISE(ABORT, 'gate decisions are immutable');
                END;

                CREATE TRIGGER IF NOT EXISTS gate_decisions_no_delete
                BEFORE DELETE ON gate_decisions BEGIN
                    SELECT RAISE(ABORT, 'gate decisions are immutable');
                END;

                CREATE TRIGGER IF NOT EXISTS program_versions_no_update
                BEFORE UPDATE ON program_versions BEGIN
                    SELECT RAISE(ABORT, 'Program versions are immutable');
                END;

                CREATE TRIGGER IF NOT EXISTS program_versions_no_delete
                BEFORE DELETE ON program_versions BEGIN
                    SELECT RAISE(ABORT, 'Program versions are immutable');
                END;

                CREATE TRIGGER IF NOT EXISTS council_session_versions_no_update
                BEFORE UPDATE ON council_session_versions BEGIN
                    SELECT RAISE(ABORT, 'Council session versions are immutable');
                END;

                CREATE TRIGGER IF NOT EXISTS council_session_versions_no_delete
                BEFORE DELETE ON council_session_versions BEGIN
                    SELECT RAISE(ABORT, 'Council session versions are immutable');
                END;
                """
            )

    @staticmethod
    def _request_digest(event: AuditEvent) -> str:
        value = {
            "idempotency_key": event.idempotency_key,
            "aggregate_type": event.aggregate_type,
            "aggregate_id": event.aggregate_id,
            "actor_id": event.actor_id,
            "actor_kind": event.actor_kind,
            "action": event.action,
            "reason": event.reason,
            "payload": event.payload,
        }
        encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
        return hashlib.sha256(encoded).hexdigest()

    def _idempotent_replay(self, connection: sqlite3.Connection, event: AuditEvent) -> bool:
        row = connection.execute(
            "SELECT request_digest FROM audit_events WHERE idempotency_key = ?",
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
        """Check a command before phase validation, preserving replay semantics."""
        with self._connection() as connection:
            return self._idempotent_replay(connection, event)

    def _append_event(self, connection: sqlite3.Connection, event: AuditEvent) -> None:
        previous = connection.execute(
            """
            SELECT event_hash FROM audit_events
            WHERE aggregate_type = ? AND aggregate_id = ?
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
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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

    def create_program(self, program: ProgramRecord, event: AuditEvent) -> ProgramRecord:
        _assert_f0_route_invariant(program)
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if self._idempotent_replay(connection, event):
                connection.rollback()
                return self.get_program(program.program_id)
            try:
                connection.execute(
                    "INSERT INTO programs VALUES (?, ?, ?, ?)",
                    (program.program_id, program.state_version, program.model_dump_json(), program.updated_at.isoformat()),
                )
                connection.execute(
                    "INSERT INTO program_versions VALUES (?, ?, ?, ?)",
                    (program.program_id, program.state_version, program.model_dump_json(), program.updated_at.isoformat()),
                )
            except sqlite3.IntegrityError as exc:
                connection.rollback()
                raise StateConflict("program already exists", program_id=program.program_id) from exc
            self._append_event(connection, event)
            connection.commit()
        return program

    def get_program(self, program_id: str) -> ProgramRecord:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT payload_json FROM programs WHERE program_id = ?", (program_id,)
            ).fetchone()
        if row is None:
            raise NotFound("program not found", program_id=program_id)
        return ProgramRecord.model_validate_json(row["payload_json"])

    def save_program(
        self,
        program: ProgramRecord,
        expected_version: int,
        event: AuditEvent,
    ) -> ProgramRecord:
        _assert_f0_route_invariant(program)
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if self._idempotent_replay(connection, event):
                connection.rollback()
                return self.get_program(program.program_id)
            row = connection.execute(
                "SELECT state_version, payload_json FROM programs WHERE program_id = ?", (program.program_id,)
            ).fetchone()
            if row is None:
                connection.rollback()
                raise NotFound("program not found", program_id=program.program_id)
            _assert_single_commit_path(
                ProgramRecord.model_validate_json(row["payload_json"]),
                program,
            )
            if row["state_version"] != expected_version or program.state_version != expected_version + 1:
                connection.rollback()
                raise StateConflict(
                    "program changed after it was read",
                    expected_version=expected_version,
                    actual_version=row["state_version"],
                )
            connection.execute(
                "UPDATE programs SET state_version = ?, payload_json = ?, updated_at = ? WHERE program_id = ?",
                (program.state_version, program.model_dump_json(), program.updated_at.isoformat(), program.program_id),
            )
            connection.execute(
                "INSERT INTO program_versions VALUES (?, ?, ?, ?)",
                (program.program_id, program.state_version, program.model_dump_json(), program.updated_at.isoformat()),
            )
            self._append_event(connection, event)
            connection.commit()
        return program

    def create_session(
        self, session: CouncilSession, expected_program_version: int, event: AuditEvent
    ) -> CouncilSession:
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if self._idempotent_replay(connection, event):
                connection.rollback()
                return self.get_session(session.session_id)
            program = connection.execute(
                "SELECT state_version FROM programs WHERE program_id = ?", (session.program_id,)
            ).fetchone()
            if program is None:
                connection.rollback()
                raise NotFound("program not found", program_id=session.program_id)
            if program["state_version"] != expected_program_version:
                connection.rollback()
                raise StateConflict(
                    "Program changed while the council session was being constituted",
                    expected_version=expected_program_version,
                    actual_version=program["state_version"],
                )
            try:
                connection.execute(
                    "INSERT INTO council_sessions VALUES (?, ?, ?, ?, ?)",
                    (
                        session.session_id,
                        session.program_id,
                        session.state_version,
                        session.model_dump_json(),
                        session.updated_at.isoformat(),
                    ),
                )
                connection.execute(
                    "INSERT INTO council_session_versions VALUES (?, ?, ?, ?)",
                    (session.session_id, session.state_version, session.model_dump_json(), session.updated_at.isoformat()),
                )
            except sqlite3.IntegrityError as exc:
                connection.rollback()
                raise StateConflict("council session already exists", session_id=session.session_id) from exc
            self._append_event(connection, event)
            connection.commit()
        return session

    def get_session(self, session_id: str) -> CouncilSession:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT payload_json FROM council_sessions WHERE session_id = ?", (session_id,)
            ).fetchone()
        if row is None:
            raise NotFound("council session not found", session_id=session_id)
        session = CouncilSession.model_validate_json(row["payload_json"])
        return self._reconcile_dissents(session)

    def _reconcile_dissents(self, session: CouncilSession) -> CouncilSession:
        """Reconcile session dissent state from the immutable append-only dissent
        ledger. Any attempted removal or rewrite of accepted dissent is detected
        from the immutable digests and fails closed."""
        ledger_dissents = self.get_dissents(session.session_id)
        if not ledger_dissents and not session.dissent:
            return session
        # The live aggregate must reconcile exactly to the immutable ledger.
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
                "SELECT payload_json FROM dissents WHERE session_id = ? ORDER BY created_at", (session_id,)
            ).fetchall()
        return [Dissent.model_validate_json(row["payload_json"]) for row in rows]

    def _append_dissents(
        self, connection: sqlite3.Connection, session: CouncilSession
    ) -> None:
        """Append-only persist of any accepted dissents missing from the immutable
        ledger, in the same transaction as the session update."""
        existing = {
            row["dissent_id"]
            for row in connection.execute(
                "SELECT dissent_id FROM dissents WHERE session_id = ?", (session.session_id,)
            ).fetchall()
        }
        for dissent in session.dissent:
            if dissent.dissent_id in existing:
                continue
            connection.execute(
                "INSERT INTO dissents (dissent_id, session_id, immutable_digest, payload_json, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    dissent.dissent_id,
                    session.session_id,
                    _dissent_digest(dissent),
                    dissent.model_dump_json(),
                    dissent.submitted_at.isoformat(),
                ),
            )

    def record_dissent(self, dissent: Dissent, session_id: str) -> None:
        """Append-only persist of an accepted dissent into the immutable ledger.
        Refuses to overwrite or remove an existing record."""
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.execute(
                    "INSERT INTO dissents (dissent_id, session_id, immutable_digest, payload_json, created_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (
                        dissent.dissent_id,
                        session_id,
                        _dissent_digest(dissent),
                        dissent.model_dump_json(),
                        dissent.submitted_at.isoformat(),
                    ),
                )
            except sqlite3.IntegrityError as exc:
                connection.rollback()
                raise StateConflict("dissent already recorded", dissent_id=dissent.dissent_id) from exc
            connection.commit()

    def save_session(
        self,
        session: CouncilSession,
        expected_version: int,
        event: AuditEvent,
        record_dissents: bool = False,
    ) -> CouncilSession:
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if self._idempotent_replay(connection, event):
                connection.rollback()
                return self.get_session(session.session_id)
            self._update_session(connection, session, expected_version)
            if record_dissents:
                self._append_dissents(connection, session)
            self._append_event(connection, event)
            connection.commit()
        return session

    def _update_session(
        self, connection: sqlite3.Connection, session: CouncilSession, expected_version: int
    ) -> None:
        row = connection.execute(
            "SELECT state_version FROM council_sessions WHERE session_id = ?", (session.session_id,)
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
            """
            UPDATE council_sessions
            SET state_version = ?, payload_json = ?, updated_at = ?
            WHERE session_id = ?
            """,
            (session.state_version, session.model_dump_json(), session.updated_at.isoformat(), session.session_id),
        )
        connection.execute(
            "INSERT INTO council_session_versions VALUES (?, ?, ?, ?)",
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
            connection.execute("BEGIN IMMEDIATE")
            if self._idempotent_replay(connection, event):
                connection.rollback()
                return self.get_session(session.session_id)
            self._update_session(connection, session, expected_session_version)
            try:
                connection.execute(
                    "INSERT INTO approvals VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        approval.approval_id,
                        approval.request_id,
                        approval.session_id,
                        approval.program_id,
                        approval.model_dump_json(),
                        approval.decided_at.isoformat(),
                    ),
                )
            except sqlite3.IntegrityError as exc:
                connection.rollback()
                raise StateConflict("approval already exists", approval_id=approval.approval_id) from exc
            self._append_event(connection, event)
            connection.commit()
        return session

    def get_approvals(self, session_id: str) -> tuple[Approval, ...]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT payload_json FROM approvals WHERE session_id = ? ORDER BY created_at, approval_id",
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
            connection.execute("BEGIN IMMEDIATE")
            if self._idempotent_replay(connection, program_event):
                connection.rollback()
                return self.get_program(program.program_id), self.get_session(session.session_id)
            if self._idempotent_replay(connection, session_event):
                connection.rollback()
                raise StateConflict("partial decision replay detected", session_id=session.session_id)

            program_row = connection.execute(
                "SELECT state_version FROM programs WHERE program_id = ?", (program.program_id,)
            ).fetchone()
            if program_row is None:
                connection.rollback()
                raise NotFound("program not found", program_id=program.program_id)
            if (
                program_row["state_version"] != expected_program_version
                or program.state_version != expected_program_version + 1
            ):
                connection.rollback()
                raise StateConflict(
                    "program changed after review was frozen",
                    expected_version=expected_program_version,
                    actual_version=program_row["state_version"],
                )

            self._update_session(connection, session, expected_session_version)
            try:
                connection.execute(
                    "INSERT INTO gate_decisions VALUES (?, ?, ?, ?, ?)",
                    (
                        decision.decision_id,
                        decision.program_id,
                        decision.session_id,
                        decision.model_dump_json(),
                        decision.committed_at.isoformat(),
                    ),
                )
            except sqlite3.IntegrityError as exc:
                connection.rollback()
                raise StateConflict("gate decision already exists", decision_id=decision.decision_id) from exc
            connection.execute(
                "UPDATE programs SET state_version = ?, payload_json = ?, updated_at = ? WHERE program_id = ?",
                (program.state_version, program.model_dump_json(), program.updated_at.isoformat(), program.program_id),
            )
            connection.execute(
                "INSERT INTO program_versions VALUES (?, ?, ?, ?)",
                (program.program_id, program.state_version, program.model_dump_json(), program.updated_at.isoformat()),
            )
            self._append_event(connection, program_event)
            self._append_event(connection, session_event)
            connection.commit()
        return program, session

    def get_gate_decision(self, decision_id: str) -> GateDecision:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT payload_json FROM gate_decisions WHERE decision_id = ?", (decision_id,)
            ).fetchone()
        if row is None:
            raise NotFound("gate decision not found", decision_id=decision_id)
        return GateDecision.model_validate_json(row["payload_json"])

    def get_program_version(self, program_id: str, state_version: int) -> ProgramRecord:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT payload_json FROM program_versions WHERE program_id = ? AND state_version = ?",
                (program_id, state_version),
            ).fetchone()
        if row is None:
            raise NotFound("Program version not found", program_id=program_id, state_version=state_version)
        return ProgramRecord.model_validate_json(row["payload_json"])

    def get_session_version(self, session_id: str, state_version: int) -> CouncilSession:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT payload_json FROM council_session_versions WHERE session_id = ? AND state_version = ?",
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
                WHERE aggregate_type = ? AND aggregate_id = ?
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
                WHERE aggregate_type = ? AND aggregate_id = ?
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
