from __future__ import annotations

import unittest
import uuid

from foundry_council.errors import IdempotencyKeyReused, NotFound, StateConflict
from foundry_council.models import (
    Approval,
    AuditEvent,
    CouncilSession,
    Dissent,
    GateDecision,
    ProgramRecord,
    Disposition,
    ProgramStage,
    Route,
)
from foundry_council.postgres_ledger import PostgresLedger

from tests.helpers import pg_test_dsn, skip_unless_postgres


def _new_schema() -> str:
    import psycopg

    schema = f"test_{uuid.uuid4().hex[:12]}"
    conn = psycopg.connect(pg_test_dsn(), autocommit=True)
    conn.execute(f'CREATE SCHEMA "{schema}"')
    conn.close()
    return schema


def _schema_dsn(schema: str) -> str:
    # Set search_path so all created objects land in the private schema.
    return pg_test_dsn(schema=schema)


def _mk_program(program_id: str, title: str = "Test Program", version: int = 1) -> ProgramRecord:
    return ProgramRecord(
        program_id=program_id,
        title=title,
        conversation_key=f"conv_{program_id}",
        state_version=version,
    )


def _mk_event(
    aggregate_type: str,
    aggregate_id: str,
    aggregate_version: int,
    action: str = "test_action",
    key: str | None = None,
) -> AuditEvent:
    return AuditEvent(
        event_id=f"evt_{uuid.uuid4().hex}",
        idempotency_key=key or f"key_{uuid.uuid4().hex}",
        aggregate_type=aggregate_type,
        aggregate_id=aggregate_id,
        aggregate_version=aggregate_version,
        actor_id="agent_test",
        actor_kind="AGENT",
        action=action,
        reason="test event",
    )


@skip_unless_postgres()
class _PostgresSchemaCase(unittest.TestCase):
    """Base: give each test class a private schema, dropped on teardown."""

    _schema: str = ""
    _dsn: str = ""

    @classmethod
    def setUpClass(cls) -> None:
        cls._schema = _new_schema()
        cls._dsn = _schema_dsn(cls._schema)
        cls.ledger = PostgresLedger(cls._dsn)

    @classmethod
    def tearDownClass(cls) -> None:
        import psycopg

        conn = psycopg.connect(pg_test_dsn(), autocommit=True)
        conn.execute(f'DROP SCHEMA IF EXISTS "{cls._schema}" CASCADE')
        conn.close()


class TestPrograms(_PostgresSchemaCase):
    def test_create_program_and_immutable_versions(self) -> None:
        program = _mk_program("prog_a")
        event = _mk_event("PROGRAM", program.program_id, program.state_version, action="PROGRAM_CREATE")
        self.ledger.create_program(program, event)

        got = self.ledger.get_program("prog_a")
        self.assertEqual(got.program_id, "prog_a")
        self.assertEqual(got.state_version, 1)

        saved = self.ledger.get_program_version("prog_a", 1)
        self.assertEqual(saved.state_version, 1)

        # version belongs to a different program -> cannot read
        with self.assertRaises(NotFound):
            self.ledger.get_program_version("prog_none", 1)

    def test_f0_route_invariant_guard(self) -> None:
        # Straight construction enforces it too, but construct then mutate via
        # model_copy to simulate a persistence boundary bypass attempt.
        base = _mk_program("prog_route")
        event = _mk_event("PROGRAM", base.program_id, base.state_version)
        good = base.model_copy()
        self.ledger.create_program(good, event)

        bad = good.model_copy(update={"stage": ProgramStage.F0, "route": Route.EXISTING_ASSET})
        with self.assertRaises(ValueError):
            self.ledger.save_program(bad, expected_version=1, event=_mk_event("PROGRAM", bad.program_id, 2))


