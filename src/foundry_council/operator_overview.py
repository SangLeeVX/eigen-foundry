"""M5 — operator overview (step 18).

Displays every state, blocker, decision, and next action from the durable
Foundry state so an operator can steer the closed loop. Read-only; never
changes formal Program state.

Aggregates, per program:
  - Program stage/status + latest gate decision.
  - Council sessions + phase.
  - Pending human approvals (from the approval console) with missing roles.
  - Crash-recovery recommendation for any session near a commit boundary.
  - Approved work orders, ingested results, attributions, learn-backs
    (from an injectable WorkOrderStore, if provided).
  - Candidate next action (human or automatic).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from .crash_recovery import CrashRecovery
from .ledger_protocol import Ledger
from .models import ApprovalDecision
from .work_order_service import WorkOrderStore


@dataclass(frozen=True)
class ProgramOverview:
    program_id: str
    stage: str
    status: str
    state_version: int
    sessions: list[dict[str, Any]]
    latest_decision: dict[str, Any] | None
    pending_approvals: list[dict[str, Any]]
    recovery: dict[str, Any]
    work_orders: list[dict[str, Any]]
    results: list[dict[str, Any]]
    attributions: list[dict[str, Any]]
    learn_backs: list[dict[str, Any]]
    next_actions: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class OperatorOverview:
    """Builds a full operator snapshot over reusable components (read-only)."""

    def __init__(
        self,
        ledger: Ledger,
        *,
        work_order_store: WorkOrderStore | None = None,
        pending_session_ids: tuple[str, ...] = (),
    ) -> None:
        self.ledger = ledger
        self.work_order_store = work_order_store
        self.recovery = CrashRecovery(ledger)
        self._pending_session_ids = pending_session_ids

    def overview(self) -> dict[str, Any]:
        programs = self.ledger.list_program_ids()
        out = []
        for pid in programs:
            out.append(self._program_overview(pid))
        return {
            "backend": type(self.ledger).__name__,
            "program_count": len(programs),
            "programs": out,
        }

    def _program_overview(self, program_id: str) -> dict[str, Any]:
        program = self.ledger.get_program(program_id)
        sessions = []
        session_ids = self.ledger.list_session_ids()
        pending = []
        recovery_by_session = {}
        for sid in session_ids:
            session = self.ledger.get_session(sid)
            if session.program_id != program_id:
                continue
            sessions.append(
                {
                    "session_id": sid,
                    "phase": session.phase.value,
                    "state_version": session.state_version,
                    "approvals": [
                        a.model_dump(mode="json")
                        for a in self.ledger.get_approvals(sid)
                        if a.decision is ApprovalDecision.APPROVED
                    ],
                }
            )
            recovery_by_session[sid] = self.recovery.plan(sid).__dict__
            # pending human approvals (missing roles)
            if (
                hasattr(session, "approval_request")
                and session.approval_request is not None
            ):
                required = session.approval_request.required_roles
                existing = {
                    a.role
                    for a in self.ledger.get_approvals(sid)
                    if a.decision is ApprovalDecision.APPROVED
                }
                missing = [r for r in required if r not in existing]
                if missing:
                    pending.append({"session_id": sid, "missing_roles": missing})

        latest_decision = None
        if program.last_gate_decision_id is not None:
            try:
                d = self.ledger.get_gate_decision(program.last_gate_decision_id)
                latest_decision = d.model_dump(mode="json")
            except Exception:  # noqa: BLE001
                latest_decision = {"decision_id": program.last_gate_decision_id, "error": "unresolvable"}

        wo = self.work_order_store
        work_orders = (
            [w.model_dump(mode="json") for w in wo.list_work_orders()]
            if wo and hasattr(wo, "list_work_orders")
            else []
        )
        results = (
            [r.model_dump(mode="json") for r in wo.list_results()]
            if wo and hasattr(wo, "list_results")
            else []
        )
        attributions = (
            [a.model_dump(mode="json") for a in wo.list_attributions()]
            if wo and hasattr(wo, "list_attributions")
            else []
        )
        learn_backs = (
            [l.model_dump(mode="json") for l in wo.list_learn_backs()]
            if wo and hasattr(wo, "list_learn_backs")
            else []
        )

        next_actions = []
        for sid, plan in recovery_by_session.items():
            if plan["kind"] == "READY_TO_COMMIT":
                next_actions.append(f"commit pending session {sid} (idempotent)")
            elif plan["kind"] == "BLOCKED_MISSING_APPROV":
                next_actions.append(
                    f"request approvals for session {sid}: {', '.join(plan['missing_roles']) or 'all roles'}"
                )
        if pending:
            next_actions.append("dispatch approval requests to missing humans")
        if not next_actions:
            next_actions.append("no pending operator action")

        return ProgramOverview(
            program_id=program_id,
            stage=program.stage.value,
            status=program.status.value,
            state_version=program.state_version,
            sessions=sessions,
            latest_decision=latest_decision,
            pending_approvals=pending,
            recovery=recovery_by_session,
            work_orders=work_orders,
            results=results,
            attributions=attributions,
            learn_backs=learn_backs,
            next_actions=next_actions,
        ).to_dict()
