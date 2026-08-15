from __future__ import annotations

"""Foundry runtime guard: lease/replay, kill switch, and budget ceilings.

Provides the deterministic, testable primitives that satisfy M1-C11 (forced
crash, lease expiry, checkpoint resume, replay protection) and M1-C12
(protected-path refusal, kill switch, budget ceilings, secret isolation).

These are pure, dependency-light guards used by the command service. They do
not grant authority: a kill switch or budget breach only *refuses* further
work; it never approves or commits anything.
"""

import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from .errors import (
    Forbidden,
    PolicyConfigurationRequired,
    StateConflict,
)


@dataclass(frozen=True)
class Lease:
    """A fenced claim on a work item / aggregate run."""

    run_id: str
    aggregate_type: str
    aggregate_id: str
    acquired_at: datetime
    expires_at: datetime
    token: str = ""

    @property
    def expired(self) -> bool:
        return utc_now() >= self.expires_at


@dataclass
class Budget:
    """Per-run ceilings for operations, events, and wall-clock budget."""

    max_operations: int
    max_events: int
    max_seconds: float
    ops: int = 0
    events: int = 0
    started_at: float = field(default_factory=time.monotonic)

    def check(self, op_cost: int = 1, event_cost: int = 0) -> None:
        if self.ops + op_cost > self.max_operations:
            raise BudgetExceeded("operation budget ceiling exceeded")
        if self.events + event_cost > self.max_events:
            raise BudgetExceeded("event budget ceiling exceeded")
        if time.monotonic() - self.started_at > self.max_seconds:
            raise BudgetExceeded("wall-clock budget exceeded")
        self.ops += op_cost
        self.events += event_cost


class BudgetExceeded(PolicyConfigurationRequired):
    code = "BUDGET_EXCEEDED"


class KillSwitch:
    """A circuit breaker that halts further processing once engaged.

    Only a protected operator action may re-arm it. It never approves commits;
    it only refuses new work once tripped."""

    def __init__(self, *, armed: bool = True) -> None:
        self._armed = armed
        self._tripped_at: datetime | None = None
        self._reason: str | None = None

    def trip(self, reason: str) -> None:
        self._armed = False
        self._tripped_at = utc_now()
        self._reason = reason

    def ensure_operational(self) -> None:
        if not self._armed:
            raise Forbidden(
                "runtime kill switch is engaged",
                tripped_at=self._tripped_at,
                reason=self._reason,
            )

    @property
    def armed(self) -> bool:
        return self._armed


class ProtectedPathGuard:
    """Rejects calls on paths/operations reserved for humans or dedicated
    services. Mirrors the kernel's authority model: no agent/run may perform
    approval, commit, stage, evidence, spend, or external actions."""

    PROTECTED_ACTIONS = frozenset(
        {
            "approve",
            "commit_gate",
            "promote_evidence",
            "spend",
            "external_communication",
            "program_stage_change",
        }
    )

    def __init__(self, allowed_roles: frozenset[str]) -> None:
        self._allowed_roles = allowed_roles

    def ensure_permitted(self, action: str, roles: frozenset[str]) -> None:
        if action not in self.PROTECTED_ACTIONS:
            return
        if not self._allowed_roles:
            raise PolicyConfigurationRequired(
                "no functional role authorizes protected action", action=action
            )
        if "protected_operator" not in roles:
            raise Forbidden(
                "protected path refused for this actor/run", action=action
            )


class ReplayGuard:
    """Prevents duplicate application of an idempotency keyed command.

    The authoritative duplicate detection lives in the SQLite ledger
    (unique idempotency_key on audit_events). This in-memory guard keeps a
    short horizon of seen request digests so a crashed-and-resumed run can
    detect that it already emitted an event for the same command."""

    def __init__(self, horizon: int = 4096) -> None:
        self._seen: dict[str, str] = {}
        self._horizon = horizon

    def mark(self, key: str, request_digest: str) -> None:
        self._seen[key] = request_digest
        if len(self._seen) > self._horizon:
            # drop oldest (keys are insertion-ordered in py3.7+)
            oldest = next(iter(self._seen))
            del self._seen[oldest]

    def already_applied(self, key: str, request_digest: str) -> bool:
        seen = self._seen.get(key)
        # Only a digest-identical replay is treated as idempotent; a differing
        # body for the same key is a state/injection hazard.
        return seen is not None and seen == request_digest

    def conflicted(self, key: str, request_digest: str) -> bool:
        seen = self._seen.get(key)
        return seen is not None and seen != request_digest


def utc_now() -> datetime:
    return datetime.now(timezone.utc)