class TestConcurrency(_PostgresSchemaCase):
    def test_stale_expected_version_raises_conflict(self) -> None:
        program = _mk_program("prog_conflict")
        self.ledger.create_program(
            program,
            _mk_event("PROGRAM", program.program_id, program.state_version, action="PROGRAM_CREATE"),
        )

        v2 = program.model_copy(update={"state_version": 2, "title": "v2"})
        self.ledger.save_program(v2, expected_version=1, event=_mk_event("PROGRAM", v2.program_id, 2))

        # reader saw version 1 -> stale expected_version now conflicts
        stale = program.model_copy(update={"state_version": 2, "title": "stale"})
        with self.assertRaises(StateConflict):
            self.ledger.save_program(
                stale, expected_version=1, event=_mk_event("PROGRAM", stale.program_id, 2)
            )

    def test_two_writer_sequential_conflict(self) -> None:
        program = _mk_program("prog_two_writer")
        self.ledger.create_program(
            program,
            _mk_event("PROGRAM", program.program_id, program.state_version, action="PROGRAM_CREATE"),
        )

        writer_a = program.model_copy(update={"state_version": 2, "title": "writer A"})
        writer_b = program.model_copy(update={"state_version": 2, "title": "writer B"})

        self.ledger.save_program(
            writer_a, expected_version=1, event=_mk_event("PROGRAM", writer_a.program_id, 2)
        )
        # writer B still holds expected_version=1 -> conflict
        with self.assertRaises(StateConflict):
            self.ledger.save_program(
                writer_b, expected_version=1, event=_mk_event("PROGRAM", writer_b.program_id, 2)
            )


class TestAuditChain(_PostgresSchemaCase):
    def test_hash_chain_verify(self) -> None:
        program = _mk_program("prog_chain")
        e1 = _mk_event("PROGRAM", program.program_id, 1, action="PROGRAM_CREATE")
        self.ledger.create_program(program, e1)

        v2 = program.model_copy(update={"state_version": 2, "title": "v2"})
        e2 = _mk_event("PROGRAM", v2.program_id, 2, action="PROGRAM_UPDATE")
        self.ledger.save_program(v2, expected_version=1, event=e2)

        self.assertTrue(self.ledger.verify_audit_chain("PROGRAM", program.program_id))
        events = self.ledger.list_events("PROGRAM", program.program_id)
        self.assertEqual(len(events), 2)
        self.assertEqual(events[0].action, "PROGRAM_CREATE")
        self.assertEqual(events[1].action, "PROGRAM_UPDATE")


class TestIdempotency(_PostgresSchemaCase):
    def test_idempotent_replay_returns_existing(self) -> None:
        program = _mk_program("prog_idem")
        program_id = program.program_id
        key = f"idem_{uuid.uuid4().hex}"
        e1 = _mk_event("PROGRAM", program_id, 1, action="PROGRAM_CREATE", key=key)
        self.ledger.create_program(program, e1)

        replay = _mk_event("PROGRAM", program_id, 1, action="PROGRAM_CREATE", key=key)
        repl = _mk_program(program_id)
        returned = self.ledger.create_program(repl, replay)
        self.assertEqual(returned.program_id, program_id)
        # Only one event appended
        self.assertEqual(len(self.ledger.list_events("PROGRAM", program_id)), 1)

    def test_idempotent_replay_changed_body_raises(self) -> None:
        program = _mk_program("prog_idem_bad")
        key = f"idem_{uuid.uuid4().hex}"
        self.ledger.create_program(
            program, _mk_event("PROGRAM", program.program_id, 1, action="PROGRAM_CREATE", key=key)
        )
        # Same key, different action -> different request digest
        changed = _mk_event("PROGRAM", program.program_id, 1, action="PROGRAM_UPDATE", key=key)
        with self.assertRaises(IdempotencyKeyReused):
            self.ledger.create_program(_mk_program(program.program_id), changed)


