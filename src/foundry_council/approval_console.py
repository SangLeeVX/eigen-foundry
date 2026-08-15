"""M4 — authenticated human approval/commit console.

An operator-facing surface for M4's exit criterion that **exact authenticated
approvals gate atomic commit**. It:

  1. Lists pending approval requests (council sessions sitting in
     AWAITING_HUMAN_APPROVAL with unsatisfied functional sign-offs).
  2. Lets an authenticated, MFA-verified human record an approval, using the
     Authorizer to derive a trusted CommandContext from the human's identity
     claims (never trusting a request body to self-assert roles or MFA).
  3. Triggers the atomic commit ONLY through the existing restricted commit path
     (commit_gate_decision), which re-checks the full human approval quorum,
     packet digest, and policy before any formal Program state changes.

This console introduces NO new authority: every action still runs through the
governed CouncilService invariants (authenticated human approval, quorum,
idempotent replay). It is a read + limited-write operator surface, not a bypass.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .errors import ApprovalRequired
from .identity import Authorizer, AuthorizationRequest
from .ledger_protocol import Ledger
from .models import (
    ActorKind,
    Approval,
    ApprovalDecision,
    SessionPhase,
)
from .policy import required_approver_roles
from .service import CommandContext, CouncilService


@dataclass
class PendingApproval:
    session_id: str
    program_id: str
    phase: str
    request_id: str
    required_roles: tuple[str, ...]
    missing_roles: tuple[str, ...]
    expires_at: str | None = None


@dataclass
class ConsoleResult:
    ok: bool
    message: str
    detail: dict[str, Any] = field(default_factory=dict)


class ApprovalConsole:
    """Authenticated operator surface over a CouncilService + Authorizer."""

    def __init__(
        self,
        service: CouncilService,
        authorizer: Authorizer,
        *,
        ledger: Ledger | None = None,
        pending_session_ids: tuple[str, ...] = (),
    ) -> None:
        self.service = service
        self.authorizer = authorizer
        self.ledger = ledger or service.ledger
        # Operator supplies the set of sessions eligible for display (a console
        # does not enumerate arbitrary sessions on its own authority).
        self._pending_session_ids = pending_session_ids

    # -- read surface -------------------------------------------------------

    def list_pending(self) -> list[PendingApproval]:
        """Return approval requests awaiting human sign-off with missing roles."""
        out: list[PendingApproval] = []
        for session_id in self._pending_session_ids:
            try:
                session = self.ledger.get_session(session_id)
            except Exception:  # noqa: BLE001 - skip unreadable/absent
                continue
            if session.phase is not SessionPhase.AWAITING_HUMAN_APPROVAL:
                continue
            request = session.approval_request
            if request is None:
                continue
            required = request.required_roles
            existing = {
                approval.role
                for approval in self.ledger.get_approvals(session_id)
                if approval.decision is ApprovalDecision.APPROVED
            }
            missing = tuple(r for r in required if r not in existing)
            out.append(
                PendingApproval(
                    session_id=session_id,
                    program_id=session.program_id,
                    phase=session.phase.value,
                    request_id=request.request_id,
                    required_roles=required,
                    missing_roles=missing,
                    expires_at=request.expires_at.isoformat(),
                )
            )
        return out

    # -- authenticated human approval ---------------------------------------

    def approve(
        self,
        *,
        session_id: str,
        approver_actor: str,
        role: str,
        raw_assertion: bytes,
        decision: ApprovalDecision = ApprovalDecision.APPROVED,
        reason: str = "Approved through the authenticated approval console.",
    ) -> ConsoleResult:
        """Record a single human approval, authenticated via the Authorizer.

        ``approver_actor`` selects the pre-registered Principal's identity; the
        Authorizer derives the trusted CommandContext (enforcing MFA + roles)
        from ``raw_assertion`` so no request body can self-assert authority.
        """
        ctx = self._derive_context(
            approver_actor=approver_actor,
            session_id=session_id,
            raw_assertion=raw_assertion,
            reason=reason,
        )
        session = self.ledger.get_session(session_id)
        query = required_approver_roles(session)
        if role not in query:
            return ConsoleResult(False, f"role '{role}' is not a required approver role")
        # Build the approval bound to the current review packet + request.
        request = session.approval_request
        packet = session.gate_packet
        if request is None or packet is None:
            return ConsoleResult(False, "session has no open approval request")
        import uuid

        approval = Approval(
            approval_id=f"appr-{uuid.uuid4().hex}",
            request_id=request.request_id,
            session_id=session_id,
            program_id=session.program_id,
            approver_id=ctx.actor_id,
            approver_kind=ActorKind.HUMAN,
            role=role,
            decision=decision,
            gate_packet_digest=packet.digest,
            rationale=reason,
        )
        try:
            self.service.record_approval(session_id, approval, ctx)
        except Exception as exc:  # noqa: BLE001 - surface governed errors
            return ConsoleResult(False, str(exc))
        return ConsoleResult(True, f"approval recorded for role '{role}'", {"actor": ctx.actor_id})

    def commit(
        self,
        *,
        session_id: str,
        approver_actor: str,
        raw_assertion: bytes,
        decision_id: str,
        reason: str = "Commit invoked through the authenticated approval console.",
    ) -> ConsoleResult:
        """Invoke the atomic commit through the restricted commit path.

        commit_gate_decision re-checks the FULL authenticated human quorum + the
        sealed packet digest + active gate policy before changing any formal
        Program state; it is the only path that can advance stage/status/route.
        """
        ctx = self._derive_context(
            approver_actor=approver_actor,
            session_id=session_id,
            raw_assertion=raw_assertion,
            reason=reason,
        )
        if "ledger_committer" not in ctx.principal_roles:
            return ConsoleResult(
                False,
                "principal lacks the 'ledger_committer' role required to commit",
            )
        try:
            program, session = self.service.commit_gate_decision(
                session_id, decision_id, ctx
            )
        except Exception as exc:  # noqa: BLE001 - surface governed errors
            return ConsoleResult(False, str(exc))
        return ConsoleResult(
            True,
            f"atomic commit performed (program revision {program.state_version})",
            {"program_stage": program.stage.value, "program_status": program.status.value},
        )

    # -- helpers ------------------------------------------------------------

    def _derive_context(
        self,
        *,
        approver_actor: str,
        session_id: str,
        raw_assertion: bytes,
        reason: str,
    ) -> CommandContext:
        session = self.ledger.get_session(session_id)
        request = AuthorizationRequest(
            raw_assertion=raw_assertion,
            idempotency_key=f"console:{approver_actor}:{session_id}",
            expected_version=session.state_version,
            reason=reason,
            program_id=session.program_id,
            session_id=session_id,
        )
        return self.authorizer.authorize(request)
