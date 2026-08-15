"""Source of trust for M2-C2: program-scoped identity and authorization.

The current kernel's CommandContext assumes a trusted internal caller. This
module is the boundary that wraps an untrusted network request and produces a
*verified* CommandContext from identity claims, so that no request body can
elevate roles, MFA, program scope, or actor kind.

Implements:
  - IdentityProvider: OIDC (human) or service-identity-backed principal claims.
  - ProgramScopeResolver: which programs a principal may act within.
  - Authorizer: deterministic derivation of a trusted CommandContext with
    fail-closed authorization. It never derives more authority than the
    verified claims grant.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from .errors import Forbidden, ValidationFailure
from .models import ActorKind, StableId
from .service import CommandContext


class Principal:
    """Verified identity claims from an external identity provider."""

    def __init__(
        self,
        principal_id: StableId,
        kind: ActorKind,
        roles: frozenset[str],
        allows_origin: frozenset[StableId] | None,
        mfa_verified: bool = False,
        expires_at: int | None = None,
    ) -> None:
        self.principal_id = principal_id
        self.kind = kind
        self.roles = roles
        self.allows_origin = allows_origin  # None = all programs (service)
        self.mfa_verified = mfa_verified
        self.expires_at = expires_at  # epoch seconds; None = no expiry


class IdentityProvider(Protocol):
    def verify(self, raw_assertion: bytes, expected_audience: str) -> Principal: ...


class StaticIdentityProvider:
    """Test/control-plane identity provider (deterministic; not for prod)."""

    def __init__(self, principals: dict[str, Principal]) -> None:
        self._principals = principals

    def verify(self, raw_assertion: bytes, expected_audience: str) -> Principal:
        try:
            pid = raw_assertion.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValidationFailure("identity assertion is not text") from exc
        principal = self._principals.get(pid)
        if principal is None:
            raise Forbidden("unknown principal")
        if principal.expires_at is not None and _epoch_now() > principal.expires_at:
            raise Forbidden("identity assertion expired")
        return principal


@dataclass(frozen=True)
class AuthorizationRequest:
    """An untrusted network request asking to invoke a command."""

    raw_assertion: bytes
    idempotency_key: StableId
    expected_version: int | None
    reason: str
    program_id: StableId | None = None
    session_id: StableId | None = None


class Authorizer:
    """Derives a trusted CommandContext from verified claims, fail-closed.

    Rules:
      - Actor kind comes only from the verified Principal, never the request.
      - Roles come only from the verified Principal.
      - Program scope: a program-scoped human may act only within an allow-listed
        origin; a service identity with a program grants scoped access.
      - MFA is required for human protected-path roles (approver, policy admin).
      - An assertion cannot elevate beyond the configured identity provider.
    """

    # Human roles that require confirmed MFA before any command is derived.
    MFA_REQUIRED_ROLES = frozenset(
        {"approver", "policy_admin", "program_owner", "foundry_commander"}
    )

    def __init__(
        self,
        identity_provider: IdentityProvider,
        expected_audience: str = "eigen-foundry-control-plane",
    ) -> None:
        self._identity = identity_provider
        self._audience = expected_audience

    def authorize(self, request: AuthorizationRequest) -> CommandContext:
        principal = self._identity.verify(request.raw_assertion, self._audience)
        if principal.kind is ActorKind.HUMAN and not principal.mfa_verified:
            if principal.roles.intersection(self.MFA_REQUIRED_ROLES):
                raise Forbidden(
                    "human protected role requires confirmed MFA",
                    roles=sorted(principal.roles.intersection(self.MFA_REQUIRED_ROLES)),
                )
        # Program scope enforcement.
        if request.program_id is not None:
            scope = principal.allows_origin
            if scope is not None and request.program_id not in scope:
                raise Forbidden(
                    "principal is not scoped to this program",
                    program_id=request.program_id,
                )
        return CommandContext(
            actor_id=principal.principal_id,
            actor_kind=principal.kind,
            idempotency_key=request.idempotency_key,
            expected_version=request.expected_version,
            reason=request.reason,
            principal_roles=principal.roles,
        )


def _epoch_now() -> int:
    import time

    return int(time.time())
