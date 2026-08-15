from __future__ import annotations

import tempfile
import unittest
import uuid

from foundry_council.models import AuditEvent, ProgramRecord
from foundry_council.outbox_dispatcher import OutboxDispatcher
from foundry_council.postgres_ledger import PostgresLedger

from tests.helpers import pg_test_dsn, skip_unless_postgres


def _new_schema() -> str:
    import psycopg

    schema = f"test_{uuid.uuid4().hex[:12]}"
    conn = psycopg.connect(pg_test_dsn(), autocommit=True)
    conn.execute(f'CREATE SCHEMA "{schema}"')
    conn.close()
    return schema


@skip_unless_postgres()
class _DispatcherCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        pass

    def setUp(self) -> None:
        self._schema = _new_schema()
        self._pg_dsn = pg_test_dsn(schema=self._schema)
        self.ledger = PostgresLedger(self._pg_dsn)

    def tearDown(self) -> None:
        import psycopg

        conn = psycopg.connect(pg_test_dsn(), autocommit=True)
        conn.execute(f'DROP SCHEMA IF EXISTS "{self._schema}" CASCADE')
        conn.close()

    def _seed_event(self, program_id: str = "PG_DISPATCH", action: str = "GO_AHEAD") -> AuditEvent:
        program = ProgramRecord(
            program_id=program_id,
            title="Dispatcher Test",
            conversation_key=f"conv_{program_id}",
            state_version=1,
        )
        self.ledger.create_program(
            program,
            AuditEvent(
                event_id=f"evt_{uuid.uuid4().hex}",
                idempotency_key=f"key_{uuid.uuid4().hex}",
                aggregate_type="PROGRAM",
                aggregate_id=program_id,
                aggregate_version=1,
                actor_id="actor_a",
                actor_kind="AGENT",
                action=action,
                reason="dispatcher test",
                payload={"note": "seed"},
            ),
        )
        return AuditEvent(
            event_id=f"evt_{uuid.uuid4().hex}",
            idempotency_key=f"key_{uuid.uuid4().hex}",
            aggregate_type="PROGRAM",
            aggregate_id=program_id,
            aggregate_version=2,
            actor_id="actor_a",
            actor_kind="AGENT",
            action=action,
            reason="outbox delivery",
            payload={"note": "dispatch me"},
        )


class TestDispatchSuccess(_DispatcherCase):
    def test_delivers_and_marks_dispatched(self) -> None:
        event = self._seed_event()
        outbox_id = self.ledger.enqueue_outbox(event, "PROGRAM", event.aggregate_id)

        received: list[dict] = []

        def handler(envelope: dict) -> None:
            received.append(envelope)

        dispatcher = OutboxDispatcher(self.ledger, handler)
        n = dispatcher.dispatch_once()

        self.assertEqual(n, 1)
        self.assertEqual(len(received), 1)
        self.assertEqual(received[0]["outbox_id"], outbox_id)
        self.assertEqual(received[0]["event_id"], event.event_id)
        self.assertEqual(received[0]["aggregate_id"], event.aggregate_id)
        # payload_json is the full serialized event; the dispatcher parses it as the
        # envelope payload, so the event's own payload field is nested inside.
        self.assertEqual(received[0]["payload"]["event_id"], event.event_id)
        self.assertEqual(received[0]["payload"]["payload"], {"note": "dispatch me"})

        rows = self.ledger.get_outbox(event.aggregate_id)
        self.assertEqual(rows[0]["status"], "DISPATCHED")
        self.assertIsNotNone(rows[0]["dispatched_at"])

    def test_at_most_once_no_redispatch(self) -> None:
        event = self._seed_event()
        self.ledger.enqueue_outbox(event, "PROGRAM", event.aggregate_id)
        received: list = []

        def handler(envelope: dict) -> None:
            received.append(envelope)

        dispatcher = OutboxDispatcher(self.ledger, handler)
        self.assertEqual(dispatcher.dispatch_once(), 1)
        self.assertEqual(dispatcher.dispatch_once(), 0)  # already DISPATCHED
        self.assertEqual(len(received), 1)


class TestDispatchFailure(_DispatcherCase):
    def test_failure_marks_failed_and_requeues_for_retry(self) -> None:
        event = self._seed_event()
        self.ledger.enqueue_outbox(event, "PROGRAM", event.aggregate_id)

        def failing_handler(envelope: dict) -> None:
            raise RuntimeError("downstream unavailable")

        dispatcher = OutboxDispatcher(self.ledger, failing_handler, max_attempts=3)
        n = dispatcher.dispatch_once()
        self.assertEqual(n, 0)  # not counted as delivered

        # After first failure: FAILED, attempts=1, requeued to PENDING for retry
        rows = self.ledger.get_outbox(event.aggregate_id)
        row = rows[0]
        self.assertEqual(row["status"], "PENDING")
        self.assertEqual(row["attempts"], 1)
        self.assertIn("unavailable", row["last_error"])

    def test_exhausted_attempts_stays_failed(self) -> None:
        event = self._seed_event()
        outbox_id = self.ledger.enqueue_outbox(event, "PROGRAM", event.aggregate_id)

        def failing_handler(envelope: dict) -> None:
            raise RuntimeError("transient")

        # max_attempts=1 -> after first failure it stays FAILED (no requeue)
        dispatcher = OutboxDispatcher(self.ledger, failing_handler, max_attempts=1)
        # Run enough rounds to exhaust attempts
        for _ in range(3):
            dispatcher.dispatch_once()

        row = self.ledger.get_outbox_by_id(outbox_id)[0]
        self.assertEqual(row["status"], "FAILED")
        self.assertEqual(row["attempts"], 1)


class TestPollLoop(_DispatcherCase):
    def test_poll_dispatches_until_controller_stops(self) -> None:
        events = [self._seed_event(program_id=f"PG_{i}") for i in range(3)]
        for e in events:
            self.ledger.enqueue_outbox(e, "PROGRAM", e.aggregate_id)

        received: list[str] = []
        rounds = [0]

        def handler(envelope: dict) -> None:
            received.append(envelope["event_id"])

        class OnceController:
            def should_continue(self) -> bool:
                rounds[0] += 1
                return rounds[0] < 1  # single round

        dispatcher = OutboxDispatcher(self.ledger, handler)
        total = dispatcher.poll(interval_seconds=0, controller=OnceController())

        self.assertEqual(total, 3)
        self.assertEqual(len(received), 3)


if __name__ == "__main__":
    unittest.main()
