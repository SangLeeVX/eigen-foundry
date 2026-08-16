from __future__ import annotations

import base64
import json
import os
import tempfile
import unittest
from pathlib import Path

from foundry_council.errors import Forbidden, ValidationFailure
from foundry_council.identity import AuthorizationRequest, Authorizer
from foundry_council.models import ActorKind
from foundry_council.signed_identity import (
    DEFAULT_AUDIENCE,
    PROTECTED_HUMAN_ROLES,
    IdentitySecretUnavailable,
    SignedAssertionIdentityProvider,
    _sign,
    load_signing_secret,
    mint_assertion,
)


def _segments(token: bytes) -> tuple[dict, dict]:
    header, payload, _ = token.decode("ascii").split(".")
    pad = lambda s: s + "=" * (-len(s) % 4)  # noqa: E731
    return (
        json.loads(base64.urlsafe_b64decode(pad(header))),
        json.loads(base64.urlsafe_b64decode(pad(payload))),
    )


def _reencode(token: bytes, *, payload: dict | None = None, signature: str | None = None) -> bytes:
    header_seg, payload_seg, _ = token.decode("ascii").split(".")
    if payload is not None:
        payload_seg = base64.urlsafe_b64encode(
            json.dumps(payload, separators=(",", ":")).encode()
        ).rstrip(b"=").decode("ascii")
    sig = _sign(b"top-secret", f"{header_seg}.{payload_seg}") if signature is None else signature
    return f"{header_seg}.{payload_seg}.{sig}".encode("ascii")


