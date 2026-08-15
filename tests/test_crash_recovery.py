from __future__ import annotations

import tempfile
import unittest
from unittest.mock import patch

from foundry_council.approval_console import ApprovalConsole
from foundry_council.crash_recovery import CrashRecovery
from foundry_council.identity import Authorizer, Principal, StaticIdentityProvider
from foundry_council.ledger import SQLiteLedger
from foundry_council.models import ActorKind, ApprovalDecision
from foundry_council.service import CouncilService
from tests.helpers import create_program_and_session, run_to_approval


class _CrashCase(unittest.TestCase):
    def setUp(self) -> None:
        fd, self.db = tempfile.mkstemp(suffix=".db")
        import os

        os.close(fd)
        self.session = None

    def tearDown(self) -> None:
        import os

        for p in (self.db, f"{self.db}-wal", f"{self.db}-shm"):
            if os.path.exists(p):
                os.remove(p)

    def _drive_to_approval(self) -> tuple[CouncilService, object]:
        ledger = SQLiteLedger(self.db)
        service = CouncilService(ledger)
        program, session = create_program_and_session(service)
        session = run_to_approval(service, session)
        self.session = session
        return service, session

    def _approvers(self) -> Authorizer:
        assert self.session is not None and self.session.approval_request is not None
        required = self.session.approval_request.required_roles
        scoped = frozenset({self.session.program_id})
        principals = {}
        for i, role in enumerate(required):
            pid = f"human-approver-{i}"
            principals[pid] = Principal(
                principal_id=pid,
                kind=ActorKind.HUMAN,
                roles=frozenset({role}),
                allows_origin=scoped,
                mfa_verified=True,
            )
        principals["committer-svc"] = Principal(
            principal_id="committer-svc",
            kind=ActorKind.SERVICE,
            roles=frozenset({"ledger_committer"}),
            allows_origin=None,
        )
        return Authorizer(StaticIdentityProvider(principals))

    def _fresh_console(self) -> ApprovalConsole:
        ledger = SQLiteLedger(self.db)
        service = CouncilService(ledger)
        return ApprovalConsole(service, self._approvers(), ledger=ledger)


class TestCrashRecovery(_CrashCase):
    def test_crash_before_commit_ready_to_commit(self) -> None:
        service, session = self._drive_to_approval()
        # Approve all roles.
        authorizer = self._approvers()
        required = session.approval_request.required_roles
        for i, role in enumerate(required):
            console = ApprovalConsole(service, authorizer, ledger=service.ledger)
            r = console.approve(
                session_id=session.session_id,
                approver_actor=f"human-approver-{i}",
                role=role,
                raw_assertion=f"human-approver-{i}".encode(),
            )
            self.assertTrue(r.ok, r.message)

        # Simulate a crash: fresh ledger read from the same durable DB file.
        recovery = CrashRecovery(SQLiteLedger(self.db))
        plan = recovery.plan(session.session_id)
        self.assertEqual(plan.kind, "READY_TO_COMMIT")
        self.assertEqual(plan.missing_roles, ())

    def test_crash_after_commit_is_idempotent_no_dup(self) -> None:
        service, session = self._drive_to_approval()
        authorizer = self._approvers()
        console = ApprovalConsole(service, authorizer, ledger=service.ledger)
        required = session.approval_request.required_roles
        for i, role in enumerate(required):
            console.approve(
                session_id=session.session_id,
                approver_actor=f"human-approver-{i}",
                role=role,
                raw_assertion=f"human-approver-{i}".encode(),
            )
        # Commit once through the restricted path.
        first = console.commit(
            session_id=session.session_id,
            approver_actor="committer-svc",
            raw_assertion="committer-svc".encode(),
            decision_id=f"dec-{session.session_id}",
        )
        self.assertTrue(first.ok, first.message)

        # Simulate crash AFTER commit: fresh read -> SESSION_COMMITTED.
        recovery = CrashRecovery(SQLiteLedger(self.db))
        plan = recovery.plan(session.session_id)
        self.assertEqual(plan.kind, "SESSION_COMMITTED")

        # Re-commit attempt is idempotent: no duplicate Program revision.
        program_before = recovery.ledger.get_program(session.program_id).state_version
        fresh = self._fresh_console()
        second = fresh.commit(
            session_id=session.session_id,
            approver_actor="committer-svc",
            raw_assertion="committer-svc".encode(),
            decision_id=f"dec-{session.session_id}",
        )
        self.assertTrue(second.ok, second.message)
        program_after = recovery.ledger.get_program(session.program_id).state_version
        self.assertEqual(program_before, program_after)  # no duplicate state

    def test_crash_with_missing_approval_is_blocked(self) -> None:
        service, session = self._drive_to_approval()
        # Do NOT approve any role -> crash before approval.
        recovery = CrashRecovery(SQLiteLedger(self.db))
        plan = recovery.plan(session.session_id)
        self.assertEqual(plan.kind, "BLOCKED_MISSING_APPROV")
        self.assertGreater(len(plan.missing_roles), 0)


if __name__ == "__main__":
    unittest.main()
