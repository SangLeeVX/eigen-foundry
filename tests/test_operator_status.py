from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class TestOperatorStatus(unittest.TestCase):
    """C6: operator UX shell surfaces durable ledger state."""

    def _status(self, *args: str) -> dict:
        result = subprocess.run(
            [sys.executable, "-m", "foundry_council.cli", "status", *args],
            capture_output=True,
            text=True,
            env={"PYTHONPATH": str(ROOT / "src"), "PATH": "/usr/bin:/bin"},
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_status_reports_backend_and_counts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = f"{tmp}/ledger.db"
            # seed a program + event
            from foundry_council.ledger import SQLiteLedger
            from foundry_council.models import AuditEvent, ProgramRecord

            ledger = SQLiteLedger(db)
            prog = ProgramRecord(
                program_id="OP01", title="Operator Prog", conversation_key="conv_op1"
            )
            ledger.create_program(
                prog,
                AuditEvent(
                    event_id="evt_op1",
                    idempotency_key="key_op1",
                    aggregate_type="PROGRAM",
                    aggregate_id="OP01",
                    aggregate_version=1,
                    actor_id="actor_op",
                    actor_kind="AGENT",
                    action="CREATE",
                    reason="seed operator state",
                ),
            )
            report = self._status("--db", db)
            self.assertEqual(report["backend"], "SQLiteLedger")
            self.assertEqual(report["program_count"], 1)
            self.assertEqual(report["session_count"], 0)
            self.assertTrue(report["audit_chains_valid"]["program"])
            self.assertEqual(report["programs"][0]["program_id"], "OP01")
            self.assertEqual(report["programs"][0]["stage"], "F0")


if __name__ == "__main__":
    unittest.main()