class SignedAssertionIdentityProviderTests(unittest.TestCase):
    """M4-C4: signed-JWT assertions bind a verified Principal with fail-closed checks."""

    SECRET = b"top-secret"

    def setUp(self) -> None:
        self.provider = SignedAssertionIdentityProvider(self.SECRET)

    def _token(self, **overrides) -> bytes:
        return mint_assertion(
            self.SECRET,
            subject=overrides.pop("subject", "human-approver"),
            audience=overrides.pop("audience", DEFAULT_AUDIENCE),
            roles=overrides.pop("roles", frozenset({"approver"})),
            kind=overrides.pop("kind", ActorKind.HUMAN),
            programs=overrides.pop("programs", None),
            mfa_verified=overrides.pop("mfa", True),
            **overrides,
        )

    def test_valid_assertion_binds_principal(self) -> None:
        token = self._token(subject="human-approver", roles=frozenset({"approver"}), programs=frozenset({"p1"}))
        principal = self.provider.verify(token, DEFAULT_AUDIENCE)
        self.assertEqual(principal.principal_id, "human-approver")
        self.assertEqual(principal.kind, ActorKind.HUMAN)
        self.assertIn("approver", principal.roles)
        self.assertTrue(principal.mfa_verified)
        self.assertEqual(principal.allows_origin, frozenset({"p1"}))
        self.assertIsNotNone(principal.expires_at)

    def test_authorizer_derives_trusted_context_from_signed_assertion(self) -> None:
        authz = Authorizer(self.provider)
        token = self._token(subject="human-approver", roles=frozenset({"approver"}), programs=frozenset({"p1"}))
        ctx = authz.authorize(
            AuthorizationRequest(
                raw_assertion=token,
                idempotency_key="console:human-approver:s1",
                expected_version=1,
                reason="approve",
                program_id="p1",
            )
        )
        self.assertEqual(ctx.actor_id, "human-approver")
        self.assertEqual(ctx.actor_kind, ActorKind.HUMAN)
        self.assertIn("approver", ctx.principal_roles)

    def test_expired_assertion_fails_closed(self) -> None:
        token = self._token(expires_in=-3600)  # expired well beyond leeway
        with self.assertRaises(Forbidden) as ctx:
            self.provider.verify(token, DEFAULT_AUDIENCE)
        self.assertIn("expired", str(ctx.exception))

    def test_bad_signature_fails_closed(self) -> None:
        token = _reencode(self._token(), signature="forged")
        with self.assertRaises(Forbidden) as ctx:
            self.provider.verify(token, DEFAULT_AUDIENCE)
        self.assertIn("signature", str(ctx.exception))

    def test_wrong_audience_fails_closed(self) -> None:
        token = self._token(audience="some-other-system")
        with self.assertRaises(Forbidden) as ctx:
            self.provider.verify(token, DEFAULT_AUDIENCE)
        self.assertIn("audience", str(ctx.exception))

    def test_wrong_issuer_fails_closed(self) -> None:
        token = self._token(issuer="attacker-issuer")
        with self.assertRaises(Forbidden) as ctx:
            self.provider.verify(token, DEFAULT_AUDIENCE)
        self.assertIn("issuer", str(ctx.exception))

    def test_future_nbf_fails_closed(self) -> None:
        import time

        token = self._token(not_before=int(time.time()) + 3600)
        with self.assertRaises(Forbidden):
            self.provider.verify(token, DEFAULT_AUDIENCE)

    def test_malformed_token_fails_closed(self) -> None:
        for bad in (b"not-a-jwt", b"a.b", b"..", b"\x00\xff"):
            with self.assertRaises(ValidationFailure):
                self.provider.verify(bad, DEFAULT_AUDIENCE)

    def test_tampered_claims_fail_closed(self) -> None:
        token = self._token(subject="human-approver")
        header_seg, _, sig = token.decode("ascii").split(".")
        payload = {"sub": "attacker", "aud": DEFAULT_AUDIENCE, "exp": 9999999999}
        payload_seg = base64.urlsafe_b64encode(
            json.dumps(payload, separators=(",", ":")).encode()
        ).rstrip(b"=").decode("ascii")
        forged = f"{header_seg}.{payload_seg}.{sig}".encode("ascii")
        with self.assertRaises(Forbidden):
            self.provider.verify(forged, DEFAULT_AUDIENCE)

    def test_no_self_approval_agent_cannot_carry_human_protected_roles(self) -> None:
        # An agent-kind token (even with a valid signature + MFA flag) must not
        # be able to approve: human protected roles are human-only. mint_assertion
        # refuses to create one, so forge a validly signed one to exercise the
        # provider's self-approval guard.
        token = self._token(kind=ActorKind.AGENT, roles=frozenset({"case_captain"}))
        _, claims = _segments(token)
        claims["roles"] = ["approver"]
        forged = _reencode(token, payload=claims)  # valid signature, agent + approver
        with self.assertRaises(Forbidden) as ctx:
            self.provider.verify(forged, DEFAULT_AUDIENCE)
        self.assertIn("human protected roles", str(ctx.exception))

    def test_mint_refuses_self_approval_token(self) -> None:
        with self.assertRaises(ValueError):
            mint_assertion(self.SECRET, subject="agent-x", kind=ActorKind.AGENT, roles=frozenset({"approver"}))
        with self.assertRaises(ValueError):
            mint_assertion(self.SECRET, subject="svc-x", kind=ActorKind.SERVICE, roles=frozenset({"policy_admin"}))

    def test_agent_with_non_protected_roles_is_allowed(self) -> None:
        token = self._token(kind=ActorKind.AGENT, roles=frozenset({"case_captain"}), mfa=False)
        principal = self.provider.verify(token, DEFAULT_AUDIENCE)
        self.assertEqual(principal.kind, ActorKind.AGENT)
        self.assertIn("case_captain", principal.roles)

    def test_missing_expiry_fails_closed(self) -> None:
        token = self._token()
        _, claims = _segments(token)
        claims.pop("exp")
        with self.assertRaises(ValidationFailure):
            self.provider.verify(_reencode(token, payload=claims), DEFAULT_AUDIENCE)

    def test_unknown_kind_fails_closed(self) -> None:
        token = self._token()
        _, claims = _segments(token)
        claims["kind"] = "ALIEN"
        with self.assertRaises(ValidationFailure):
            self.provider.verify(_reencode(token, payload=claims), DEFAULT_AUDIENCE)

    def test_wrong_secret_provider_rejects(self) -> None:
        other = SignedAssertionIdentityProvider(b"other-secret")
        with self.assertRaises(Forbidden):
            other.verify(self._token(), DEFAULT_AUDIENCE)

    def test_approval_path_requires_signed_assertion(self) -> None:
        # End-to-end: the authenticated human path (Authorizer over the signed
        # provider, as wired into ApprovalConsole) rejects a bare/unsigned
        # human identity and accepts only a valid signed assertion.
        authz = Authorizer(self.provider)
        with self.assertRaises(ValidationFailure):
            authz.authorize(
                AuthorizationRequest(
                    raw_assertion=b"human-approver",  # static-style bare id
                    idempotency_key="k",
                    expected_version=None,
                    reason="r",
                )
            )
        token = self._token(subject="human-approver", roles=frozenset({"approver"}))
        ctx = authz.authorize(
            AuthorizationRequest(
                raw_assertion=token,
                idempotency_key="k2",
                expected_version=None,
                reason="r",
            )
        )
        self.assertEqual(ctx.actor_id, "human-approver")
        self.assertIn("approver", ctx.principal_roles)


class SigningSecretStoreTests(unittest.TestCase):
    """M4-C4: the signing secret lives in the approved secrets store only."""

    def test_loads_secret_from_env_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "foundry_identity.env"
            path.write_text("FOUNDRY_IDENTITY_SIGNING_SECRET=abc123secret\n", encoding="utf-8")
            self.assertEqual(load_signing_secret(path), b"abc123secret")

    def test_missing_secret_fails_closed_without_value(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "foundry_identity.env"
            path.write_text("SOMETHING_ELSE=1\n", encoding="utf-8")
            with self.assertRaises(IdentitySecretUnavailable) as ctx:
                load_signing_secret(path)
            self.assertNotIn("abc123", str(ctx.exception))

    def test_from_secrets_env_builds_provider(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "foundry_identity.env"
            path.write_text("FOUNDRY_IDENTITY_SIGNING_SECRET=roundtrip-secret\n", encoding="utf-8")
            provider = SignedAssertionIdentityProvider.from_secrets_env(path)
            token = mint_assertion(b"roundtrip-secret", subject="human-approver")
            principal = provider.verify(token, DEFAULT_AUDIENCE)
            self.assertEqual(principal.principal_id, "human-approver")

    def test_protected_human_roles_inventory(self) -> None:
        self.assertTrue({"approver", "policy_admin"}.issubset(PROTECTED_HUMAN_ROLES))


if __name__ == "__main__":
    unittest.main()