class TestTransactionalOutbox(_PostgresSchemaCase):
    def test_enqueue_and_drain(self) -> None:
        program = _mk_program("prog_outbox")
        self.ledger.create_program(
            program, _mk_event("PROGRAM", program.program_id, 1, action="PROGRAM_CREATE")
        )
        event = _mk_event("PROGRAM", program.program_id, 2, action="PROGRAM_UPDATE")

        outbox_id = self.ledger.enqueue_outbox(event, "PROGRAM", program.program_id)
        self.assertTrue(outbox_id)

        # Event was appended transactionally with the outbox row
        self.assertEqual(len(self.ledger.list_events("PROGRAM", program.program_id)), 2)

        pending = self.ledger.get_outbox(program.program_id)
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["status"], "PENDING")

        drained = self.ledger.drain_outbox(limit=10)
        self.assertEqual(len(drained), 1)
        self.assertEqual(drained[0]["outbox_id"], outbox_id)
        self.assertEqual(drained[0]["status"], "DISPATCHED")

        # payload preserved
        import json

        payload = json.loads(drained[0]["payload_json"])
        self.assertEqual(payload["event_id"], event.event_id)

        after = self.ledger.get_outbox(program.program_id)
        self.assertEqual(after[0]["status"], "DISPATCHED")
        self.assertIsNotNone(after[0]["dispatched_at"])


class TestApprovalsAndDissent(_PostgresSchemaCase):
    def test_record_dissent_immutable(self) -> None:
        from foundry_council.models import (
            DecisionCharter,
            Disposition,
            ProgramPointers,
            ProgramStage,
            SnapshotRef,
        )

        def _sha(label: str) -> str:
            import hashlib

            return f"sha256:{hashlib.sha256(label.encode()).hexdigest()}"

        def _snap(oid: str) -> SnapshotRef:
            return SnapshotRef(object_id=oid, version=1, digest=_sha(oid))

        program = _mk_program("prog_dissent")
        self.ledger.create_program(
            program, _mk_event("PROGRAM", program.program_id, 1, action="PROGRAM_CREATE")
        )

        pointers = ProgramPointers(
            portfolio_mandate=_snap("pm-v0"),
            tpp=_snap("tpp-v0"),
            rights_snapshot=_snap("rights-v0"),
            budget=_snap("budget-v0"),
            risk_register=_snap("risk-v0"),
            standard_of_care=_snap("soc-v0"),
        )
        program_ref = _snap(program.program_id)

        from datetime import datetime, timedelta, timezone

        charter = DecisionCharter(
            question="Should this controlled F0 mandate be authorized for F1 mapping?",
            proposed_action="Advance the controlled test Program from F0 to F1.",
            exact_scope="Authorize F1 mapping only.",
            requested_disposition=Disposition.ADVANCE,
            current_stage=ProgramStage.F0,
            proposed_stage=ProgramStage.F1,
            expected_program_state_version=1,
            program_snapshot=program_ref,
            portfolio_mandate=pointers.portfolio_mandate,
            tpp=pointers.tpp,
            rights=pointers.rights_snapshot,
            budget=pointers.budget,
            risk_register=pointers.risk_register,
            standard_of_care=pointers.standard_of_care,
            gate_policy=_snap("gatepolicy-v0"),
            session_deadline=datetime.now(timezone.utc) + timedelta(days=1),
        )
        session = CouncilSession(
            session_id="sess_dissent",
            program_id=program.program_id,
            charter=charter,
            participants=(),
        )
        self.ledger.create_session(
            session,
            expected_program_version=1,
            event=_mk_event("COUNCIL_SESSION", session.session_id, 1, action="SESSION_CREATE"),
        )

        dissent = Dissent(
            dissent_id="diss_q1",
            agent_id="agent_9",
            assignment_id="assign_9",
            role="reviewer",
            statement="Material concern with the kill criteria.",
            materiality="MATERIAL",
        )
        self.ledger.record_dissent(dissent, session.session_id)

        got = self.ledger.get_dissents(session.session_id)
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0].dissent_id, "diss_q1")

        with self.assertRaises(StateConflict):
            self.ledger.record_dissent(dissent, session.session_id)


if __name__ == "__main__":
    unittest.main()
