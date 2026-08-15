from __future__ import annotations

import tempfile
import unittest

from foundry_council.errors import Forbidden
from foundry_council.ledger import SQLiteLedger
from foundry_council.models import ActorKind, AuditEvent, ProgramStage
from foundry_council.service import CommandContext, CouncilService
from tests.helpers import create_program_and_session
# A non-committer CommandContext (draft-path role only) must never be able to
# change formal state.
_ORDINARY_CONTEXT = CommandContext(
    actor_id="agent-operator",
    actor_kind=ActorKind.AGENT,
    idempotency_key="key-c4-ordinary",
    expected_version=None,
    reason="ordinary draft-path command",
    principal_roles=frozenset({"program_drafter"}),
)


class _SqliteCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self._tmp.close()
        self.ledger = SQLiteLedger(self._tmp.name)
        self.service = CouncilService(self.ledger)

    def tearDown(self) -> None:
        import os

        for p in (self._tmp.name, f"{self._tmp.name}-wal", f"{self._tmp.name}-shm"):
            if os.path.exists(p):
                os.remove(p)


class TestSingleCommitPathGuards(_SqliteCase):
    def test_save_program_rejects_formal_route_change(self) -> None:
        # A draft-path save attempting to change the formal route is rejected by
        # the single-commit-path guard, even though a model bound would allow it.
        program, _ = create_program_and_session(self.service)
        # Try to flip formal state via the non-commit save path.
        mutated = program.model_copy(
            update={"program_id": program.program_id, "stage": ProgramStage.F1}
        )
        event = AuditEvent(
            event_id="evt-c4-route",
            idempotency_key="key-c4-formal-route",
            aggregate_type="PROGRAM",
            aggregate_id=program.program_id,
            aggregate_version=program.state_version + 1,
            actor_id="agent-operator",
            actor_kind=ActorKind.AGENT,
            action="PROGRAM.X",
            reason="attempted formal-state mutation via implicit save path",
        )
        with self.assertRaises(ValueError):
            self.ledger.save_program(mutated, program.state_version, event)

    def test_save_program_allows_non_formal_mutation(self) -> None:
        # Changing non-formal fields (e.g. title) via save_program is fine.
        program, _ = create_program_and_session(self.service)
        current = self.ledger.get_program(program.program_id)  # authoritative version
        renamed = current.model_copy(
            update={"title": "Renamed title", "state_version": current.state_version + 1}
        )
        event = AuditEvent(
            event_id="evt-c4-rename",
            idempotency_key="key-c4-rename",
            aggregate_type="PROGRAM",
            aggregate_id=program.program_id,
            aggregate_version=current.state_version + 1,
            actor_id="agent-operator",
            actor_kind=ActorKind.AGENT,
            action="PROGRAM.EDIT",
            reason="rename only",
        )
        saved = self.ledger.save_program(renamed, current.state_version, event)
        self.assertEqual(saved.title, "Renamed title")
        self.assertEqual(saved.stage, current.stage)  # formal state unchanged

    def test_commit_gate_decision_requires_committer_role(self) -> None:
        # Only the restricted commit path may change formal state; an ordinary
        # context cannot invoke it even if it reaches the method.
        program, session = create_program_and_session(self.service)
        with self.assertRaises((Forbidden,)):
            self.service.commit_gate_decision(
                session.session_id,
                "decision-not-authorised",
                _ORDINARY_CONTEXT,
            )


if __name__ == "__main__":
    unittest.main()
