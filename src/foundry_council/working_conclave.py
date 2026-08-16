"""M4 — Working Conclave orchestrator.

Ties the SeatRuntime layer to the governed council orchestration: a full F0
council session runs where each case-captain seat is executed through a
SeatRuntime (versioned run identity + bounded tool envelope + structured-output
validation) and its validated outputs feed the existing CouncilService.

This reproduces the synthetic F0 flow with live seat execution and introduces NO
new authority over the council kernel: seat outputs are structured proposals that
the governed service still validates, rejects-or-accepts, and commits only through
the existing restricted commit path. The orchestrator itself never changes formal
Program state.

Typical use::

    conclave = WorkingConclave(sqlite_path="working.db")
    trace = conclave.run(seed=7)
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from .ledger import SQLiteLedger
from .models import (
    ActorKind,
    CaseOpinion,
    CaseStatus,
    CaseType,
    Claim,
    ClaimState,
    CouncilSession,
    DecisionCharter,
    Disposition,
    EntryPoint,
    EvidenceManifest,
    FinalCaseAssessment,
    Materiality,
    ParticipantAssignment,
    ProgramPointers,
    ProgramRecord,
    ProgramStage,
    Route,
    SnapshotRef,
)
from .live_seat_model import default_seat_model_factory
from .policy import DEFAULT_GATE_POLICY, canonical_digest
from .seat_runtime import LiteralSeatModel, SeatRuntime, bind_seat
from .service import CouncilService


def _sha(label: str) -> str:
    return f"sha256:{hashlib.sha256(label.encode()).hexdigest()}"


def _snap(object_id: str, version: int = 1) -> SnapshotRef:
    return SnapshotRef(object_id=object_id, version=version, digest=_sha(object_id))


def _future(*, days: int) -> datetime:
    return datetime.now(timezone.utc) + timedelta(days=days)


def _command(actor_id: str, key: str, version: int | None = None, *, roles=frozenset()) -> "CR":
    from .service import CommandContext

    return CommandContext(
        actor_id=actor_id,
        actor_kind=ActorKind.AGENT,
        idempotency_key=key,
        expected_version=version,
        reason="Working Conclave deterministic run (seat-executed, no authority added).",
        principal_roles=roles,
    )


def _roster(cases: tuple[CaseType, ...]) -> tuple[ParticipantAssignment, ...]:
    base = [
        ParticipantAssignment(
            assignment_id="seat-commander",
            actor_id="agent-commander",
            actor_kind=ActorKind.AGENT,
            role="foundry_commander",
            run_id="run-commander",
            model_version="model-commander",
            prompt_version="prompt-commander",
            independence_group="group-commander",
        ),
        ParticipantAssignment(
            assignment_id="seat-evidence",
            actor_id="agent-evidence",
            actor_kind=ActorKind.AGENT,
            role="evidence_steward",
            run_id="run-evidence",
            model_version="model-evidence",
            prompt_version="prompt-evidence",
            independence_group="group-evidence",
        ),
        ParticipantAssignment(
            assignment_id="seat-architect",
            actor_id="agent-architect",
            actor_kind=ActorKind.AGENT,
            role="program_architect",
            run_id="run-architect",
            model_version="model-architect",
            prompt_version="prompt-architect",
            independence_group="group-architect",
        ),
        ParticipantAssignment(
            assignment_id="seat-red-team",
            actor_id="agent-red-team",
            actor_kind=ActorKind.AGENT,
            role="independent_red_team",
            run_id="run-red-team",
            model_version="model-red-team",
            prompt_version="prompt-red-team",
            independence_group="group-red-team",
        ),
        ParticipantAssignment(
            assignment_id="seat-arbiter",
            actor_id="policy-engine",
            actor_kind=ActorKind.SERVICE,
            role="policy_arbiter",
            run_id="run-policy",
            model_version=None,
            prompt_version=None,
            independence_group="group-policy",
        ),
        ParticipantAssignment(
            assignment_id="seat-reviewer",
            actor_id="agent-reviewer",
            actor_kind=ActorKind.AGENT,
            role="independent_reviewer",
            run_id="run-reviewer",
            model_version="model-reviewer",
            prompt_version="prompt-reviewer",
            independence_group="group-reviewer",
        ),
    ]
    for case in cases:
        suffix = case.value.lower()
        base.append(
            ParticipantAssignment(
                assignment_id=f"seat-{suffix}",
                actor_id=f"captain-{suffix}",
                actor_kind=ActorKind.AGENT,
                role="case_captain",
                case=case,
                run_id=f"run-{suffix}",
                model_version=f"model-{suffix}",
                prompt_version=f"prompt-{suffix}",
                independence_group=f"group-{suffix}",
            )
        )
    return tuple(base)


@dataclass
class WorkingConclaveTrace:
    program_id: str
    session_id: str
    seed: int
    seat_outputs: list[dict[str, Any]] = field(default_factory=list)
    phases: list[str] = field(default_factory=list)
    audit_chains_valid: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "program_id": self.program_id,
            "session_id": self.session_id,
            "seed": self.seed,
            "seat_outputs": self.seat_outputs,
            "phases": self.phases,
            "audit_chains_valid": self.audit_chains_valid,
            "harness_only": True,
        }


class WorkingConclave:
    """Runs a deterministic F0 council session with seat-executed outputs.

    Seat models are injectable: ``seat_model_factory(assignment, template)``
    returns the ``LiteralSeatModel`` for a case-captain seat. By default the
    factory is environment-driven (``FOUNDRY_SEAT_MODEL=live`` binds Kimi K2.5
    via :class:`~.live_seat_model.LiveSeatModel`; the default is the
    deterministic mock so CI stays hermetic and network-free).
    """

    def __init__(
        self,
        sqlite_path: str | Path = "working-conclave.db",
        *,
        seed: int = 7,
        cases: tuple[CaseType, ...] | None = None,
        seat_model_factory: Callable[[ParticipantAssignment, dict[str, Any]], LiteralSeatModel] | None = None,
    ) -> None:
        self.ledger = SQLiteLedger(sqlite_path)
        self.service = CouncilService(self.ledger)
        self.cases = cases or tuple(CaseType)
        self.seed = seed
        self.seat_model_factory = seat_model_factory or default_seat_model_factory

    def _captain_model_template(self, case: CaseType) -> dict[str, Any]:
        suffix = case.value.lower()
        return {
            "claim_id": f"wc-claim-{suffix}",
            "statement": f"Synthetic working-conclave claim for {case.value} (validated structured output).",
            "state": "SUPPORTED_INFERENCE",
            "materiality": "MATERIAL",
            "evidence_refs": ("wc-evidence-item",),
            "context": f"Frozen evidence for {case.value}.",
            "gate_impact": f"Controls the {case.value} case determination.",
            "proposed_falsifier": f"Synthetic falsifier for {case.value}.",
        }

    def _captain_run(self, assignment: ParticipantAssignment) -> SeatRuntime:
        template = self._captain_model_template(assignment.case)
        model = self.seat_model_factory(assignment, template)
        return SeatRuntime(bind_seat(assignment, seed=self.seed), model)

    def run(self) -> WorkingConclaveTrace:
        program, session = self._create_program_and_session()
        trace = WorkingConclaveTrace(
            program_id=program.program_id,
            session_id=session.session_id,
            seed=self.seed,
        )

        # Freeze evidence
        evidence_item = _snap(f"wc-evidence-item")
        evidence = EvidenceManifest(
            snapshot=SnapshotRef(
                object_id=f"wc-evidence-{session.session_id}",
                version=1,
                digest=canonical_digest({"items": [evidence_item.model_dump(mode="json")]}),
            ),
            items=(evidence_item,),
        )
        session = self.service.freeze_evidence(
            session.session_id,
            evidence,
            _command("agent-evidence", f"wc-evidence-{self.seed}", session.state_version),
        )
        trace.phases.append("freeze_evidence")

        # Blind round: each case-captain seat produces a validated claim + opinion.
        session = self.service.start_blind_round(
            session.session_id, _command("agent-commander", f"wc-blind-{self.seed}", session.state_version)
        )
        expected_version = session.state_version
        seat_outputs: list[dict[str, Any]] = []
        claim_by_case: dict[CaseType, str] = {}
        for case in self.cases:
            assignment = _assignment_for(session, case)
            runtime = self._captain_run(assignment)
            # 1. Seat produces a validated claim via produce() (structured-output check).
            out = runtime.produce(
                f"produce a claim for {case.value}",
                {},
                expected_kind="claim",
                required_fields=("claim_id", "statement", "state", "materiality", "context", "gate_impact"),
                seed=self.seed,
            )
            content = out.content
            claim = Claim(
                claim_id=content["claim_id"],
                owner_agent_id=assignment.actor_id,
                statement=content["statement"],
                state=ClaimState(content["state"]),
                materiality=Materiality(content["materiality"]),
                evidence_refs=(evidence_item,),
                context=content["context"],
                gate_impact=content["gate_impact"],
                proposed_falsifier=content.get("proposed_falsifier"),
            )
            claim_by_case[case] = claim.claim_id
            opinion = CaseOpinion(
                opinion_id=f"wc-opinion-{case.value.lower()}",
                case=case,
                captain_agent_id=assignment.actor_id,
                status=CaseStatus.PASS,
                rationale=f"Working-conclave validated opinion for {case.value} (deterministic).",
                claim_ids=(claim.claim_id,),
            )
            # 2. Submit through the governed service using the seat's own identity.
            receipt = self.service.submit_blind_opinion(
                session.session_id,
                opinion,
                (claim,),
                runtime.command(
                    expected_version=expected_version,
                    idempotency_key=f"wc-opinion-{self.seed}-" + assignment.assignment_id,
                ),
            )
            seat_outputs.append(
                {
                    "seat": assignment.assignment_id,
                    "run_id": out.run_id,
                    "model_version": out.model_version,
                    "kind": out.kind,
                    "claim_id": claim.claim_id,
                    "version": receipt.state_version,
                }
            )
            expected_version = receipt.state_version
        trace.seat_outputs = seat_outputs
        trace.phases.append("blind_round_complete")

        # Re-fetch authoritative session, then challenge round (open+close).
        session = self.ledger.get_session(session.session_id)
        session = self.service.reveal_claims(
            session.session_id, _command("agent-commander", f"wc-reveal-{self.seed}", session.state_version)
        )
        session = self.service.open_challenges(
            session.session_id, _command("agent-commander", f"wc-challenge-open-{self.seed}", session.state_version)
        )
        session = self.service.close_challenges(
            session.session_id, _command("agent-commander", f"wc-challenge-close-{self.seed}", session.state_version)
        )
        trace.phases.append("challenge_round_complete")

        # Red team + final cases (seat-produced case determinations).
        session = self.service.start_red_team(
            session.session_id, _command("agent-commander", f"wc-red-{self.seed}", session.state_version)
        )
        session = self.service.submit_red_team(
            session.session_id,
            _red_report(session.session_id),
            _command("agent-red-team", f"wc-red-submit-{self.seed}", session.state_version),
        )
        session = self.service.open_final_cases(
            session.session_id, _command("agent-commander", f"wc-final-open-{self.seed}", session.state_version)
        )
        for case in self.cases:
            assignment = _assignment_for(session, case)
            runtime = self._captain_run(assignment)
            earlier_claim_id = claim_by_case.get(case)
            final_prompt = (
                f"finalize case [{case.value}] by returning ONLY the claim_id of the "
                "claim your seat already submitted in the blind round for this case. "
                "Reuse that exact claim_id string verbatim; do not invent a new one. "
            )
            if earlier_claim_id:
                final_prompt += (
                    f"Your seat's blind-round claim_id for {case.value} was: "
                    f"{earlier_claim_id}. Return exactly that value as the claim_id. "
                )
            out = runtime.produce(
                final_prompt,
                {},
                expected_kind="final_case",
                required_fields=("claim_id",),
                seed=self.seed,
            )
            assessment = FinalCaseAssessment(
                assessment_id=f"wc-final-{case.value.lower()}",
                case=case,
                captain_agent_id=assignment.actor_id,
                status=CaseStatus.PASS,
                rationale=f"Working-conclave validated determination for {case.value} (deterministic).",
                claim_ids=(out.content["claim_id"],),
            )
            session = self.service.submit_final_case(
                session.session_id,
                assessment,
                runtime.command(
                    expected_version=session.state_version,
                    idempotency_key=f"wc-final-{self.seed}-" + assignment.assignment_id,
                ),
            )
        trace.phases.append("final_cases_complete")

        # Verify audit chains across the whole seat-executed run (harness-only).
        program_ok = self.ledger.verify_audit_chain("PROGRAM", program.program_id)
        session_ok = self.ledger.verify_audit_chain("COUNCIL_SESSION", session.session_id)
        trace.audit_chains_valid = program_ok and session_ok
        return trace

    def _create_program_and_session(self) -> tuple[ProgramRecord, CouncilSession]:
        program_id = f"WC-HARNESS-{self.seed}"
        session_id = f"session-wc-{self.seed}"
        pointers = ProgramPointers(
            portfolio_mandate=_snap("portfolio-mandate-v0"),
            tpp=_snap("tpp-v0"),
            rights_snapshot=_snap("rights-v0"),
            budget=_snap("budget-v0"),
            risk_register=_snap("risk-v0"),
            standard_of_care=_snap("soc-v0"),
            gate_policy=DEFAULT_GATE_POLICY.snapshot_ref(),
        )
        program = self.service.create_program_draft(
            program_id=program_id,
            title=f"Working Conclave dry run (seed {self.seed})",
            entry_point=EntryPoint.DISEASE_FIRST,
            route=Route.UNSELECTED,
            owner="owner-harness",
            pointers=pointers,
            context=_command("agent-drafter", f"wc-program-{self.seed}", roles=frozenset({"program_drafter"})),
        )
        program_ref = SnapshotRef(
            object_id=program.program_id,
            version=program.state_version,
            digest=canonical_digest(program),
        )
        charter = DecisionCharter(
            question="Should this synthetic F0 mandate be authorized for F1 mapping?",
            proposed_action="Advance the synthetic Program from F0 to F1.",
            exact_scope="Authorize F1 mapping only in a working-conclave dry run; no real spend/outreach.",
            requested_disposition=Disposition.ADVANCE,
            current_stage=ProgramStage.F0,
            proposed_stage=ProgramStage.F1,
            expected_program_state_version=program.state_version,
            program_snapshot=program_ref,
            portfolio_mandate=pointers.portfolio_mandate,
            tpp=pointers.tpp,
            rights=pointers.rights_snapshot,
            budget=pointers.budget,
            risk_register=pointers.risk_register,
            standard_of_care=pointers.standard_of_care,
            gate_policy=pointers.gate_policy,
            session_deadline=_future(days=7),
        )
        session = self.service.create_session(
            session_id=session_id,
            program_id=program.program_id,
            charter=charter,
            participants=_roster(tuple(CaseType)),
            context=_command("agent-commander", f"wc-session-{self.seed}"),
        )
        return program, session


def _assignment_for(session: CouncilSession, case: CaseType) -> ParticipantAssignment:
    for p in session.participants:
        if p.role == "case_captain" and p.case is case:
            return p
    raise KeyError(case)


def _red_report(session_id: str):
    from .models import RedTeamReport

    return RedTeamReport(
        report_id=f"report-{session_id}",
        reviewer_agent_id="agent-red-team",
        findings=(),
        conclusion="Synthetic working-conclave red team: no material contradiction within scope.",
    )
