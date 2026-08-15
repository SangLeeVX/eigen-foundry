"""M5 — crash-safe resume between approval and commit.

Implements M5 step 17: force a crash between human approval and atomic commit,
then resume WITHOUT duplicate state.

Approach: the ledger + approval console are already idempotent — re-invoking
commit_gate_decision after the commit applies returns the existing committed
Program/Session (no duplicate event, no double state change). This module
provides the deterministic recovery plan an operator/loop uses to decide, on
resume, exactly what to do for a session that was mid-commit:

  - SESSION_COMMITTED      -> already applied; re-drive is a safe idempotent no-op.
  - READY_TO_COMMIT        -> full authenticated quorum present; safe to re-invoke
                              commit_gate_decision (idempotent on replay).
  - BLOCKED_MISSING_APPROV -> some functional sign-off is still missing; do not commit.
  - NOT_AT_COMMIT_BOUNDARY -> session is not waiting on a human-gated commit.

It never changes state itself; it only reports the recovery action so the
caller (operator console / loop) can re-drive the SAME restricted commit path.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .models import ApprovalDecision, SessionPhase
from .policy import required_approver_roles


RecoveryKind = Literal[
    "SESSION_COMMITTED",
    "READY_TO_COMMIT",
    "BLOCKED_MISSING_APPROV",
    "NOT_AT_COMMIT_BOUNDARY",
]


@dataclass(frozen=True)
class RecoveryPlan:
    kind: RecoveryKind
    message: str
    missing_roles: tuple[str, ...] = ()


class CrashRecovery:
    """Deterministic recovery decision for a council session near a commit."""

    def __init__(self, ledger) -> None:
        self.ledger = ledger

    def plan(self, session_id: str) -> RecoveryPlan:
        try:
            session = self.ledger.get_session(session_id)
        except Exception:  # noqa: BLE001 - absent session
            return RecoveryPlan("NOT_AT_COMMIT_BOUNDARY", f"session '{session_id}' not found")

        program = self.ledger.get_program(session.program_id)
        if session.phase is SessionPhase.COMMITTED:
            return RecoveryPlan(
                "SESSION_COMMITTED",
                f"session already committed (program revision {program.state_version}); "
                "re-drive is an idempotent no-op.",
            )

        if session.phase is not SessionPhase.AWAITING_HUMAN_APPROVAL:
            return RecoveryPlan(
                "NOT_AT_COMMIT_BOUNDARY",
                f"session is in '{session.phase.value}'; no human-gated commit is pending.",
            )

        required = required_approver_roles(session)
        existing = {
            approval.role
            for approval in self.ledger.get_approvals(session_id)
            if approval.decision is ApprovalDecision.APPROVED
        }
        missing = tuple(r for r in required if r not in existing)
        if missing:
            return RecoveryPlan(
                "BLOCKED_MISSING_APPROV",
                "commit is blocked pending human sign-off for required roles.",
                missing_roles=missing,
            )
        return RecoveryPlan(
            "READY_TO_COMMIT",
            "full authenticated quorum present; safe to re-invoke commit_gate_decision "
            "(idempotent on replay).",
        )
