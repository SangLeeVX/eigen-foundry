from __future__ import annotations

import unittest

from foundry_council.errors import Forbidden, ValidationFailure
from foundry_council.identity import (
    Authorizer,
    AuthorizationRequest,
    Principal,
    StaticIdentityProvider,
)
from foundry_council.models import ActorKind


def principal(
    pid="agent-commander",
    kind=ActorKind.AGENT,
    roles=frozenset({"foundry_commander"}),
    scope=frozenset({"program-p1"}),
    mfa=False,
    expires_at=None,
):
    return Principal(pid, kind, roles, scope, mfa, expires_at)


class AuthorizerTests(unittest.TestCase):
    """M2-C2: program-scoped authorization derives a trusted CommandContext."""

    def setUp(self) -> None:
        self.provider = StaticIdentityProvider(
            {
                "agent-commander": principal(),
                "human-approver": principal(
                    "human-approver", ActorKind.HUMAN, frozenset({"approver", "policy_admin"}), None, True
                ),
                "human-no-mfa": principal(
                    "human-no-mfa", ActorKind.HUMAN, frozenset({"approver"}), None, False
                ),
                "expired": principal("expired", expires_at=1),
            }
        )
        self.authz = Authorizer(self.provider)

    def _req(self, pid, program=None, **kw):
        return AuthorizationRequest(
            raw_assertion=pid.encode(),
            idempotency_key=kw.get("key", "cmd-1"),
            expected_version=kw.get("version", 1),
            reason=kw.get("reason", "test"),
            program_id=program,
        )

    def test_verified_agent_gets_scoped_context(self) -> None:
        ctx = self.authz.authorize(self._req("agent-commander", "program-p1"))
        self.assertEqual(ctx.actor_kind, ActorKind.AGENT)
        self.assertEqual(ctx.principal_roles, frozenset({"foundry_commander"}))
        self.assertEqual(ctx.idempotency_key, "cmd-1")

    def test_human_cannot_cross_program_scope(self) -> None:
        with self.assertRaises(Forbidden):
            self.authz.authorize(self._req("agent-commander", "program-other"))

    def test_service_scope_none_allows_all(self) -> None:
        # A service-identity principal with scope=None is trusted inside any program.
        self.provider._principals["svc"] = Principal(
            "svc", ActorKind.SERVICE, frozenset({"ledger_committer"}), None, False
        )
        ctx = self.authz.authorize(self._req("svc", "program-p1"))
        self.assertEqual(ctx.actor_kind, ActorKind.SERVICE)

    def test_mfa_required_for_human_protected_role(self) -> None:
        with self.assertRaises(Forbidden):
            self.authz.authorize(self._req("human-no-mfa", None))

    def test_mfa_verified_human_protected_role_is_allowed(self) -> None:
        ctx = self.authz.authorize(self._req("human-approver", None))
        self.assertEqual(ctx.actor_kind, ActorKind.HUMAN)
        self.assertIn("approver", ctx.principal_roles)

    def test_role_cannot_be_elevated_by_request(self) -> None:
        # Request cannot add a role the verified principal was not granted.
        ctx = self.authz.authorize(self._req("agent-commander", "program-p1"))
        self.assertNotIn("policy_admin", ctx.principal_roles)

    def test_expired_assertion_fails_closed(self) -> None:
        with self.assertRaises(Forbidden):
            self.authz.authorize(self._req("expired", None))

    def test_unknown_principal_fails_closed(self) -> None:
        with self.assertRaises(Forbidden):
            self.authz.authorize(self._req("ghost", None))

    def test_malformed_assertion_fails_closed(self) -> None:
        with self.assertRaises(ValidationFailure):
            self.authz.authorize(AuthorizationRequest(b"\x00\xff", "cmd", 1, "t"))


if __name__ == "__main__":
    unittest.main()
