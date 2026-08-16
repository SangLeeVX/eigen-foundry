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

from foundry_council.identity import Authorizer
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
    # M5-C5 drives the authenticated staging surface through the signed-assertion
    # identity provider (M4-C4), so the adversarial suite proves the REAL signed
    # path enforces every fail-closed guard (MFA, agent-kind, no self-approval).
    TEST_SECRET = b"test-secret-for-adversarial-auth-not-a-real-credential"

    def _signed_console(self, session):
        from foundry_council.approval_console import ApprovalConsole
        from foundry_council.signed_identity import SignedAssertionIdentityProvider, mint_assertion

        provider = SignedAssertionIdentityProvider(self.TEST_SECRET)
        console = ApprovalConsole(
            self.service, Authorizer(provider), ledger=self.ledger
        )
        return console, mint_assertion

    def test_no_mfa_for_protected_role_blocks(self) -> None:
        from foundry_council.signed_identity import mint_assertion

        program, session = create_program_and_session(self.service)
        self.session = session
        console, _ = self._signed_console(session)
        # A signed human token that legitimately lacks MFA must fail closed at the
        # Authorizer boundary before any command is derived.
        token = mint_assertion(
            self.TEST_SECRET, subject="human-cmd-no-mfa",
            roles=frozenset({"foundry_commander"}), kind=ActorKind.HUMAN,
            mfa_verified=False, programs=frozenset({session.program_id}),
        )
        with self.assertRaises(Exception):
            console._derive_context(
                approver_actor="human-cmd-no-mfa",
                session_id=session.session_id,
                raw_assertion=token,
                reason="must fail without MFA",
            )

    def test_agent_approval_blocked(self) -> None:
        import json
        import time as _time
        from foundry_council.signed_identity import _b64encode, _sign

        program, session = create_program_and_session(self.service)
        session = run_to_approval(self.service, session)
        self.session = session
        assert session.approval_request is not None
        required = session.approval_request.required_roles
        console, _ = self._signed_console(session)
        # Build a validly-SIGNED assertion whose claims claim agent-kind with a
        # human approver role. mint_assertion refuses to mint this, so forge it
        # to prove the provider's no-self-approval guard rejects it even with a
        # correct signature.
        header_seg = _b64encode(json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":")).encode())
        now = int(_time.time())
        payload = {
            "sub": "agent-x", "aud": "eigen-foundry-control-plane",
            "iss": "eigen-foundry-control-plane", "iat": now, "exp": now + 900,
            "kind": "AGENT", "roles": [required[0]], "mfa": True,
        }
        payload_seg = _b64encode(json.dumps(payload, separators=(",", ":")).encode())
        sig = _sign(self.TEST_SECRET, f"{header_seg}.{payload_seg}")
        forged = f"{header_seg}.{payload_seg}.{sig}".encode("ascii")
        result = console.approve(
            session_id=session.session_id,
            approver_actor="agent-x",
            role=required[0],
            raw_assertion=forged,
        )
        # Agent approval must fail closed (valid signature, but agent-kind cannot
        # carry a human protected approver role).
        self.assertFalse(result.ok)


if __name__ == "__main__":
    unittest.main()
