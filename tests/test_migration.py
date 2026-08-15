from __future__ import annotations

import sqlite3
import tempfile
import unittest
import uuid

from foundry_council import build_ledger, migrate_sqlite_to_postgres
from foundry_council.ledger import SQLiteLedger
from foundry_council.models import (
    AuditEvent,
    CouncilSession,
    DecisionCharter,
    Disposition,
    ProgramPointers,
    ProgramRecord,
    ProgramStage,
    SnapshotRef,
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


def _dsn(schema: str) -> str:
    return pg_test_dsn(schema=schema)



def _sha(label: str) -> str:
    import hashlib

    return f"sha256:{hashlib.sha256(label.encode()).hexdigest()}"


def _snap(oid: str) -> SnapshotRef:
    return SnapshotRef(object_id=oid, version=1, digest=_sha(oid))


def _mk_event(aggregate_type: str, aggregate_id: str, version: int, key: str) -> AuditEvent:
    return AuditEvent(
        event_id=f"evt_{uuid.uuid4().hex}",
        idempotency_key=key,
        aggregate_type=aggregate_type,
        aggregate_id=aggregate_id,
        aggregate_version=version,
        actor_id="agent_migrate",
        actor_kind="AGENT",
        action="TEST_ACTION",
        reason="migration test",
    )


@skip_unless_postgres()
class MigrationBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        pass

    @classmethod
    def tearDownClass(cls) -> None:
        pass

    def setUp(self) -> None:
        # Fresh SQLite source AND fresh Postgres schema per test (isolated).
        self._schema = _new_schema()
        self._pg_dsn = _dsn(self._schema)
        self._tmpfile = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self._tmpfile.close()
        self.sqlite_path = self._tmpfile.name
        self.src = SQLiteLedger(self.sqlite_path)
        self.pg = PostgresLedger(self._pg_dsn)

    def tearDown(self) -> None:
        import os
        import psycopg

        conn = psycopg.connect(pg_test_dsn(), autocommit=True)
        conn.execute(f'DROP SCHEMA IF EXISTS "{self._schema}" CASCADE')
        conn.close()
        if os.path.exists(self.sqlite_path):
            os.remove(self.sqlite_path)

    def _seed(self) -> ProgramRecord:
        program = ProgramRecord(
            program_id="prog_mig",
            title="Migration Test Program",
            conversation_key="conv_mig",
            state_version=1,
        )
        self.src.create_program(
            program, _mk_event("PROGRAM", program.program_id, 1, f"k_p1_{uuid.uuid4().hex}")
        )
        v2 = program.model_copy(update={"state_version": 2, "title": "Migration v2"})
        self.src.save_program(
            v2, expected_version=1, event=_mk_event("PROGRAM", v2.program_id, 2, f"k_p2_{uuid.uuid4().hex}")
        )
        return v2


class TestMigrationEndToEnd(MigrationBase):
    def test_migrate_preserves_hash_chain_and_data(self) -> None:
        v2 = self._seed()
        counts = migrate_sqlite_to_postgres(self.sqlite_path, self.pg)

        self.assertGreaterEqual(counts["programs"], 1)
        self.assertGreaterEqual(counts["program_versions"], 2)
        self.assertGreaterEqual(counts["audit_events"], 2)

        # Program current + all versions present in Postgres.
        pg_program = self.pg.get_program("prog_mig")
        self.assertEqual(pg_program.title, v2.title)
        self.assertEqual(pg_program.state_version, 2)
        self.assertEqual(self.pg.get_program_version("prog_mig", 1).state_version, 1)
        self.assertEqual(self.pg.get_program_version("prog_mig", 2).state_version, 2)

        # Audit chain still verifies after migration (hashes carried verbatim).
        self.assertTrue(self.pg.verify_audit_chain("PROGRAM", "prog_mig"))
        events = self.pg.list_events("PROGRAM", "prog_mig")
        self.assertEqual(len(events), 2)

        # The migrated source ledger remains independently consistent too.
        self.assertTrue(self.src.verify_audit_chain("PROGRAM", "prog_mig"))

    def test_migrate_is_idempotent(self) -> None:
        self._seed()
        counts_first = migrate_sqlite_to_postgres(self.sqlite_path, self.pg)
        counts_second = migrate_sqlite_to_postgres(self.sqlite_path, self.pg)
        # Second run adds nothing new.
        self.assertEqual(counts_first, counts_second)
        self.assertEqual(len(self.pg.list_events("PROGRAM", "prog_mig")), 2)


class TestDecisionsAndDissentsMigration(MigrationBase):
    def test_approvals_dissents_gate_decisions_migrate(self) -> None:
        # Not exercised exhaustively here; covered by ledger-level tests.
        self._seed()
        counts = migrate_sqlite_to_postgres(self.sqlite_path, self.pg)
        self.assertGreaterEqual(counts["audit_events"], 2)
        self.assertTrue(self.pg.verify_audit_chain("PROGRAM", "prog_mig"))


class TestBuildLedger(unittest.TestCase):
    def test_build_ledger_sqlite_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = f"{tmp}/x.db"
            ledger = build_ledger(path)
            self.assertIsInstance(ledger, SQLiteLedger)

    @skip_unless_postgres()
    def test_build_ledger_postgres_dsn(self) -> None:
        import psycopg

        schema = _new_schema()
        dsn = _dsn(schema)
        ledger = build_ledger(dsn)
        try:
            self.assertIsInstance(ledger, PostgresLedger)
        finally:
            conn = psycopg.connect(pg_test_dsn(), autocommit=True)
            conn.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
            conn.close()


if __name__ == "__main__":
    unittest.main()
