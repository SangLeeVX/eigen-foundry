"""M4-C4 — signed-assertion identity for authenticated human approval/commit.

The kernel's identity boundary (``identity.Authorizer``) already derives a
trusted ``CommandContext`` from a verified ``Principal``. Historically the only
provider was ``StaticIdentityProvider``, whose "assertions" are bare principal
ids — fine for control-plane tests, but it never *verifies* a real signed
assertion.

This module adds the real verification path for M4-C4:

  - :class:`SignedAssertionIdentityProvider` verifies a short-lived signed JWT
    (HS256) — signature, expiry (``now`` vs ``exp``), ``nbf``, issuer, and
    audience — and binds the verified claims to a ``Principal``.
  - :func:`mint_assertion` issues test/operator assertions bound to a
    subject + audience + expiry. It refuses to mint "no self-approval"
    tokens (agent/service kind carrying human protected approver roles).
  - :func:`load_signing_secret` reads the signing secret from the approved
    secrets store (``~/.openclaw/workspace/secrets/foundry_identity.env``,
    chmod 600). The secret is never committed to the repository and never
    appears in exceptions or logs.

The provider is drop-in for ``identity.IdentityProvider``, so the existing
``Authorizer`` and ``ApprovalConsole`` gain a real authenticated-human path
without any change to their fail-closed authorization logic.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .errors import Forbidden, ValidationFailure
from .identity import Authorizer, Principal
from .models import ActorKind

DEFAULT_IDENTITY_ENV = Path.home() / ".openclaw" / "workspace" / "secrets" / "foundry_identity.env"
DEFAULT_ISSUER = "eigen-foundry-control-plane"
DEFAULT_AUDIENCE = "eigen-foundry-control-plane"

# Human protected roles that no self-approving agent/service token may carry.
PROTECTED_HUMAN_ROLES = frozenset({"approver", "policy_admin", "program_owner", "foundry_commander"})

_HEADER = {"alg": "HS256", "typ": "JWT"}


class IdentitySecretUnavailable(ValidationFailure):
    """The approved identity signing secret could not be resolved."""


def _b64encode(payload: bytes) -> str:
    return base64.urlsafe_b64encode(payload).rstrip(b"=").decode("ascii")


def _b64decode(segment: str) -> bytes:
    padding = "=" * (-len(segment) % 4)
    try:
        return base64.urlsafe_b64decode(segment + padding)
    except (ValueError, TypeError) as exc:
        raise ValidationFailure("identity assertion is not valid base64url") from exc


def load_signing_secret(env_file: str | Path | None = None) -> bytes:
    """Load the HS256 signing secret from the approved secrets store.

    Resolution order: explicit ``env_file`` > ``FOUNDRY_IDENTITY_ENV`` >
    the default secrets path > ambient ``FOUNDRY_IDENTITY_SIGNING_SECRET``.
    The secret value is never included in any error message.
    """
    requested = env_file or os.environ.get("FOUNDRY_IDENTITY_ENV") or DEFAULT_IDENTITY_ENV
    secret: str | None = None
    if requested:
        try:
            for line in Path(requested).read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line.startswith("FOUNDRY_IDENTITY_SIGNING_SECRET="):
                    secret = line.partition("=")[2].strip().strip('"').strip("'")
                    break
        except OSError:
            secret = None
    if not secret:
        secret = os.environ.get("FOUNDRY_IDENTITY_SIGNING_SECRET")
    if not secret:
        raise IdentitySecretUnavailable(
            "identity signing secret is not present in the approved secrets "
            "store (foundry_identity.env)"
        )
    return secret.encode("utf-8")


def _sign(secret: bytes, signing_input: str) -> str:
    signature = hmac.new(secret, signing_input.encode("utf-8"), hashlib.sha256).digest()
    return _b64encode(signature)


def mint_assertion(
    secret: bytes,
    *,
    subject: str,
    audience: str = DEFAULT_AUDIENCE,
    roles: frozenset[str] = frozenset(),
    kind: ActorKind = ActorKind.HUMAN,
    programs: frozenset[str] | None = None,
    mfa_verified: bool = True,
    expires_in: int = 900,
    issuer: str = DEFAULT_ISSUER,
    not_before: int | None = None,
) -> bytes:
    """Issue a short-lived signed JWT assertion (test/operator path only).

    Refuses to mint a self-approval token: a non-human kind carrying human
    protected approver roles fails closed.
    """
    if kind is not ActorKind.HUMAN and roles.intersection(PROTECTED_HUMAN_ROLES):
        raise ValueError("non-human assertion cannot carry human protected roles")
    now = int(time.time())
    claims: dict[str, Any] = {
        "sub": subject,
        "aud": audience,
        "iss": issuer,
        "iat": now,
        "exp": now + expires_in,
        "kind": kind.value,
        "roles": sorted(roles),
        "mfa": mfa_verified,
    }
    if programs is not None:
        claims["programs"] = sorted(programs)
    if not_before is not None:
        claims["nbf"] = not_before
    header = _b64encode(json.dumps(_HEADER, separators=(",", ":")).encode("utf-8"))
    payload = _b64encode(json.dumps(claims, separators=(",", ":")).encode("utf-8"))
    signing_input = f"{header}.{payload}"
    return f"{signing_input}.{_sign(secret, signing_input)}".encode("ascii")


class SignedAssertionIdentityProvider:
    """Verifies short-lived signed JWTs and binds them to a Principal.

    Enforced, in order: token structure -> signature -> ``nbf``/``exp`` ->
    audience -> issuer -> self-approval guard. Every failure is fail-closed
    (:class:`Forbidden` or :class:`ValidationFailure`), and no failure path
    reveals token contents beyond a safe description.
    """

    def __init__(
        self,
        secret: bytes | str,
        *,
        issuer: str = DEFAULT_ISSUER,
        leeway: int = 30,
    ) -> None:
        if isinstance(secret, str):
            secret = secret.encode("utf-8")
        if not secret:
            raise ValueError("signing secret must not be empty")
        self._secret = secret
        self._issuer = issuer
        self._leeway = leeway

    @classmethod
    def from_secrets_env(cls, env_file: str | Path | None = None) -> "SignedAssertionIdentityProvider":
        return cls(load_signing_secret(env_file))

    def verify(self, raw_assertion: bytes, expected_audience: str) -> Principal:
        if not isinstance(raw_assertion, bytes):
            raise ValidationFailure("identity assertion is not bytes")
        try:
            text = raw_assertion.decode("ascii")
        except UnicodeDecodeError as exc:
            raise ValidationFailure("identity assertion is not text") from exc
        parts = text.split(".")
        if len(parts) != 3 or not all(parts):
            raise ValidationFailure("identity assertion is not a signed JWT")

        header_segment, payload_segment, signature_segment = parts
        signing_input = f"{header_segment}.{payload_segment}"
        expected = _sign(self._secret, signing_input)
        if not hmac.compare_digest(signature_segment, expected):
            raise Forbidden("identity assertion signature verification failed")

        try:
            header = json.loads(_b64decode(header_segment))
            claims = json.loads(_b64decode(payload_segment))
        except (ValueError, ValidationFailure) as exc:
            raise ValidationFailure("identity assertion payload is malformed") from exc
        if not isinstance(header, dict) or header.get("alg") != "HS256":
            raise ValidationFailure("identity assertion uses an unsupported algorithm")
        if not isinstance(claims, dict):
            raise ValidationFailure("identity assertion payload is not an object")

        now = int(time.time())
        nbf = claims.get("nbf")
        if isinstance(nbf, int) and now + self._leeway < nbf:
            raise Forbidden("identity assertion is not yet valid")
        exp = claims.get("exp")
        if not isinstance(exp, int):
            raise ValidationFailure("identity assertion has no expiry")
        if now > exp + self._leeway:
            raise Forbidden("identity assertion expired")

        if claims.get("aud") != expected_audience:
            raise Forbidden("identity assertion audience does not match")
        if self._issuer and claims.get("iss") != self._issuer:
            raise Forbidden("identity assertion issuer does not match")

        subject = claims.get("sub")
        if not isinstance(subject, str) or not subject:
            raise ValidationFailure("identity assertion has no subject")
        try:
            kind = ActorKind(str(claims.get("kind", "HUMAN")))
        except ValueError as exc:
            raise ValidationFailure("identity assertion has an invalid kind") from exc
        roles = frozenset(claims.get("roles", [])) if isinstance(claims.get("roles", []), list) else frozenset()
        if kind is not ActorKind.HUMAN and roles.intersection(PROTECTED_HUMAN_ROLES):
            # No self-approval: an agent/service token can never carry the
            # human approver roles, even if its signature is valid.
            raise Forbidden("non-human assertion carries human protected roles")
        programs = claims.get("programs")
        scope = frozenset(programs) if isinstance(programs, list) else None
        return Principal(
            principal_id=subject,
            kind=kind,
            roles=roles,
            allows_origin=scope,
            mfa_verified=bool(claims.get("mfa", False)),
            expires_at=exp,
        )


def build_signed_authorizer(
    env_file: str | Path | None = None,
    *,
    audience: str = DEFAULT_AUDIENCE,
) -> Authorizer:
    """Build an ``identity.Authorizer`` backed by the signed-assertion provider.

    Wire this into ``ApprovalConsole`` so the authenticated human
    approval/commit path requires a valid signed assertion (no self-approval).
    """
    provider = SignedAssertionIdentityProvider.from_secrets_env(env_file)
    return Authorizer(provider, expected_audience=audience)
