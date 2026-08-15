from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone

from foundry_council.approval_console import ApprovalConsole
from foundry_council.identity import Authorizer, Principal, StaticIdentityProvider
from foundry_council.ledger import SQLiteLedger
from foundry_council.models import ActorKind, ApprovalDecision
from foundry_council.service import CouncilService
from tests.helpers import create_program_and_session, run_to_approval


class _ConsoleCase(unittest.TestCase):
    def setUp(self) -> None:
        fd, self.db = tempfile.mkstemp(suffix=".db")
        import os

        os.close(fd)
        self.ledger = SQLiteLedger(self.db)
        self.service = CouncilService(self.ledger)
        # Drive a session to AWAITING_HUMAN_APPROVAL using the governed helpers.
        program, session = create_program_and_session(self.service)
        self.session = run_to_approval(self.service, session)

    def tearDown(self) -> None:
        import os

        for p in (self.db, f"{self.db}-wal", f"{self.db}-shm"):
            if os.path.exists(p):
                os.remove(p)

    def _authorizer(self) -> Authorizer:
        # Build MFA-verified human principals for each required approver role,
        # plus a service committer principal (assertion = principal_id bytes).
        assert self.session.approval_request is not None
        required = self.session.approval_request.required_roles
        scoped = frozenset({self.session.program_id})
        principals: dict[str, Principal] = {}
        for index, role in enumerate(required):
            pid = f"human-approver-{index}"
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

    def _console(self) -> ApprovalConsole:
        return ApprovalConsole(
            self.service,
            self._authorizer(),
            ledger=self.ledger,
            pending_session_ids=(self.session.session_id,),
        )


class TestApprovalConsoleList(_ConsoleCase):
    def test_lists_pending_with_missing_roles(self) -> None:
        console = self._console()
        pending = console.list_pending()
        self.assertEqual(len(pending), 1)
        item = pending[0]
        self.assertEqual(item.session_id, self.session.session_id)
        self.assertEqual(sorted(item.required_roles), sorted(item.missing_roles))  # nothing signed yet


class TestApprovalConsoleApprove(_ConsoleCase):
    def test_approve_each_role_then_commit(self) -> None:
        console = self._console()
        assert self.session.approval_request is not None
        required = self.session.approval_request.required_roles

        # Approve each required role through the authenticated console.
        for index, role in enumerate(required):
            result = console.approve(
                session_id=self.session.session_id,
                approver_actor=f"human-approver-{index}",
                role=role,
                raw_assertion=f"human-approver-{index}".encode(),
                decision=ApprovalDecision.APPROVED,
            )
            self.assertTrue(result.ok, result.message)

        # Now the quorum is complete -> pending shows no missing roles.
        pending = console.list_pending()
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0].missing_roles, ())

        # Commit through the restricted path (service committer).
        commit = console.commit(
            session_id=self.session.session_id,
            approver_actor="committer-svc",
            raw_assertion="committer-svc".encode(),
            decision_id=f"decision-{self.session.session_id}",
        )
        self.assertTrue(commit.ok, commit.message)
        self.assertEqual(commit.detail["program_stage"], "F1")

    def test_wrong_role_fails_closed(self) -> None:
        # A principal whose verified roles do NOT include the approver role must
        # be rejected — the console cannot self-assert authority.
        assert self.session.approval_request is not None
        required = self.session.approval_request.required_roles
        role = required[0]
        scoped = frozenset({self.session.program_id})
        principals = {
            "human-wrong-role": Principal(
                principal_id="human-wrong-role",
                kind=ActorKind.HUMAN,
                roles=frozenset({"some_unrelated_role"}),  # NOT a required approver role
                allows_origin=scoped,
                mfa_verified=True,
            )
        }
        console = ApprovalConsole(
            self.service, Authorizer(StaticIdentityProvider(principals)), ledger=self.ledger
        )
        result = console.approve(
            session_id=self.session.session_id,
            approver_actor="human-wrong-role",
            role=role,
            raw_assertion="human-wrong-role".encode(),
        )
        self.assertFalse(result.ok)

    def test_mfa_required_for_protected_role(self) -> None:
        # A protected role (foundry_commander) without confirmed MFA must be
        # rejected by the Authorizer before any command is derived.
        assert self.session.approval_request is not None
        scoped = frozenset({self.session.program_id})
        principals = {
            "human-commander-no-mfa": Principal(
                principal_id="human-commander-no-mfa",
                kind=ActorKind.HUMAN,
                roles=frozenset({"foundry_commander"}),
                allows_origin=scoped,
                mfa_verified=False,
            )
        }
        console = ApprovalConsole(
            self.service, Authorizer(StaticIdentityProvider(principals)), ledger=self.ledger
        )
        # Any approval attempt derives a context via the Authorizer, which must
        # reject the MFA-less protected-role principal.
        with self.assertRaises(Exception):
            console._derive_context(
                approver_actor="human-commander-no-mfa",
                session_id=self.session.session_id,
                raw_assertion="human-commander-no-mfa".encode(),
                reason="should fail without MFA",
            )


if __name__ == "__main__":
    unittest.main()
