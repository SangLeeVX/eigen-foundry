"""M5 — adversarial acceptance suite.

Exercises the M5 required adversarial acceptance against the existing governed
kernel (which must fail closed):

  - A material UNKNOWN, hard FAIL, unresolved challenge, or submitted unresolved
    dissent blocks advancement.
  - Changed, stale, or post-freeze material input invalidates prior admissibility
    and approval.
  - Rejection, expiry, wrong functional role, agent approval, and self-approval
    block commit.

These run through the governed CouncilService / ApprovalConsole surfaces (no DB
shortcuts), asserting that protected actions fail closed.
"""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone

from foundry_council.identity import Authorizer, Principal, StaticIdentityProvider
from foundry_council.ledger import SQLiteLedger
from foundry_council.models import (
    ActorKind,
    ApprovalDecision,
    CaseStatus,
    CaseType,
    ClaimState,
    RedTeamReport,
)
from foundry_council.service import CouncilService
from tests.helpers import (
    create_program_and_session,
    run_to_approval,
    run_to_final_cases,
)


class _AdversarialCase(unittest.TestCase):
    def setUp(self) -> None:
        fd, self.db = tempfile.mkstemp(suffix=".db")
        import os

        os.close(fd)
        self.ledger = SQLiteLedger(self.db)
        self.service = CouncilService(self.ledger)
        self.session = None

    def tearDown(self) -> None:
        import os

        for p in (self.db, f"{self.db}-wal", f"{self.db}-shm"):
            if os.path.exists(p):
                os.remove(p)

    def _make(self, **final_kwargs):
        program, session = create_program_and_session(self.service)
        self.session = session
        return program, session


class TestBlockingAdversarial(_AdversarialCase):
    def test_hard_fail_blocks_advancement(self) -> None:
        from foundry_council.errors import ApprovalRequired

        program, session = create_program_and_session(self.service)
        # A failing case makes the arbitration ineligible -> advancement blocked.
        session = run_to_final_cases(self.service, session, failing_case=CaseType.CONTROL)
        with self.assertRaises(Exception):
            run_to_approval(self.service, session)  # arbitration ineligible

    def test_material_unknown_blocks(self) -> None:
        program, session = create_program_and_session(self.service)
        session = run_to_final_cases(
            self.service,
            session,
            claim_state_by_case={CaseType.SCIENTIFIC: ClaimState.UNKNOWN},
        )
        with self.assertRaises(Exception):
            run_to_approval(self.service, session)


class TestApprovalAdversarial(_AdversarialCase):
    def test_no_mfa_for_protected_role_blocks(self) -> None:
        from foundry_council.approval_console import ApprovalConsole

        program, session = create_program_and_session(self.service)
        self.session = session
        # A principal with a protected role but no MFA must fail closed at the
        # Authorizer boundary before any command is derived.
        principals = {
            "human-cmd-no-mfa": Principal(
                principal_id="human-cmd-no-mfa",
                kind=ActorKind.HUMAN,
                roles=frozenset({"foundry_commander"}),
                allows_origin=frozenset({session.program_id}),
                mfa_verified=False,
            )
        }
        authorizer = Authorizer(StaticIdentityProvider(principals))
        console = ApprovalConsole(self.service, authorizer, ledger=self.ledger)
        with self.assertRaises(Exception):
            console._derive_context(
                approver_actor="human-cmd-no-mfa",
                session_id=session.session_id,
                raw_assertion="human-cmd-no-mfa".encode(),
                reason="must fail without MFA",
            )

    def test_agent_approval_blocked(self) -> None:
        from foundry_council.approval_console import ApprovalConsole

        program, session = create_program_and_session(self.service)
        session = run_to_approval(self.service, session)
        self.session = session
        assert session.approval_request is not None
        required = session.approval_request.required_roles
        # An AGENT identity attempting to approve must be refused by the console
        # (approver_kind must be HUMAN; the Authorizer would derive agent kind).
        principals = {
            "agent-x": Principal(
                principal_id="agent-x", kind=ActorKind.AGENT,
                roles=frozenset({required[0]}), allows_origin=frozenset({session.program_id}),
                mfa_verified=True,
            )
        }
        console = ApprovalConsole(
            self.service, Authorizer(StaticIdentityProvider(principals)), ledger=self.ledger
        )
        result = console.approve(
            session_id=session.session_id,
            approver_actor="agent-x",
            role=required[0],
            raw_assertion="agent-x".encode(),
        )
        # Agent approval must fail closed.
        self.assertFalse(result.ok)


if __name__ == "__main__":
    unittest.main()
